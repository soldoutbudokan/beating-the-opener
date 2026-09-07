"""Source gates test semantic failures, not just parser happy paths."""
import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from research.engine.sources import (HistoricalSources, SOURCE_COMMIT, box_pilot,
    game_clocks, load_frozen_quotes, observations, select_pilot_sample, verify_manifest)


def fixture():
    schedule, players, teams = [], [], []
    for year in (2015, 2020, 2024):
        for day in range(1, 21):
            gid = f"{year}{day:02}"
            tip = pd.Timestamp(f"{year}-06-{day:02}T23:00:00Z")
            schedule.append({"game_id": gid, "season": year, "date": tip.isoformat(),
                "game_date_time": tip.tz_convert("America/New_York"), "status_period": 4,
                "season_type": 2, "type_abbreviation": "STD", "home_id": "A", "away_id": "B"})
            for team, opp in (("A", "B"), ("B", "A")):
                teams.append({"game_id": gid, "season": year, "team_id": team,
                    "opponent_team_id": opp, "field_goals_attempted": 65,
                    "free_throws_attempted": 20, "offensive_rebounds": 10,
                    "total_turnovers": 12, "team_score": 80})
                players.append({"game_id": gid, "season": year, "team_id": team,
                    "athlete_id": team + "1", "opponent_team_id": opp,
                    "minutes": 30., "starter": True, "did_not_play": False,
                    "points": 15., "rebounds": 5., "assists": 3.,
                    "three_point_field_goals_made": 1.})
    return HistoricalSources(pd.DataFrame(players), pd.DataFrame(teams),
        pd.DataFrame(columns=["game_id", "wallclock"]), pd.DataFrame(schedule),
        {"recorded_at_utc": "2026-09-07T12:00:00Z"})


class SourceTests(unittest.TestCase):
    def test_pilot_selection_uses_ids_and_preserves_missing_games(self):
        data = fixture()
        sample = select_pilot_sample(data.schedule)
        reordered = data.schedule.sample(frac=1, random_state=4).assign(unused_result=999)
        self.assertEqual(sample, select_pilot_sample(reordered))
        self.assertEqual(box_pilot(data, sample)["status"], "PASS_UNDER_ASSUMPTION")
        missing = {sample[0]["game_id"], sample[1]["game_id"]}
        data.player_box = data.player_box[~data.player_box.game_id.isin(missing)]
        report = box_pilot(data, sample)
        self.assertEqual(len(report["rows"]), 120)
        self.assertEqual(report["families"]["box_identity_counts"]["usable"], 58)
        self.assertEqual(report["status"], "FAIL")

    def test_clocks_reject_wrong_offset_and_late_game_events_delay_availability(self):
        data = fixture()
        gid = data.schedule.iloc[0].game_id
        late = "2015-06-02T08:00:00Z"
        data.pbp = pd.DataFrame([{"game_id": gid, "wallclock": late}])
        self.assertEqual(game_clocks(data)[gid][1], pd.Timestamp("2015-06-02T09:00:00Z"))
        data.schedule.loc[0, "date"] = "2015-06-01T19:00:00Z"
        with self.assertRaises(ValueError):
            game_clocks(data)
        with self.assertRaises(ValueError):
            game_clocks(fixture(), availability_hours=0)

    def test_market_fields_never_enter_observations_and_missing_minutes_are_not_dnp(self):
        data = fixture()
        baseline = observations(data)
        data.player_box["line"] = 999
        data.team_box["spread"] = -999
        changed = observations(data)
        self.assertEqual([o.manifest_entry() for o in baseline], [o.manifest_entry() for o in changed])
        data.player_box.loc[0, "minutes"] = float("nan")
        self.assertEqual(len(observations(data)), len(baseline) - 1)
        data.player_box.loc[0, "did_not_play"] = True
        fixed = observations(data)
        row = next(o for o in fixed if o.record_id == "player_box:201501:A1")
        self.assertEqual(row.payload["minutes"], 0)
        self.assertTrue(row.payload["did_not_play"])
        self.assertEqual(row.payload["possession_basis"], "box_estimate")
        self.assertIn("team_source_payload_hash", row.payload)
        self.assertEqual(tuple(row.payload["scheduled_team_ids"]), ("A", "B"))
        team = next(o for o in fixed if o.record_id == "team_box:201501:A")
        self.assertEqual(team.payload["roster_count"], 1)
        self.assertIn("completed", team.payload["roster_count_basis"])

    def test_future_season_and_conflicting_identity_are_not_development(self):
        data = fixture()
        data.schedule.loc[0, "season"] = 2026
        with self.assertRaises(ValueError):
            observations(data)
        data = fixture()
        data.player_box = pd.concat([data.player_box, data.player_box.iloc[[0]]], ignore_index=True)
        output = observations(data)
        self.assertFalse(any(o.record_id == "player_box:201501:A1" for o in output))
        self.assertEqual(data.quality["observation_omissions"]["player_conflicting_identity"], 2)

    def test_allstar_is_not_regular_history_even_when_season_type_is_two(self):
        data = fixture()
        data.schedule.loc[0, "type_abbreviation"] = "ALLSTAR"
        self.assertFalse(any(o.event_id == "201501" for o in observations(data)))
        self.assertEqual(data.quality["excluded_noncompetitive_game_ids"], ["201501"])

    def test_unknown_minutes_fail_pilot_and_played_negative_counts_fail(self):
        data = fixture()
        sample = select_pilot_sample(data.schedule)
        data.player_box.loc[0, "minutes"] = float("nan")
        data.player_box.loc[2, "points"] = -1
        result = box_pilot(data, sample)
        self.assertEqual(result["families"]["box_identity_counts"]["usable"], 58)

    def test_manifest_refuses_2026_traversal_missing_assets_and_bad_frozen_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            path = root / "manifest.json"
            value = {"source_commit": SOURCE_COMMIT, "complete": True,
                     "assets": [{"kind": "player_box", "season": 2026}]}
            path.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError, "scope"):
                verify_manifest(root)
            value["assets"] = [{"kind": "player_box", "season": 2025, "filename": "../escape.parquet"}]
            path.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError, "basenames"):
                verify_manifest(root)
            value["assets"] = []
            path.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError, "85"):
                verify_manifest(root)
            (root / "forecasts_8h.csv.gz").write_bytes(b"altered baseline")
            with self.assertRaisesRegex(ValueError, "bytes changed"):
                load_frozen_quotes(root)


if __name__ == "__main__":
    unittest.main()
