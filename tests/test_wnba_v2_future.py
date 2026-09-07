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
