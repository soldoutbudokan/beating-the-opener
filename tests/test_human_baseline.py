import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from research.prospective_props.human_baseline import (ARMS, evaluate, summarize_rows,
    save_evaluation, validate_releases, verify_saved)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def releases(root):
    """Fully synthetic completed receipts obey the strict production schema."""
    (root / "recipe.json").write_bytes(b"synthetic registered recipe")
    prepared = {"population": {"endpoint_date": "2026-08-14", "endpoint_n": 3045,
                "minimum": 3000, "endpoint_rule": "entire_first_ET_date_reaching_threshold",
                "available_eligible_n": 3045, "daily_counts": [{"date": "2026-08-14", "n": 3045, "cumulative": 3045}]},
                "recipe_sha256": sha(root / "recipe.json"), "implementation_sha256": "b" * 64, "forecasts": {}}
    specs = {}
    empty_economics = {"bets": 0, "profit": 0., "stake": 0., "roi": None,
                       "registered_t": None, "selections": []}
    def segment(n):
        return {"n": n, "log_loss_model": .6 if n else None,
                "log_loss_opener": .6 if n else None,
                "model_minus_opener": {"estimate": 0. if n else None,
                                       "registered_t": 0. if n else None, "ci95": None},
                "model_minus_close": {"n": 0, "estimate": None, "registered_t": None, "ci95": None},
                "economics": {"candidate": copy.deepcopy(empty_economics),
                              "placebo": copy.deepcopy(empty_economics)}}
    for arm in ARMS:
        forecast = root / f"{arm}.forecasts.json.gz"
        forecast.write_bytes(b"synthetic frozen forecasts")
        prepared["forecasts"][arm] = {"path": forecast.name, "sha256": sha(forecast)}
        result = root / f"{arm}.results.json"
        result.write_text(json.dumps({"schema": "prospective-props-reconstruction-v1",
            "arm": arm, "population": prepared["population"], "original_forecast_bytes_recovered": False,
            "segments": {"overall": segment(3045), "august_1_3": segment(0),
                         "august_4_onward": segment(3045)}}))
        receipt = root / f"{arm}.receipt.json"
        receipt.write_text(json.dumps({"status": "complete", "arm": arm,
            "registration_commit": "c" * 40, "registration_sha256": "d" * 64,
            "recipe_sha256": prepared["recipe_sha256"], "implementation_sha256": "9" * 64,
            "generation_implementation_sha256": prepared["implementation_sha256"],
            "forecasts_sha256": sha(forecast), "results_sha256": sha(result),
            "attestation_sha256": "e" * 64, "freeze_commit": "f" * 40, "reconstructed": True,
            "completed_at": "2026-09-07T17:00:00Z"}))
        specs[arm] = {"receipt_path": str(receipt), "prepared_dir": str(root)}
    (root / "prepared.json").write_text(json.dumps(prepared))
    return specs


def data():
    schedule = []
    box = []
    for day in (12, 15, 17, 19):
        tip = pd.Timestamp(f"2026-08-{day:02}T23:00:00Z")
        gid = str(day)
        schedule.append({"game_id": gid, "season": 2026, "date": tip.isoformat(),
            "game_date_time": tip.tz_convert("America/New_York"), "home_id": "A", "away_id": "B"})
        box.append({"game_id": gid, "season": 2026, "athlete_id": "p", "team_id": "A",
            "athlete_display_name": "Test Player", "minutes": 20. if day != 17 else float("nan"),
            "did_not_play": day == 17})
    return pd.DataFrame(box), pd.DataFrame(schedule)


def entry(**kw):
    return {"player": "Test Player", "added": "2026-08-15T12:00:00Z", "game_date": "2026-08-15",
            "status": "in", "minutes_est": 24, "minutes_range": [15, 30], "superseded_by": None, **kw}


class HumanBaselineTests(unittest.TestCase):
    def test_release_requires_both_completed_hashed_results_and_forecasts(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); spec = releases(root)
            self.assertEqual(str(validate_releases(spec)[0]), "2026-08-14")
            with self.assertRaises(ValueError):
                validate_releases({ARMS[0]: spec[ARMS[0]]})
            (root / f"{ARMS[1]}.forecasts.json.gz").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "hashes disagree"):
                validate_releases(spec)

    def test_preserves_all_entries_and_never_grades_late_or_protected_entries(self):
        with tempfile.TemporaryDirectory() as d:
            spec = releases(Path(d)); box, schedule = data()
            entries = [entry(), entry(game_date="2026-08-14"), entry(added="2026-08-15T23:00:00Z"),
                       entry(player="Unseen Player")]
            result = evaluate(entries, box, schedule, spec)
            self.assertEqual(result["entries_requested"], 4)
            self.assertEqual([r["resolution"] for r in result["rows"]],
                ["resolved", "excluded_before_or_at_endpoint", "late_at_or_after_tip", "unresolved_identity_or_game"])
            self.assertEqual(result["primary_all_entries"]["in"]["status"], "insufficient")
            self.assertIsNone(result["primary_all_entries"]["in"]["minutes_est"]["mae"])
            self.assertEqual(verify_saved(result)["status"], "PASS")
            save_evaluation(result, Path(d) / "saved")
            with self.assertRaises(FileExistsError):
                save_evaluation(result, Path(d) / "saved")
            altered = copy.deepcopy(result); altered["rows"][0]["actual_minutes"] = 0
            with self.assertRaisesRegex(ValueError, "rows changed"):
                verify_saved(altered)

    def test_until_cleared_expands_only_before_actual_supersession(self):
        with tempfile.TemporaryDirectory() as d:
            spec = releases(Path(d)); box, schedule = data()
            entries = [entry(game_date=None, superseded_by=1),
                       entry(added="2026-08-18T12:00:00Z", game_date="2026-08-19", status="out")]
            result = evaluate(entries, box, schedule, spec)
            first = [r for r in result["rows"] if r["entry_index"] == 0]
            self.assertEqual([r["game_id"] for r in first], ["15", "17"])
            self.assertEqual(first[1]["actual_minutes"], 0)
            self.assertEqual(result["entry_log"][0]["source_entry"]["superseded_by"], 1)
            self.assertEqual(verify_saved(result)["status"], "PASS")

    def test_missing_box_is_not_dnp_and_final_team_cannot_repair_identity(self):
        with tempfile.TemporaryDirectory() as d:
            spec = releases(Path(d)); box, schedule = data()
            missing = box[box.game_id.ne("15")]
            result = evaluate([entry()], missing, schedule, spec)
            self.assertEqual(result["rows"][0]["resolution"], "missing_player_record")
            # A target box saying team C must not move the pregame matching team.
            box.loc[box.game_id.eq("15"), "team_id"] = "C"
            result = evaluate([entry()], box, schedule, spec)
            self.assertEqual(result["rows"][0]["game_id"], "15")
            self.assertEqual(result["rows"][0]["resolution"], "outcome_team_mismatch")

    def test_primary_keeps_duplicates_and_last_pre_tip_is_separate(self):
        with tempfile.TemporaryDirectory() as d:
            spec = releases(Path(d)); box, schedule = data()
            result = evaluate([entry(), entry(added="2026-08-15T15:00:00Z", minutes_est=20)], box, schedule, spec)
            self.assertEqual(result["primary_all_entries"]["in"]["entries"], 2)
            self.assertEqual(result["last_pre_tip_sensitivity"]["in"]["entries"], 1)

    def test_minimum_sample_counts_distinct_entries_not_expanded_pairs(self):
        rows = [{"resolution": "resolved", "entry_index": 0, "game_id": str(i), "athlete_id": "p",
                 "status": "out", "actual_minutes": 0., "minutes_est": None, "minutes_range": None} for i in range(40)]
        self.assertIsNone(summarize_rows(rows)["out"]["out_played_rate"])
        for i, row in enumerate(rows):
            row["entry_index"] = i
        self.assertEqual(summarize_rows(rows)["out"]["out_played_rate"], 0.)


if __name__ == "__main__":
    unittest.main()
