"""Boundary checks for the research baseline and historical quote loader."""
from copy import deepcopy
from types import SimpleNamespace
import unittest

import numpy as np
import pandas as pd
from scipy.stats import poisson

from .baseline import RAW, _next_talent, expected_profit, outcome_probabilities, predict_means
from .benchmark import american_decimal, match_openers, parse_offer


class BaselineBoundaries(unittest.TestCase):
    def test_integer_push_is_neither_win_nor_loss(self):
        p = outcome_probabilities("threes", 3.0, 3.0, {})
        self.assertAlmostEqual(float(p["p_push"]), poisson.pmf(3, 3))
        self.assertAlmostEqual(float(p["p_under"]), poisson.cdf(2, 3))
        self.assertAlmostEqual(float(sum(p.values())), 1.0)
        dec = float(american_decimal(-110))
        ev = expected_profit(p["p_under"], p["p_push"], dec)
        self.assertAlmostEqual(float(ev), .03195014435208915)

    def test_nonnegative_count_support_and_half_lines(self):
        cal = {"sigma": {"points": [5.0, 1.5]}}
        for market, params in [("points", cal), ("threes", {})]:
            p = outcome_probabilities(market, [0.2, 3., 19.], [0., 3.5, 19.], params)
            np.testing.assert_allclose(sum(p.values()), 1)
            self.assertEqual(p["p_under"][0], 0.)
            self.assertEqual(p["p_push"][1], 0.)

    def test_forecasts_do_not_depend_on_batch_or_outcomes(self):
        row = {"min_ewf": 30., "min_ews": 28., "lg_pace_asof": 78.,
               "lg_pts_against_asof": 80., "tm_pace_ew": 79., "opp_pace_ew": 77.,
               "opp_pts_against_ew": 81., "home": 1., "gp": 20.}
        for stat in RAW:
            row.update({f"{stat}_ewf": 4., f"{stat}_ews": 3.5, f"{stat}_rate_ewf": .15})
        target = pd.DataFrame([row])
        other = dict(row, opp_pace_ew=130, actual=3, open_line=3)
        mixed = pd.DataFrame([row] + [other] * 200)
        expected = predict_means(target).iloc[0]
        np.testing.assert_allclose(expected, predict_means(mixed).iloc[0])
        mixed.loc[1:, "actual"] = 4
        np.testing.assert_allclose(expected, predict_means(mixed).iloc[0])

    def test_snapshot_advances_latest_game_observation(self):
        module = SimpleNamespace(curve_level=lambda *args: .5, OFFSEASON_Q_MULT=10.)
        data = pd.DataFrame({"athlete_id": ["a", "a"], "pos": ["G", "G"],
                             "gp": [0, 1], "season_yr": [2024, 2024],
                             "den_poi": [20., 20.], "y_poi": [.5, 1.]})
        states = _next_talent(module, data, {}, {"poi": 1.}, 0., .5, "poi")
        self.assertEqual(states[0], .5)
        self.assertGreater(states[1], .7)
        self.assertLess(states[1], 1.)


def quote_fixture():
    event = {"id": 1, "scheduled": "2025-06-01 23:00:00", "season": 2025,
             "season_type": "REG", "home": "NYL", "visitor": "ATL"}
    offer = {"id": "quote1", "event_id": 1, "market_id": 393, "player_id": 2,
             "participants": [{"player": {"first_name": "Example", "last_name": "Player", "team": "NYL"}}],
             "selections": [{"selection": side, "opening_line": {
                 "line": 12.5, "cost": -110, "book_id": 10,
                 "created": "2025-06-01 12:00:00"}} for side in ["over", "under"]]}
    return offer, event


class QuoteBoundaries(unittest.TestCase):
    def test_source_pair_requires_same_book_time_and_line(self):
        offer, event = quote_fixture()
        self.assertTrue(parse_offer(offer, event)["open_coherent"])
        for field, value, reason in [
            ("book_id", 12, "missing_or_different_books"),
            ("line", 13.5, "missing_or_different_lines"),
            ("created", "2025-06-01 12:00:01", "unsynchronized_openers"),
        ]:
            changed = deepcopy(offer)
            changed["selections"][1]["opening_line"][field] = value
            parsed = parse_offer(changed, event)
            self.assertFalse(parsed["open_coherent"])
            self.assertIn(reason, parsed["rejection_reasons"])

    def test_started_game_and_invalid_prices_are_rejected(self):
        offer, event = quote_fixture()
        for side in offer["selections"]:
            side["opening_line"]["created"] = event["scheduled"]
        self.assertIn("opener_not_before_tip", parse_offer(offer, event)["rejection_reasons"])
        with self.assertRaises(ValueError):
            american_decimal(0)

    def test_outcome_matching_never_uses_neighboring_game(self):
        offer, event = quote_fixture()
        row = {"athlete_id": "a", "game_id": "g", "team_id": "ny", "opponent_team_id": "atl",
               "athlete_display_name": "Example Player", "game_date": "2025-06-02",
               "team_abbreviation": "NY", "opponent_team_abbreviation": "ATL",
               "home_away": "home", "minutes": 30., **{c: 2. for c in RAW.values()}}
        quotes = pd.DataFrame([parse_offer(offer, event)])
        matched, _ = match_openers(quotes, pd.DataFrame([row]))
        self.assertFalse(matched.matched.iloc[0])
        row["game_date"] = "2025-06-01"
        matched, _ = match_openers(quotes, pd.DataFrame([row]))
        self.assertTrue(matched.matched.iloc[0])
        self.assertEqual(matched.actual.iloc[0], 2.)
        self.assertNotIn("points", matched)
        row["opponent_team_abbreviation"] = "CON"
        matched, _ = match_openers(quotes, pd.DataFrame([row]))
        self.assertFalse(matched.matched.iloc[0])


if __name__ == "__main__":
    unittest.main()
