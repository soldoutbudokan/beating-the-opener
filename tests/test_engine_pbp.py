"""Possession accounting and failure isolation on complete synthetic games."""
import copy
import unittest

from research.engine.pbp import parse_game, parse_games, select_pilot_games


def fixture(extra=None):
    rows, scores = [], {"A": 0, "B": 0}
    production = {f"{team}{n}": dict(points=0, rebounds=0, assists=0,
                  three_point_field_goals_made=0) for team in "AB" for n in range(1, 7)}

    def emit(period, clock, kind, team=None, actor=None, actor2=None, text="", value=0):
        if value:
            scores[team] += value
            production[actor]["points"] += value
            if value == 3:
                production[actor]["three_point_field_goals_made"] += 1
        if "Rebound" in kind and actor:
            production[actor]["rebounds"] += 1
        rows.append(dict(game_id="g", season=2024, game_play_number=len(rows)+1,
            sequence_number=len(rows)+1, period_number=period, clock_display_value=clock,
            type_text=kind, text=text, team_id=team, athlete_id_1=actor, athlete_id_2=actor2,
            home_score=scores["A"], away_score=scores["B"], scoring_play=bool(value),
            shooting_play="Shot" in kind, score_value=value))

    for period in range(1, 5):
        emit(period, "9:00", "Jump Shot", "A", "A2", text="A2 makes jump shot", value=2)
        if period == 1 and extra:
            extra(emit)
        emit(period, "1:00", "Jump Shot", "B", "B2", text="B2 makes jump shot", value=2)
        emit(period, "0:00", "End Period", text="End of quarter")
    boxes = [dict(game_id="g", season=2024, athlete_id=player, team_id=player[0],
                  minutes=40 if int(player[1]) <= 5 else 0, starter=int(player[1]) <= 5,
                  did_not_play=int(player[1]) == 6, **values) for player, values in production.items()]
    return rows, boxes


class PossessionTests(unittest.TestCase):
    def test_complete_game_and_reorder_invariance(self):
        rows, boxes = fixture()
        parsed = parse_game(rows, boxes)
        self.assertTrue(parsed["qc"]["stints"], parsed["qc"])
        self.assertTrue(parsed["qc"]["on_off_shares"])
        self.assertEqual(parsed["teams"]["A"]["possessions"], 4)
        self.assertEqual(parsed["players"]["A2"]["court_minutes"], 40)
        self.assertEqual(parsed, parse_game(list(reversed(rows)), list(reversed(boxes))))

    def test_and_one_and_retained_technical_are_not_extra_possessions(self):
        def extra(emit):
            emit(1, "9:00", "Shooting Foul", "B", "B1")
            emit(1, "9:00", "Free Throw - 1 of 1", "A", "A2",
                 text="A2 makes free throw 1 of 1", value=1)
            emit(1, "8:30", "Free Throw - Technical", "A", "A2",
                 text="A2 makes technical free throw", value=1)
        rows, boxes = fixture(extra)
        parsed = parse_game(rows, boxes)
        self.assertTrue(parsed["qc"]["possessions"], parsed["qc"])
        self.assertEqual(parsed["teams"]["A"]["possessions"], 4)
        self.assertEqual(parsed["possession_endpoints"][0]["reason"], "final_free_throw")

    def test_offensive_rebound_and_missed_final_free_throw(self):
        def extra(emit):
            emit(1, "8:00", "Jump Shot", "A", "A2", text="A2 misses jump shot")
            emit(1, "7:58", "Offensive Rebound", "A", "A1")
            emit(1, "7:50", "Free Throw - 1 of 2", "A", "A2", text="A2 misses free throw 1 of 2")
            emit(1, "7:50", "Free Throw - 2 of 2", "A", "A2", text="A2 misses free throw 2 of 2")
            emit(1, "7:49", "Defensive Rebound", "B", "B1")
        rows, boxes = fixture(extra)
        parsed = parse_game(rows, boxes)
        self.assertEqual(parsed["teams"]["A"]["possessions"], 5)
        self.assertEqual(sum(p["reason"] == "defensive_rebound" for p in parsed["possession_endpoints"]), 1)

    def test_broken_substitution_never_guesses_lineups(self):
        def extra(emit):
            emit(1, "8:00", "Substitution", "A", "A6", None)
        rows, boxes = fixture(extra)
        result = parse_game(rows, boxes)
        self.assertTrue(result["qc"]["possessions"])
        self.assertFalse(result["qc"]["stints"])
        self.assertIsNone(result["players"]["A2"]["on_off"])
        self.assertIsNone(result["players"]["A2"]["court_minutes"])
        self.assertGreater(result["qc"]["valid_window_seconds"], 0)

    def test_valid_substitution_exposure_and_off_share(self):
        def extra(emit):
            emit(1, "5:00", "Substitution", "A", "A6", "A1")
            emit(1, "4:00", "Jump Shot", "A", "A2", text="A2 makes jump shot", value=2)
            emit(1, "3:00", "Substitution", "A", "A1", "A6")
        rows, boxes = fixture(extra)
        for box in boxes:
            if box["athlete_id"] == "A1": box["minutes"] = 38
            if box["athlete_id"] == "A6": box.update(minutes=2, did_not_play=False)
        result = parse_game(rows, boxes)
        self.assertTrue(result["qc"]["stints"], result["qc"])
        self.assertEqual(result["players"]["A6"]["court_minutes"], 2)
        self.assertEqual(result["players"]["A2"]["on_off"]["A1"]["off"]["player_shots"], 1)

    def test_chronological_play_number_over_revision_sequence(self):
        rows, boxes = fixture()
        rows[0]["sequence_number"] = 900
        parsed = parse_game(rows, boxes)
        self.assertTrue(parsed["qc"]["event_ordering"])
        rows[0]["game_play_number"] = rows[1]["game_play_number"]
        self.assertFalse(parse_game(rows, boxes)["qc"]["possessions"])

    def test_explicit_dnp_nulls_and_missing_game_are_distinct(self):
        rows, boxes = fixture()
        for box in boxes:
            if box["did_not_play"]:
                for field in ("minutes", "points", "rebounds", "assists", "three_point_field_goals_made"):
                    box[field] = None
        self.assertTrue(parse_game(rows, boxes)["qc"]["box_identity"])
        _, quality = parse_games(rows, boxes, requested_game_ids=["g", "missing"])
        self.assertEqual(len(quality), 2)
        self.assertFalse(quality[1]["possessions"])

    def test_late_score_correction_and_future_season_fail_closed(self):
        rows, boxes = fixture()
        rows[-1]["home_score"] = 0  # stale non-scoring scoreboard field
        self.assertTrue(parse_game(rows, boxes)["qc"]["foul_margin"])
        boxes[0]["points"] += 2
        self.assertFalse(parse_game(rows, boxes)["qc"]["foul_margin"])
        rows[0]["season"] = 2026
        with self.assertRaisesRegex(ValueError, "Protected"):
            parse_game(rows, boxes)

    def test_sample_is_identity_only_and_stable(self):
        schedule = [dict(game_id=str(i), season=2024, score=i) for i in range(30)]
        selected = select_pilot_games(schedule, 2024)
        self.assertEqual(len(selected), 20)
        self.assertEqual(selected, select_pilot_games(list(reversed(schedule)), 2024))


if __name__ == "__main__":
    unittest.main()
