"""Source corruption and complete count-denominator regression checks."""
from dataclasses import replace
import unittest

import pandas as pd

from research.engine.sources import HistoricalSources
from research.wnba_v2 import sources
from research.wnba_v2.comparison import source_check, count_comparison
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
        self.assertEqual(len(result["hard_team_conflicts"]), 1)

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
