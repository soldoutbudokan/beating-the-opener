"""Separate future population never releases old protected source windows."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from research.engine.store import ForecastRequest, ProtectedDataError, TimeBasis
from research.wnba_v2.future import FutureOnlyModel, observation_record, observation_from_record, write_observations, load_observations
from tests.test_wnba_v2_model import observation, recipe

UTC = timezone.utc
FREEZE = datetime(2026, 9, 7, 23, tzinfo=UTC)


def request(as_of=None):
    return ForecastRequest("p1", "new-game", "t1", "t2", 2026,
                           FREEZE + timedelta(days=3), as_of or FREEZE + timedelta(days=2))


def observed_update(event="new-prior", effective=None, observed=None):
    base = observation(event, year=2024)
    return replace(base, season=2026, effective_at=effective or FREEZE + timedelta(hours=2),
                   observed_at=observed or FREEZE + timedelta(hours=8),
                   time_basis=TimeBasis.OBSERVED, assumed_available_at=None, time_note="")


def measured_update(event, tip_hours, received_hours, *, minutes=20., points=8):
    row = observed_update(event, FREEZE + timedelta(hours=tip_hours),
                          FREEZE + timedelta(hours=received_hours))
    payload = dict(row.payload, minutes=minutes, counts=dict(row.payload["counts"], points=points))
    return replace(row, payload=payload, payload_hash=None)


def forecast_distribution(forecast):
    return {key: forecast[key] for key in (
        "p_dnp", "minutes_probs", "count_components", "rate_mean", "rate_variance",
        "history_rows", "pace_possessions_per_minute")}


class FutureOnlyTests(unittest.TestCase):
    def test_seed_2025_can_forecast_after_freeze(self):
        engine = FutureOnlyModel([observation("seed", year=2025)], recipe(), frozen_at=FREEZE)
        forecast = engine.predict(request())
        self.assertEqual(forecast["history_rows"], 1)
        self.assertFalse(forecast["future_only_boundary"]["pre_freeze_2026_history_allowed"])

    def test_old_2026_window_rejected_even_with_new_observation_clock(self):
        old = observed_update(effective=FREEZE - timedelta(days=30))
        with self.assertRaisesRegex(ProtectedDataError, "Pre-freeze"):
            FutureOnlyModel([old], recipe(), frozen_at=FREEZE)

    def test_post_freeze_game_updates_only_after_observed_clock(self):
        row = observed_update()
        engine = FutureOnlyModel([row], recipe(), frozen_at=FREEZE)
        before = engine.predict(request(FREEZE + timedelta(hours=7)))
        after = engine.predict(request(FREEZE + timedelta(hours=9)))
        self.assertEqual((before["history_rows"], after["history_rows"]), (0, 1))

    def test_late_player_correction_rebuilds_game_order_without_changing_old_forecast(self):
        first = measured_update("first", 2, 8, minutes=10., points=4)
        second = measured_update("second", 26, 32, minutes=35., points=23)
        revised = measured_update("first", 2, 40, minutes=15., points=12)
        engine = FutureOnlyModel([first, second, revised], recipe(), frozen_at=FREEZE)
        baseline = FutureOnlyModel([first, second], recipe(), frozen_at=FREEZE)
        before = request(FREEZE + timedelta(hours=39))
        old_forecast = engine.predict(before)
        self.assertEqual(old_forecast, baseline.predict(before))
        boundary = request(revised.available_at)
        self.assertEqual(engine.predict(boundary), baseline.predict(boundary))

        # A reference with the corrected value available on time has the same
        # eventual measurement history, although its receipt manifest differs.
        on_time = replace(revised, observed_at=first.available_at)
        reference = FutureOnlyModel([on_time, second], recipe(), frozen_at=FREEZE)
        after = request(FREEZE + timedelta(hours=41))
        actual = engine.predict(after)
        expected = reference.predict(after)
        self.assertEqual(forecast_distribution(actual), forecast_distribution(expected))
        self.assertNotEqual(actual["rate_mean"], old_forecast["rate_mean"])
        self.assertEqual(engine._player_cache["p1"][1]["last_tip"], second.effective_at)
        # Reusing a cache built with the correction must also preserve replay.
        self.assertEqual(engine.predict(before), old_forecast)

    def test_late_first_receipt_uses_game_order(self):
        first = measured_update("first", 2, 40, minutes=10., points=4)
        second = measured_update("second", 26, 32, minutes=35., points=23)
        engine = FutureOnlyModel([second, first], recipe(), frozen_at=FREEZE)
        reference = FutureOnlyModel([replace(first, observed_at=FREEZE + timedelta(hours=8)), second],
                                    recipe(), frozen_at=FREEZE)
        self.assertEqual(engine.predict(request(FREEZE + timedelta(hours=39)))["history_rows"], 1)
        self.assertEqual(forecast_distribution(engine.predict(request())),
                         forecast_distribution(reference.predict(request())))

    def test_late_team_correction_preserves_recent_pace_and_roster(self):
        def team(event, tip_hours, received_hours, possessions, roster):
            row = measured_update(event, tip_hours, received_hours)
            return replace(row, entity_id="t1", record_id="team:" + event,
                           payload={"record_type": "team_box", "possessions_estimate": possessions,
                                    "duration_minutes": 40., "roster_count": roster}, payload_hash=None)

        first = team("first", 2, 8, 60., 10)
        second = team("second", 26, 32, 100., 12)
        revised = team("first", 2, 40, 80., 11)
        engine = FutureOnlyModel([first, second, revised], recipe(), frozen_at=FREEZE)
        reference = FutureOnlyModel([replace(revised, observed_at=first.available_at), second],
                                    recipe(), frozen_at=FREEZE)
        before = request(FREEZE + timedelta(hours=39))
        old_forecast = engine.predict(before)
        actual = engine.predict(request())
        self.assertEqual(forecast_distribution(actual), forecast_distribution(reference.predict(request())))
        # Corrected first-game pace equals the prior, then the recent game
        # supplies one update. The recent roster remains the current one.
        self.assertAlmostEqual(actual["pace_possessions_per_minute"], (2.06 + 2.) / 2.)
        self.assertEqual(engine._team_cache["t1"][1][1], 12.)
        self.assertEqual(engine.predict(before), old_forecast)

    def test_older_season_correction_cannot_roll_current_season_backward(self):
        first = measured_update("first", 2, 8, minutes=10., points=4)
        second_tip = datetime(2027, 5, 1, 20, tzinfo=UTC)
        second = replace(measured_update("second", 26, 32, minutes=35., points=23),
                         season=2027, effective_at=second_tip,
                         observed_at=second_tip + timedelta(hours=4))
        revised = replace(measured_update("first", 2, 40, minutes=15., points=12),
                          observed_at=second_tip + timedelta(hours=8))
        engine = FutureOnlyModel([first, second, revised], recipe(), frozen_at=FREEZE)
        target = ForecastRequest("p1", "new-game", "t1", "t2", 2027,
                                 second_tip + timedelta(days=3), second_tip + timedelta(days=2))
        engine.predict(target)
        state = engine._player_cache["p1"][1]
        self.assertEqual(state["season"], 2027)
        self.assertEqual(state["last_tip"], second_tip)
        self.assertEqual(state["prior_season"], 15.)

    def test_assumed_clock_or_backdated_completion_rejected(self):
        row = observed_update()
        assumed = replace(row, time_basis=TimeBasis.ASSUMED, observed_at=None,
                          assumed_available_at=FREEZE + timedelta(hours=8), time_note="assumed")
        for bad in (assumed, observed_update(observed=FREEZE + timedelta(hours=1))):
            with self.assertRaises(ValueError):
                FutureOnlyModel([bad], recipe(), frozen_at=FREEZE)

    def test_historical_replay_and_target_outcome_are_excluded(self):
        engine = FutureOnlyModel([observed_update(event="new-game")], recipe(), frozen_at=FREEZE)
        self.assertEqual(engine.predict(request())["history_rows"], 0)
        with self.assertRaisesRegex(ProtectedDataError, "pre-freeze"):
            engine.predict(request(FREEZE - timedelta(seconds=1)))

    def test_freeze_clock_must_be_aware_and_not_pre2026(self):
        for clock in (datetime(2026, 9, 7), datetime(2025, 9, 7, tzinfo=UTC)):
            with self.assertRaises(ValueError):
                FutureOnlyModel([], recipe(), frozen_at=clock)

    def test_private_seed_roundtrip_preserves_exact_hashes_and_predictions(self):
        rows = [observation("seed", year=2025), observed_update()]
        with tempfile.TemporaryDirectory() as directory:
            first, second = Path(directory) / "one.gz", Path(directory) / "two.gz"
            write_observations(first, rows)
            write_observations(second, reversed(rows))
            self.assertEqual(first.read_bytes(), second.read_bytes())
            restored = load_observations(first)
            a = FutureOnlyModel(rows, recipe(), frozen_at=FREEZE).predict(request())
            b = FutureOnlyModel(restored, recipe(), frozen_at=FREEZE).predict(request())
            self.assertEqual(a, b)
        altered = observation_record(rows[0])
        altered["payload"]["counts"]["points"] += 1
        with self.assertRaisesRegex(ValueError, "payload_hash"):
            observation_from_record(altered)


if __name__ == "__main__":
    unittest.main()
