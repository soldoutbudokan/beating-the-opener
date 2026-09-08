"""Causal state and probability checks for the separate WNBA recipe."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import math
import unittest

import numpy as np

from research.engine import model as original
from research.engine.store import ForecastRequest, Observation, ObservationKind, TimeBasis, payload_digest
from research.wnba_v2 import model
from research.wnba_v2.comparison import independent_state, decision, freeze_scalars, rate_checkpoint

UTC = timezone.utc


def base_recipe():
    p = {"schema": original.RECIPE_VERSION, "through_season": 2022, "variant": "box", "shrinkage": 1,
         "priors": {"rates": {"all": [.2, .1, .05, .02], "G": [.3, .08, .08, .03]},
                    "rate_rvar": [.4, .2, .1, .05], "minutes_mean": 20., "minutes_variance": 25.,
                    "p_dnp": .1, "pace": 2.},
         "count_fano": [2., 2., 1.5, 1.2], "feature_center": [0.] * 11, "feature_scale": [1.] * 11,
         "minutes_coefficients": [20.] + [0.] * 10, "dnp_coefficients": [-2.] + [0.] * 10,
         "role_variance": [25.] * 4}
    p["recipe_hash"] = payload_digest(p)
    return p


def recipe(mode="constant", total=2., noise=1.5):
    return model.configuration(base_recipe(), {m: {"F_total": total, "F_noise": noise} for m in model.MARKETS}, mode)


def observation(game, day=1, year=2022, status="played", minutes=20., counts=None):
    stamp = datetime(year, 6, day, 20, tzinfo=UTC)
    measured = status == "played" and minutes is not None and minutes > 0
    counts = dict(counts) if counts is not None else {m: 8 if status == "played" else 0 for m in model.MARKETS}
    p = {"record_type": "player_box", "participation": status,
         "did_not_play": False if status == "played" else True if status == "dnp" else None,
         "exposure_status": "observed_positive" if measured else "not_applicable" if status == "dnp" else "unknown",
         "minute_measurement_eligible": measured,
         "rate_measurement_eligible": measured and all(v is not None for v in counts.values()),
         "minutes": minutes, "counts": counts, "starter": True, "position": "G",
         "team_id": "t1", "opponent_id": "t2", "duration_minutes": 40., "possessions_estimate": 80.}
    return Observation("fixture", "box:" + game, "p1", game, year, ObservationKind.HISTORICAL_OUTCOME,
                       stamp, p, time_basis=TimeBasis.ASSUMED, assumed_available_at=stamp + timedelta(hours=8),
                       time_note="Synthetic historical clock")


def request(year=2023):
    tip = datetime(year, 6, 20, 20, tzinfo=UTC)
    return ForecastRequest("p1", "target", "t1", "t2", year, tip, tip - timedelta(hours=24))


class WnbaV2ModelTests(unittest.TestCase):
    def test_unmeasured_appearance_preserves_participation_without_numeric_update(self):
        engine = model.ParticipationModel([], recipe())
        before = engine._new_state()
        state = deepcopy(before)
        row = observation("short", minutes=None, counts={m: 1 for m in model.MARKETS})
        engine._update(state, row)
        self.assertEqual((state["played"], state["minute_measured"], state["unmeasured_played"]), (1, 0, 1))
        self.assertEqual(state["dnp"], .82 * before["dnp"])
        self.assertEqual(state["fast"], before["fast"])
        self.assertEqual(state["season_n"], 0)
        np.testing.assert_array_equal(state["rates"], base_recipe()["priors"]["rates"]["G"])
        np.testing.assert_allclose(state["variance"], before["variance"] + .0003 * np.array(base_recipe()["priors"]["rate_rvar"]))

    def test_missing_counts_retain_valid_minute_measurement(self):
        engine = model.ParticipationModel([], recipe())
        state = engine._new_state()
        row = observation("missing-count", minutes=35., counts={m: None for m in model.MARKETS})
        engine._update(state, row)
        self.assertEqual(state["minute_measured"], 1)
        self.assertEqual(state["fast"], .18 * 35 + .82 * 20)
        np.testing.assert_array_equal(state["rates"], base_recipe()["priors"]["rates"]["G"])

    def test_unknown_participation_is_not_a_dnp_measurement(self):
        engine = model.ParticipationModel([], recipe())
        state = engine._new_state()
        before = deepcopy(state)
        engine._update(state, observation("unknown", status="unknown", minutes=None))
        for key in ("dnp", "starter", "played", "minute_measured", "fast", "slow"):
            self.assertEqual(state[key], before[key])
        np.testing.assert_array_equal(state["variance"], before["variance"])
        self.assertEqual((state["n"], state["unknown_participation"]), (1, 1))

    def test_independent_scalar_state_matches_all_repaired_cases_and_season_transition(self):
        rows = [observation("first", year=2021), observation("short", day=2, minutes=None),
                observation("dnp", day=3, status="dnp", minutes=0),
                observation("unknown", day=4, status="unknown", minutes=None),
                observation("missing-count", day=5, minutes=35, counts={m: None for m in model.MARKETS}),
                observation("valid", day=6, minutes=25)]
        engine = model.ParticipationModel(rows, recipe())
        actual = engine._player_state("p1", rows)
        expected = independent_state(rows, base_recipe())
        for key, value in expected.items():
            if isinstance(value, list):
                np.testing.assert_allclose(actual[key], value, rtol=1e-12)
            else:
                self.assertEqual(actual[key], value)

    def test_no_unmeasured_appearances_matches_original_state(self):
        rows = [observation("a"), observation("b", day=2, minutes=30), observation("dnp", day=3, status="dnp", minutes=0)]
        old = original.StructuralModel(rows, base_recipe())._player_state("p1", rows)
        new = model.ParticipationModel(rows, recipe())._player_state("p1", rows)
        for key, value in old.items():
            if isinstance(value, np.ndarray):
                np.testing.assert_array_equal(new[key], value)
            else:
                self.assertEqual(new[key], value)

    def test_target_future_and_other_player_outcomes_cannot_change_old_prediction(self):
        old = observation("old")
        target = replace(observation("target", year=2023, day=20), assumed_available_at=request().as_of - timedelta(hours=1))
        later = observation("future", year=2024, counts={m: 1000 for m in model.MARKETS})
        outsider = replace(observation("unrelated"), entity_id="other")
        first = model.ParticipationModel([old], recipe()).predict(request())
        second = model.ParticipationModel([later, target, outsider, old], recipe()).predict(request())
        self.assertEqual(first, second)

    def test_rate_change_affects_only_points_and_not_means_or_minutes(self):
        rows = [observation("old")]
        a = model.ParticipationModel(rows, recipe("constant")).predict(request())
        b = model.ParticipationModel(rows, recipe("rate")).predict(request())
        self.assertEqual(a["minutes_probs"], b["minutes_probs"])
        self.assertEqual(a["rate_mean"], b["rate_mean"])
        for market in model.MARKETS:
            self.assertEqual(a["count_components"][market]["means"], b["count_components"][market]["means"])
            if market == "points":
                self.assertNotEqual(a["count_components"][market]["variances"], b["count_components"][market]["variances"])
            else:
                self.assertEqual(a["count_components"][market], b["count_components"][market])

    def test_count_mass_and_half_integer_price_partitions(self):
        forecast = model.ParticipationModel([observation("old")], recipe("rate")).predict(request())
        for market in model.MARKETS:
            total = sum(model.count_probability(forecast, market, n) for n in range(500))
            self.assertAlmostEqual(total, 1., places=10)
            for line in (0, .5, 5, 15.5):
                p = model.price_forecast(forecast, market, line)
                self.assertAlmostEqual(p["p_under"] + p["p_push"] + p["p_over"], 1.)
                self.assertEqual(p["p_push"] == 0, not float(line).is_integer())

    def test_poisson_limit_and_log_score_stability(self):
        forecast = model.ParticipationModel([], recipe(total=1., noise=1.)).predict(request())
        for n in (0, 1, 30, 10000):
            logp = model.count_log_probability(forecast, "points", n)
            self.assertTrue(math.isfinite(logp) and logp <= 0)
        self.assertAlmostEqual(model.count_cdf(forecast, "points", 1000), 1.)

    def test_invalid_probabilities_recipes_and_dnp_contradictions_fail(self):
        forecast = model.ParticipationModel([], recipe()).predict(request())
        forecast["minutes_probs"][0] += .1
        with self.assertRaises(ValueError):
            model.price_forecast(forecast, "points", 10)
        altered = recipe()
        altered["variance_mode"] = "rate"
        with self.assertRaisesRegex(ValueError, "hash"):
            model.ParticipationModel([], altered)
        engine = model.ParticipationModel([], recipe())
        bad = observation("contradiction", status="dnp", minutes=0, counts={m: 1 for m in model.MARKETS})
        with self.assertRaisesRegex(ValueError, "contradicts"):
            engine._update(engine._new_state(), bad)

    def test_development_does_not_enable_protected_forecasts(self):
        with self.assertRaises(original.ProtectedDataError):
            model.ParticipationModel([], recipe()).predict(request(2026))

    def test_scalar_calibration_excludes_unmeasured_rows_and_is_not_a_refit(self):
        row = {"season": 2022, "actual_exposure": 10., "rate_mean": [.2] * 4,
               "rate_variance": [.03] * 4, "actual_counts": [6.] * 4,
               "dnp": False, "rate_measurement_eligible": True}
        missing = dict(row, actual_exposure=None, rate_measurement_eligible=False)
        s = freeze_scalars([row, missing], base_recipe())
        self.assertEqual(s["points"]["n"], 1)
        self.assertEqual(s["points"]["F_total"], 8.)
        self.assertEqual(s["points"]["F_noise"], 6.5)
        with self.assertRaisesRegex(ValueError, "check years"):
            freeze_scalars([dict(row, season=2023)], base_recipe())

    def test_count_gate_and_variance_gates_both_required(self):
        source = {"status": "PASS_UNDER_ASSUMPTION"}
        scalars = {"points": {"total_floor": False, "noise_floor": False}}
        point = {"aggregate_rate_uncertainty_share": .1, "rate_minus_constant_score": -.02,
                 "variances": {"rate": {"squared_error_to_variance": 1.}}}
        checks = {y: {"points": deepcopy(point)} for y in ("2023", "2024")}
        counts = {y: {"distributions_valid": True, "markets": {"points": {"rate_minus_constant_nll": -.006}}} for y in checks}
        self.assertEqual(decision(source, scalars, checks, counts)["selected_variance_mode"], "rate")
        counts["2023"]["markets"]["points"]["rate_minus_constant_nll"] = -.0049
        self.assertEqual(decision(source, scalars, checks, counts)["selected_variance_mode"], "constant")
        self.assertFalse(decision(source, scalars, checks, counts)["market_advantage_established"])
        counts["2023"]["distributions_valid"] = False
        self.assertIsNone(decision(source, scalars, checks, counts)["selected_variance_mode"])


if __name__ == "__main__":
    unittest.main()
