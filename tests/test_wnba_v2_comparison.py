"""Source corruption and complete count-denominator regression checks."""
from dataclasses import replace
import unittest

import pandas as pd

from research.engine.sources import HistoricalSources
from research.wnba_v2 import sources
from research.wnba_v2.comparison import source_check, count_comparison, verify_admitted_sources
from tests.test_wnba_v2_model import base_recipe
from tests.test_wnba_v2_sources import data, player


def two_seasons(rows=None):
    first = data(rows)
    second = data(rows)
    for frame in (second.player_box, second.team_box, second.schedule):
        frame["season"] = 2024
        frame["game_id"] = "g2"
    second.schedule["date"] = "2024-06-01T20:00:00Z"
    second.schedule["game_date_time"] = "2024-06-01T16:00:00-04:00"
    return HistoricalSources(*(pd.concat([getattr(first, name), getattr(second, name)], ignore_index=True)
                               for name in ("player_box", "team_box", "pbp", "schedule")), first.manifest)


def check(source, rows):
    return source_check(source, rows, [{"game_id": "g1"}, {"game_id": "g2"}], base_recipe(), float("inf"))


class WnbaV2ComparisonTests(unittest.TestCase):
    def test_raw_count_or_team_roster_corruption_cannot_pass_independent_source_check(self):
        source = two_seasons()
        rows = sources.observations(source)
        self.assertEqual(check(source, rows)["status"], "PASS_UNDER_ASSUMPTION")
        for kind in ("counts", "roster"):
            changed = []
            for row in rows:
                payload = dict(row.payload)
                if kind == "counts" and payload["record_type"] == "player_box":
                    payload["counts"] = dict(payload["counts"], points=999.)
                if kind == "roster" and payload["record_type"] == "team_box":
                    payload["roster_count"] += 5
                changed.append(replace(row, payload=payload, payload_hash=None))
            self.assertEqual(check(source, changed)["status"], "FAIL")

    def test_competitive_team_problem_outside_sample_is_still_hard_failure(self):
        source = two_seasons()
        rows = sources.observations(source)
        # A completed team source may be absent from sampled player histories.
        # Its full-audit conflict must nevertheless prevent scalar fitting.
        source.quality["wnba_v2_source_audit"]["team_rows"].append(
            {"game_id": "g1", "team_id": "t1", "issues": ["invalid_team_exposure"]})
        result = check(source, rows)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("audit", result["setup_failure"])

    def test_only_fully_omitted_pre2015_keys_can_be_quarantined(self):
        source = two_seasons()
        old = data([player(athlete_id="duplicate", minutes=None, **{v: None for v in sources.COUNTS.values()}),
                    player(athlete_id="duplicate", team_id="t2", opponent_team_id="t1", minutes=None,
                           **{v: None for v in sources.COUNTS.values()})])
        for frame in (old.player_box, old.team_box, old.schedule):
            frame["season"] = 2005
            frame["game_id"] = "old-missing"
        old.schedule["date"] = "2005-06-01T20:00:00Z"
        old.schedule["game_date_time"] = "2005-06-01T16:00:00-04:00"
        old.team_box["field_goals_attempted"] = 0.
        old.team_box["free_throws_attempted"] = 0.
        old.team_box["offensive_rebounds"] = 0.
        old.team_box["total_turnovers"] = 0.
        for name in ("player_box", "team_box", "schedule"):
            setattr(source, name, pd.concat([getattr(source, name), getattr(old, name)], ignore_index=True))
        observations = sources.observations(source)
        with self.assertRaises(ValueError):
            verify_admitted_sources(source, observations)
        result = verify_admitted_sources(source, observations, quarantine_pre2015=True)
        self.assertEqual(result["status"], "PASS_UNDER_ASSUMPTION")
        self.assertEqual(result["quarantine"]["player_rows"], 2)
        self.assertEqual(result["quarantine"]["by_season_and_cause"]["player"]["2005"]["rows"], 2)
        self.assertEqual(result["quarantine"]["quarantined_keys_consumed"], 0)
        forged = replace(next(r for r in observations if r.payload["record_type"] == "player_box"),
                         entity_id="duplicate", event_id="old-missing", record_id="forged")
        with self.assertRaisesRegex(ValueError, "Quarantined"):
            verify_admitted_sources(source, observations + [forged], quarantine_pre2015=True)
        # Even an omitted player key can affect an admitted team's roster
        # count or duration fallback. That is not a complete quarantine.
        source.team_box.loc[source.team_box.game_id.eq("old-missing"), "field_goals_attempted"] = 50.
        indirect = sources.observations(source)
        with self.assertRaisesRegex(ValueError, "competitive"):
            verify_admitted_sources(source, indirect, quarantine_pre2015=True)
        source.team_box.loc[source.team_box.game_id.eq("old-missing"), "field_goals_attempted"] = 0.
        # Moving the same unavailable raw source to the comparison era cannot
        # turn it into an allowed quarantine, even without reading any scores.
        source.player_box.loc[source.player_box.game_id.eq("old-missing"), "season"] = 2015
        source.team_box.loc[source.team_box.game_id.eq("old-missing"), "season"] = 2015
        source.schedule.loc[source.schedule.game_id.eq("old-missing"), "season"] = 2015
        source.schedule.loc[source.schedule.game_id.eq("old-missing"), "date"] = "2015-06-01T20:00:00Z"
        source.schedule.loc[source.schedule.game_id.eq("old-missing"), "game_date_time"] = "2015-06-01T16:00:00-04:00"
        observations = sources.observations(source)
        with self.assertRaisesRegex(ValueError, "competitive"):
            verify_admitted_sources(source, observations, quarantine_pre2015=True)

    def test_all_admitted_records_are_reconciled_outside_the_fixed_sample(self):
        source = two_seasons()
        observations = sources.observations(source)
        changed = []
        for row in observations:
            payload = dict(row.payload)
            if row.event_id == "g1" and payload["record_type"] == "player_box":
                payload["counts"] = dict(payload["counts"], points=999.)
            changed.append(replace(row, payload=payload, payload_hash=None))
        with self.assertRaisesRegex(ValueError, "Raw counts"):
            verify_admitted_sources(source, changed, quarantine_pre2015=True)

    def test_included_population_is_bijective_and_seasons_and_team_entities_reconcile(self):
        source = two_seasons()
        observations = sources.observations(source)
        for changed in (observations[:-1], observations + observations[:1]):
            with self.assertRaisesRegex(ValueError, "bijection"):
                verify_admitted_sources(source, changed, quarantine_pre2015=True)
        player_index = next(i for i, row in enumerate(observations) if row.payload["record_type"] == "player_box")
        bad_year = list(observations)
        bad_year[player_index] = replace(bad_year[player_index], season=2022)
        with self.assertRaisesRegex(ValueError, "season"):
            verify_admitted_sources(source, bad_year, quarantine_pre2015=True)
        team_indices = [i for i, row in enumerate(observations) if row.event_id == "g1" and row.payload["record_type"] == "team_box"]
        wrong_teams = list(observations)
        a, b = team_indices
        wrong_teams[a] = replace(observations[a], entity_id=observations[b].entity_id)
        wrong_teams[b] = replace(observations[b], entity_id=observations[a].entity_id)
        with self.assertRaisesRegex(ValueError, "Team entity"):
            verify_admitted_sources(source, wrong_teams, quarantine_pre2015=True)

    def test_valid_points_survive_missing_other_count_and_unknown_exposure(self):
        raw = [player(athlete_id="normal"), player(athlete_id="partial", rebounds=None),
               player(athlete_id="short", minutes=0.)]
        # Provider uses total_rebounds, not the market label.
        raw[1][sources.COUNTS["rebounds"]] = None
        source = data(raw)
        observations = sources.observations(source)
        scalars = {m: {"F_total": 2., "F_noise": 1.5} for m in sources.COUNTS}
        rows, result = count_comparison(observations, base_recipe(), scalars, 2023, float("inf"))
        self.assertEqual(result["markets"]["points"]["n"], 3)
        self.assertEqual(result["markets"]["rebounds"]["n"], 2)
        self.assertEqual(result["denominators"]["by_market"]["rebounds"]["missing_or_invalid"], 1)
        short = result["markets"]["points"]["by_exposure"]["unknown"]
        self.assertEqual(short["n"], 1)
        self.assertIsNotNone(short["mean_nll"]["rate"])


if __name__ == "__main__":
    unittest.main()
