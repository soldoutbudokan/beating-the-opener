"""Synthetic checks for the registered market-free variance mechanism pilot."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from research.diagnostics.rate_uncertainty import (
    appearance_inventory, check_time, close, decision, denominator, exposure,
    freeze_scalars, independent_clocks, independent_state, innovations, rate_checkpoint,
    retained_assets, run_pilot, sample_games, scheduled_sides, source_check, variance_report, write, write_rows,
)
from research.engine import model, sources
from research.engine.store import ForecastRequest, Observation, ObservationKind, TimeBasis, payload_digest

UTC = timezone.utc


def recipe():
    r = {"schema": model.RECIPE_VERSION, "through_season": 2022, "shrinkage": 1, "variant": "box",
         "priors": {"rates": {"all": [.2] * 4, "G": [.3] * 4}, "rate_rvar": [.4] * 4,
                    "minutes_mean": 20., "minutes_variance": 25., "pace": 2., "p_dnp": .1},
         "count_fano": [2.] * 4}
    r["recipe_hash"] = payload_digest(r)
    return r


def observation(game, year=2022, day=1, minutes=20., counts=None, available=None):
    tip = datetime(year, 6, day, 20, tzinfo=UTC)
    return Observation("test", "box:" + game, "p1", game, year, ObservationKind.HISTORICAL_OUTCOME,
        tip, {"record_type": "player_box", "team_id": "t1", "opponent_id": "t2",
              "minutes": minutes, "did_not_play": minutes == 0, "starter": True, "position": "G",
              "duration_minutes": 40., "possessions_estimate": 80.,
              "counts": counts or {m: 0 if minutes == 0 else 8 for m in model.MARKETS}},
        time_basis=TimeBasis.ASSUMED, assumed_available_at=available or tip + timedelta(hours=8),
        time_note="synthetic clock")


def checkpoint(**updates):
    r = {"season": 2022, "actual_exposure": 10., "dnp": False,
         "rate_mean": [.2] * 4, "rate_variance": [.03] * 4, "actual_counts": [6.] * 4}
    r.update(updates)
    return r


class RateUncertaintyTests(unittest.TestCase):
    def test_independent_state_matches_frozen_update_across_dnp_and_season(self):
        rows = [observation("old", year=2021), observation("dnp", minutes=0.),
                observation("played", day=3), observation("played2", day=7, minutes=35.)]
        frozen = model.StructuralModel(rows, recipe())._player_state("p1", rows)
        rates, variance = independent_state(list(reversed(rows)), recipe())
        close(rates, frozen["rates"].tolist(), "rates")
        close(variance, frozen["variance"].tolist(), "variance")
        self.assertTrue(all(v > 0 for v in variance))

    def test_exposure_scaling_distinguishes_mean_and_rate_variance_units(self):
        p = {"minutes": 20., "duration_minutes": 40., "possessions_estimate": 80.}
        self.assertEqual(exposure(p), 40.)
        a = innovations([checkpoint()], "points")[0]
        b = innovations([checkpoint(actual_exposure=20.)], "points")[0]
        self.assertEqual(b[0], 2 * a[0])
        self.assertEqual(b[2], 4 * a[2])
        with self.assertRaises(ValueError):
            exposure(dict(p, duration_minutes=0))

    def test_dnp_does_not_supply_rate_innovation(self):
        a = checkpoint()
        dnp = checkpoint(actual_exposure=0., dnp=True, actual_counts=[0.] * 4)
        self.assertEqual(len(innovations([a, dnp], "points")), 1)
        with self.assertRaisesRegex(ValueError, "Invalid DNP"):
            innovations([checkpoint(actual_exposure=0., dnp=True)], "points")

    def test_noise_scalars_separate_uncertainty_once_and_match_original_cap(self):
        s = freeze_scalars([checkpoint()], recipe())["points"]
        self.assertEqual(s["raw_total"], 8.)
        self.assertEqual(s["raw_noise"], 6.5)
        self.assertEqual(s["sum_rate_uncertainty"], 3.)
        self.assertFalse(s["noise_floor"])
        with self.assertRaisesRegex(ValueError, "check seasons"):
            freeze_scalars([checkpoint(season=2023)], recipe())

    def test_constant_uncertainty_share_cannot_beat_uncapped_constant_control(self):
        rows = [checkpoint()]
        s = freeze_scalars(rows, recipe())
        r = variance_report(rows, s)["points"]
        self.assertEqual(r["rate_minus_constant_score"], 0.)
        self.assertEqual(r["variances"]["rate"]["mean_variance"], 16.)
        self.assertAlmostEqual(r["aggregate_rate_uncertainty_share"], 3 / 16)
        self.assertLess(r["rate_minus_old_score"], 0.)

    def test_floor_activations_are_reported_and_invalid_variance_fails(self):
        p = recipe()
        p["count_fano"] = [1.] * 4
        s = freeze_scalars([checkpoint(actual_counts=[2.] * 4)], p)["points"]
        self.assertEqual(s["raw_total"], 0.)
        self.assertEqual(s["raw_noise"], -1.5)
        self.assertTrue(s["total_floor"] and s["noise_floor"])
        for v in (-.01, float("nan"), float("inf")):
            with self.subTest(v=v), self.assertRaises(ValueError):
                innovations([checkpoint(rate_variance=[v] * 4)], "points")

    def test_future_target_outcome_changes_cannot_change_pregame_state(self):
        old = observation("old")
        outcome = observation("target", year=2023)
        request = ForecastRequest("p1", "target", "t1", "t2", 2023,
                                  outcome.effective_at, outcome.effective_at - timedelta(hours=24))
        target = replace(outcome, assumed_available_at=request.as_of - timedelta(hours=1))
        later = observation("future", year=2024, counts={m: 1000 for m in model.MARKETS})
        first, _ = rate_checkpoint(model.StructuralModel([old], recipe()), request, outcome)
        altered = replace(outcome, payload=dict(outcome.payload, counts={m: 100 for m in model.MARKETS}), payload_hash=None)
        with patch.object(model.StructuralModel, "predict", side_effect=AssertionError("No count forecast")):
            second, history = rate_checkpoint(model.StructuralModel([later, target, old], recipe()), request, altered)
        self.assertEqual(first["rate_mean"], second["rate_mean"])
        self.assertEqual(first["rate_variance"], second["rate_variance"])
        self.assertEqual([r.event_id for r in history], ["old"])
        self.assertNotEqual(first["actual_counts"], second["actual_counts"])

    def test_pregame_checkpoint_refuses_2025_and_2026(self):
        for year in (2025, 2026):
            outcome = observation("target", year=year)
            request = ForecastRequest("p1", "target", "t1", "t2", year, outcome.effective_at,
                                      outcome.effective_at - timedelta(hours=24))
            with self.subTest(year=year), self.assertRaisesRegex(ValueError, "excluded years"):
                rate_checkpoint(model.StructuralModel([], recipe()), request, outcome)

    def test_manifest_filters_excluded_assets_before_path_or_hash_access(self):
        assets = []
        for kind in sources.KINDS:
            for year in range(2010 if kind == "pbp" else 2003, 2025):
                assets.append({"kind": kind, "season": year, "filename": f"{kind}_{year}.parquet",
                               "url": f"https://fixture/{sources.SOURCE_COMMIT}/wnba/file"})
        assets.extend([{"season": 2025, "filename": "/must/not/open"},
                       {"season": 2026, "filename": "/must/not/open/either"}])
        selected = retained_assets({"source_commit": sources.SOURCE_COMMIT, "complete": True, "assets": assets})
        self.assertEqual(len(selected), 81)
        self.assertTrue(all(a["season"] <= 2024 for a in selected))
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            retained_assets({"source_commit": sources.SOURCE_COMMIT, "complete": True, "assets": assets[1:]})

    def test_sample_is_fixed_from_ids_and_ignores_other_values(self):
        schedule = [{"season": year, "game_id": f"{year}-{i}"} for year in (2023, 2024) for i in range(30)]
        first = sample_games(schedule)
        self.assertEqual(len(first), 40)
        self.assertEqual(first, sample_games([dict(r, points=99999) for r in reversed(schedule)]))

    def test_schedule_team_ids_match_normalized_numeric_and_nullable_source_ids(self):
        self.assertEqual(scheduled_sides({"home_id": 3., "away_id": "4.0"}), {"3", "4"})
        self.assertEqual(scheduled_sides({"home_id": pd.NA, "away_id": 4.}), {None, "4"})

    def test_independent_clocks_use_registered_parser_fields_and_maximum_delay(self):
        schedule = pd.DataFrame([{"game_id": "clock", "season": 2023, "season_type": 2,
            "date": "2023-06-01T20:00:00Z", "game_date_time": "2023-06-01T16:00:00-04:00",
            "completed_at": "2023-06-02T06:00:00Z"}])
        pbp = pd.DataFrame([{"game_id": "clock", "wallclock": "2023-06-02T08:00:00Z"}])
        data = sources.HistoricalSources(pd.DataFrame(), pd.DataFrame(), pbp, schedule, {})
        actual = independent_clocks(data)["clock"]
        expected = [datetime(2023, 6, 1, 20, tzinfo=UTC), datetime(2023, 6, 2, 9, tzinfo=UTC)]
        self.assertEqual(actual, expected)
        self.assertEqual(tuple(actual), sources.game_clocks(data)["clock"][:2])
        data.pbp = pd.DataFrame()
        self.assertEqual(independent_clocks(data)["clock"][1], datetime(2023, 6, 2, 7, tzinfo=UTC))

    def test_raw_denominator_retains_missing_rows_and_flags_false_dnp_counts(self):
        normal = observation("g")
        raw = {"season": 2022, "game_id": "g", "athlete_id": "p1", "team_id": "t1",
               "opponent_team_id": "t2", "minutes": 20., "did_not_play": False,
               **{value: 8. for value in sources.COUNTS.values()}}
        missing = dict(raw, athlete_id="missing", minutes=float("nan"))
        impossible = dict(raw, game_id="bad", minutes=0.)
        data = sources.HistoricalSources(pd.DataFrame([raw, missing, impossible]), pd.DataFrame(),
                                         pd.DataFrame(), pd.DataFrame(), {}, {"excluded_noncompetitive_game_ids": []})
        inventory = appearance_inventory(data, [normal])
        d = denominator(inventory)["2022"]
        self.assertEqual((d["raw_requested"], d["normalized"], d["missing_or_invalid"], d["impossible_raw_grade"]), (3, 1, 1, 1))
        self.assertEqual(d["explicit_dnp"], 0)

    def test_primary_gate_cannot_be_replaced_by_other_market_or_old_control(self):
        source = {"status": "PASS_UNDER_ASSUMPTION"}
        scalars = {"points": {"total_floor": False, "noise_floor": False}}
        good = {"aggregate_rate_uncertainty_share": .10, "rate_minus_constant_score": -.02,
                "variances": {"rate": {"squared_error_to_variance": 1.}}}
        checks = {year: {"points": deepcopy(good), "rebounds": deepcopy(good)} for year in ("2023", "2024")}
        self.assertEqual(decision(source, scalars, checks)["status"], "PASS")
        checks["2023"]["points"]["rate_minus_constant_score"] = -.009
        self.assertEqual(decision(source, scalars, checks)["decision"], "stop this variance change")
        self.assertFalse(decision(source, scalars, checks)["gates"]["useful_variance_score"])

    def test_source_failure_stops_before_any_scalar_or_check_requirement(self):
        r = decision({"status": "FAIL"})
        self.assertEqual(r["decision"], "collect named missing source")
        self.assertEqual(r["gates"], {"source": False})

    def test_bad_sample_clock_is_retained_and_cannot_be_waived_by_99pct_coverage(self):
        outcomes = [observation("bad", year=2023), observation("good23", year=2023, day=2),
                    observation("good24", year=2024)]
        requests = {o.event_id: ForecastRequest("p1", o.event_id, "t1", "t2", o.season,
                    o.effective_at, o.effective_at - timedelta(hours=24)) for o in outcomes}
        inventory = []
        for o in outcomes:
            row = {"season": o.season, "game_id": o.event_id, "player_id": "p1", "team_id": "t1",
                   "opponent_id": "t2", "status": "normalized", "source_record_id": o.record_id, "dnp": False}
            inventory.extend([row] * (1 if o.event_id == "bad" else 100))
        clocks = {o.event_id: [o.effective_at, o.available_at] for o in outcomes}
        clocks["bad"][1] += timedelta(seconds=1)
        data = sources.HistoricalSources(pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
            pd.DataFrame([{"game_id": o.event_id, "home_id": "t1", "away_id": "t2"} for o in outcomes]), {})
        mock_checkpoint = {"rate_mean": [.2] * 4, "rate_variance": [.4 / 60] * 4}
        examples = lambda observations, year: [(requests[o.event_id], o) for o in outcomes if o.season == year]
        with patch("research.diagnostics.rate_uncertainty.model.training_examples", side_effect=examples), \
             patch("research.diagnostics.rate_uncertainty.independent_clocks", return_value=clocks), \
             patch("research.diagnostics.rate_uncertainty.rate_checkpoint", return_value=(mock_checkpoint, [])):
            result = source_check(data, outcomes, inventory, [{"game_id": o.event_id} for o in outcomes], recipe(), float("inf"))
        self.assertEqual(len(result["rows"]), 201)
        self.assertTrue(all(c["coverage"] >= .99 for c in result["coverage"].values()))
        self.assertEqual(result["hard_failures"], 1)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("clock differs", result["rows"][0]["reason"])

    def test_existing_output_and_budget_refuse_execution(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch("research.diagnostics.rate_uncertainty.registration_identity", return_value={}), \
             patch("research.diagnostics.rate_uncertainty.frozen_recipe") as frozen:
            with self.assertRaises(FileExistsError):
                run_pilot("raw", "fit", "bundle", "seal", directory, "plan", "a" * 40)
            frozen.assert_not_called()
        with self.assertRaisesRegex(ValueError, "budget"):
            check_time(-1)
        with self.assertRaisesRegex(ValueError, "Budget"):
            run_pilot("raw", "fit", "bundle", "seal", "unused", "plan", "a" * 40, budget_seconds=601)

    def test_scalar_and_checkpoint_bytes_are_deterministic_and_exclusive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_rows(root / "one.gz", [checkpoint()])
            write_rows(root / "two.gz", [checkpoint()])
            self.assertEqual((root / "one.gz").read_bytes(), (root / "two.gz").read_bytes())
            write(root / "freeze.json", {"frozen": True})
            with self.assertRaises(FileExistsError):
                write(root / "freeze.json", {"frozen": False})


if __name__ == "__main__":
    unittest.main()
