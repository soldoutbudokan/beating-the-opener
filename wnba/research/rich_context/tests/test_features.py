"""Boundary checks for information time, source quality and richer context."""
import unittest

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from wnba.research.rich_context.features import (
    aggregate_pbp, build_state_tables, feature_columns, query_features,
)


def fixtures():
    boxes, teams, schedule, plays = [], [], [], []
    for game, date in ((1, "2025-06-01T20:00Z"), (2, "2025-06-04T20:00Z"),
                       (3, "2025-06-07T20:00Z")):
        schedule.append({"game_id": game, "tip_at": date})
        for athlete, team in ((11, 1), (12, 1), (21, 2), (22, 2)):
            boxes.append({
                "game_id": game, "athlete_id": athlete, "team_id": team,
                "opponent_team_id": 3 - team, "game_date": date[:10],
                "minutes": 20 if game == 1 else 40, "points": 2.,
                "rebounds": 1., "offensive_rebounds": 0., "defensive_rebounds": 1.,
                "assists": 0., "field_goals_attempted": 1., "field_goals_made": 1.,
                "three_point_field_goals_attempted": 0., "three_point_field_goals_made": 0.,
                "free_throws_attempted": 0., "steals": 0., "blocks": 0., "turnovers": 0.,
                "starter": True,
            })
            plays.append({"game_id": game, "athlete_id_1": athlete, "athlete_id_2": np.nan,
                          "team_id": team, "type_text": "Layup Shot", "text": "Player makes 2-foot layup",
                          "sequence_number": athlete, "shooting_play": True, "scoring_play": True,
                          "points_attempted": 2, "score_value": 2, "period_number": 1,
                          "clock_display_value": "9:00", "home_score": 2, "away_score": 2,
                          "coordinate_x": -214748365., "coordinate_y": 214748365.})
            plays.append({"game_id": game, "athlete_id_1": athlete, "athlete_id_2": np.nan,
                          "team_id": team, "type_text": "Defensive Rebound", "text": "Player defensive rebound",
                          "sequence_number": athlete + 100, "shooting_play": False, "scoring_play": False,
                          "points_attempted": 0, "score_value": 0, "period_number": 4,
                          "clock_display_value": "2:00", "home_score": 20, "away_score": 18})
        for team in (1, 2):
            teams.append({"game_id": game, "team_id": team, "game_date": date[:10],
                          "field_goals_attempted": 20 + game, "offensive_rebounds": 2.,
                          "total_turnovers": 3., "free_throws_attempted": 4.,
                          "team_score": 40 + game, "opponent_team_score": 39 + game,
                          "assists": 10., "total_rebounds": 15., "three_point_field_goals_attempted": 4.,
                          "defensive_rebounds": 13.})
    return tuple(pd.DataFrame(x) for x in (boxes, teams, plays, schedule))


def queries(at="2025-06-06T12:00Z"):
    return pd.DataFrame({"game_id": [3], "athlete_id": [11], "team_id": [1],
                         "opponent_team_id": [2], "tip_at": ["2025-06-07T20:00Z"],
                         "forecast_at": [at], "home": [1]})


class FeatureTimeTests(unittest.TestCase):
    def test_latest_completed_game_is_in_state(self):
        state = build_state_tables(*fixtures())
        q = query_features(state, queries())
        self.assertAlmostEqual(q.min_ewf.iloc[0], (20 * .82 + 40) / 1.82)
        self.assertEqual(q.gp.iloc[0], 2)
        self.assertEqual(q.player_source_game_id.iloc[0], "2")
        self.assertLess(q.max_source_at.iloc[0], q.forecast_at.iloc[0])

    def test_future_outcomes_and_target_rosters_do_not_change_features(self):
        box, team, pbp, schedule = fixtures()
        old = build_state_tables(box, team, pbp, schedule)
        box.loc[box.game_id.eq(3), ["minutes", "points", "assists"]] = [2., 90., 40.]
        box.loc[box.game_id.eq(3), "team_id"] = 9
        team.loc[team.game_id.eq(3), "team_score"] = 900
        pbp.loc[pbp.game_id.eq(3), "text"] = "Player misses 24-foot three point shot"
        new = build_state_tables(box, team, pbp, schedule)
        assert_frame_equal(query_features(old, queries()), query_features(new, queries()))

    def test_prefix_build_matches_full_history(self):
        box, team, pbp, schedule = fixtures()
        full = build_state_tables(box, team, pbp, schedule)
        short = build_state_tables(*(x[x.game_id.lt(3)] for x in (box, team, pbp, schedule)))
        assert_frame_equal(query_features(full, queries()), query_features(short, queries()))

    def test_batch_duplicates_order_and_unrelated_outcomes_do_not_matter(self):
        state = build_state_tables(*fixtures())
        one = query_features(state, queries())
        batch = pd.concat([queries()] * 10, ignore_index=True)
        batch.loc[1:, "athlete_id"] = 21
        batch["actual"] = [0] + [100] * 9
        result = query_features(state, batch).iloc[[0]].drop(columns="actual")
        assert_frame_equal(one, result)
        reversed_q = batch.iloc[::-1].copy()
        reversed_result = query_features(state, reversed_q).sort_index().drop(columns="actual")
        assert_frame_equal(query_features(state, batch).drop(columns="actual"), reversed_result)

    def test_strict_boundary_and_24_hour_sensitivity(self):
        state = build_state_tables(*fixtures())
        # June 4 at20:00 +8h is exactly June5 at04:00: equality is excluded.
        q = query_features(state, queries("2025-06-05T04:00Z"))
        self.assertEqual(q.gp.iloc[0], 1)
        q = query_features(state, queries("2025-06-05T04:01Z"))
        self.assertEqual(q.gp.iloc[0], 2)
        delayed = build_state_tables(*fixtures(), availability_hours=24)
        self.assertEqual(query_features(delayed, queries("2025-06-05T04:01Z")).gp.iloc[0], 1)

    def test_protected_year_rejected(self):
        box, team, pbp, schedule = fixtures()
        box.loc[0, "game_date"] = "2026-01-01"
        with self.assertRaisesRegex(ValueError, "refuses 2026"):
            build_state_tables(box, team, pbp, schedule)

    def test_delayed_previous_source_blocks_later_dependent_states(self):
        box, team, pbp, schedule = fixtures()
        schedule["completed_at"] = pd.Series(pd.NaT, index=schedule.index, dtype="datetime64[ns, UTC]")
        schedule.loc[schedule.game_id.eq(1), "completed_at"] = pd.Timestamp("2025-06-06T20:00Z")
        state = build_state_tables(box, team, pbp, schedule)
        # Game2 nominally available June5, but its cumulative player/team
        # history depends on game1, whose source is not available untilJune6.
        q = query_features(state, queries("2025-06-06T12:00Z"))
        self.assertTrue(pd.isna(q.gp.iloc[0]))
        self.assertTrue(pd.isna(q.tm_pace_ew.iloc[0]))
        later = query_features(state, queries("2025-06-06T22:00Z"))
        self.assertEqual(later.gp.iloc[0], 2)

    def test_query_at_tip_rejected(self):
        with self.assertRaisesRegex(ValueError, "precede tip"):
            query_features(build_state_tables(*fixtures()), queries("2025-06-07T20:00Z"))


class PbpQualityTests(unittest.TestCase):
    def test_coordinate_distance_uses_attacking_basket_for_heaves(self):
        pbp = pd.DataFrame([{
            "game_id": 1, "athlete_id_1": 11, "team_id": 1,
            "home_team_id": 1, "away_team_id": 2, "type_text": "Jump Shot",
            "text": "Player misses three point jumper", "shooting_play": True,
            "points_attempted": 3, "coordinate_x": -10., "coordinate_y": 0.,
        }])
        player, _, _ = aggregate_pbp(pbp)
        self.assertAlmostEqual(player.pbp_distance_sum.iloc[0], 51.75)

    def test_old_text_assists_and_coordinate_sentinels(self):
        pbp = pd.DataFrame([
            {"game_id": 1, "athlete_id_1": 11, "athlete_id_2": 12, "team_id": 1,
             "type_text": "JumpShot", "text": "Player made 24 ft Three Point Jumper. Assisted by Other.",
             "shooting_play": True, "scoring_play": True, "score_value": 3,
             "coordinate_x": 214748365., "coordinate_y": -214748365.},
            {"game_id": 1, "athlete_id_1": 12, "athlete_id_2": np.nan, "team_id": 1,
             "type_text": "JumpShot", "text": "Player missed Jumper.", "shooting_play": True,
             "scoring_play": False, "score_value": 0, "coordinate_x": 214748365., "coordinate_y": -214748365.},
        ])
        player, _, coverage = aggregate_pbp(pbp)
        p = player.set_index("athlete_id")
        self.assertEqual(p.loc["11", "pbp_three_fgm"], 1)
        self.assertEqual(p.loc["12", "pbp_created_makes"], 1)
        self.assertEqual(p.loc["12", "pbp_distance_count"], 0)
        self.assertEqual(p.loc["11", "pbp_distance_sum"], 24)
        self.assertEqual(coverage["shot_distance_coverage"], .5)

    def test_inconsistent_family_is_missing_and_older_source_is_visible(self):
        box, team, pbp, schedule = fixtures()
        box.loc[box.game_id.eq(2) & box.athlete_id.eq(11), "field_goals_attempted"] = 8
        state = build_state_tables(box, team, pbp, schedule)
        q = query_features(state, queries())
        self.assertEqual(state.coverage["reconciliation"]["shot"]["invalid_games"], 1)
        self.assertEqual(q.pbp_shot_valid_games.iloc[0], 1)
        self.assertEqual(q.pbp_shot_source_at.iloc[0], pd.Timestamp("2025-06-02T04:00Z"))
        self.assertEqual(q.pbp_rebound_valid_games.iloc[0], 2)
        self.assertAlmostEqual(q.pbp_rim_share_ewf.iloc[0], 1.)

    def test_predictor_list_excludes_outcomes_identifiers_and_source_times(self):
        state = build_state_tables(*fixtures())
        columns = feature_columns(state)
        self.assertIn("pbp_rim_share_ewf", columns["rich"])
        self.assertIn("opp_pbp_allowed_rim_share_ew", columns["rich"])
        for forbidden in ("game_id", "athlete_id", "source_at", "minutes", "points", "team_score"):
            self.assertNotIn(forbidden, columns["basic"] + columns["rich"])


if __name__ == "__main__":
    unittest.main()
