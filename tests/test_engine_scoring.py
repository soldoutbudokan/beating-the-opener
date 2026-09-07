"""Synthetic arithmetic, correlated-date resampling, selection and tamper checks."""
from copy import deepcopy
import math
import unittest

from research.engine.scoring import (
    build_report, date_bootstrap, discrete_crps, evaluate_gates,
    paired_comparison, score_bets, score_forecasts, select_bets,
    validate_rows, verify_saved_report,
)


def forecast(quote_id="q1", game_id="g1", player_id="p1", day=1, **updates):
    row = {
        "quote_id": quote_id, "game_id": game_id, "player_id": player_id,
        "team_id": "t1", "market": "points", "book": "book1", "date": f"2025-06-{day:02}",
        "line": 5., "over_odds": 2., "under_odds": 2.,
        "over_at": f"2025-06-{day:02}T12:00:00Z", "under_at": f"2025-06-{day:02}T12:00:00Z",
        "quote_available_at": f"2025-06-{day:02}T12:00:00Z", "as_of": f"2025-06-{day:02}T12:00:00Z",
        "tip_at": f"2025-06-{day:02}T23:00:00Z", "p_over": .6, "p_push": .2, "p_under": .2,
        "actual": 6, "actual_minutes": 20., "void": False,
        "p_dnp": .1, "minutes_values": [10., 30.], "minutes_probs": [.5, .5],
        "count_p_actual": .3, "model_mean": 6., "model_median": 6.,
        "baseline_p_over_nonpush": .5, "baseline_count_p_actual": .2,
        "close_p_over_nonpush": .55,
        "baseline_p_dnp": 0., "baseline_minutes_values": [10., 30.], "baseline_minutes_probs": [.5, .5],
    }
    row.update(updates)
    return row


class ForecastScoringTests(unittest.TestCase):
    def test_integer_push_void_and_conditional_scoring(self):
        rows = [forecast(), forecast("q2", "g2", day=2, actual=5, count_p_actual=.2),
                forecast("q3", "g3", day=3, actual=None, actual_minutes=0., void=True,
                         count_p_actual=None, baseline_count_p_actual=None)]
        scores = score_forecasts(rows, resamples=200)
        self.assertEqual((scores["quotes"], scores["settled_nonpush"], scores["pushes"], scores["voids"]), (3, 1, 1, 1))
        self.assertAlmostEqual(scores["log_loss"], -math.log(.75))
        self.assertAlmostEqual(scores["brier"], .25 ** 2)
        self.assertAlmostEqual(scores["calibration"]["predicted"], .75)
        self.assertAlmostEqual(scores["count_by_market"]["points"]["nll"], -(math.log(.3) + math.log(.2)) / 2)
        self.assertEqual(scores["count_by_market"]["points"]["n"], 2)
        self.assertEqual(scores["minutes"]["n"], 3)
        self.assertAlmostEqual(scores["minutes"]["participation"]["predicted_dnp"], .1)
        self.assertAlmostEqual(scores["minutes"]["participation"]["observed_dnp"], 1/3)
        self.assertAlmostEqual(scores["minutes"]["participation"]["brier"], (.01+.01+.81)/3)

    def test_crps_known_distribution_and_duplicate_market(self):
        self.assertAlmostEqual(discrete_crps([0, 10], [.5, .5], 5), 2.5)
        self.assertAlmostEqual(discrete_crps([10], [1], 7), 3.)
        first = forecast()
        second = forecast("q2", market="assists", line=2.5, p_over=.75, p_push=0, p_under=.25, actual=3,
                          model_mean=3., model_median=3.)
        result = score_forecasts([second, first], resamples=200)
        self.assertEqual(result["minutes"]["n"], 1)
        self.assertAlmostEqual(result["minutes"]["crps"], 5.15)
        self.assertAlmostEqual(result["minutes"]["mae"], 2.)
        self.assertAlmostEqual(result["minutes"]["baseline"]["crps"], 5.)
        self.assertAlmostEqual(result["minutes"]["paired"]["crps"]["estimate"], .15)
        self.assertEqual(result["minutes"]["quote_ids"], ["q1"])

    def test_all_probability_and_mean_bucket_boundaries(self):
        rows = [forecast(f"q{i}", f"g{i}", day=i + 1, model_mean=mean,
                         p_over=p, p_push=0., p_under=1 - p, line=5.5,
                         count_p_actual=min(p, .1))
                for i, (mean, p) in enumerate(zip([0., 3., 6., 10., 15., 20.], [0., .1, .3, .5, .9, 1.]))]
        result = score_forecasts(rows, resamples=100)
        self.assertEqual([b["n"] for b in result["calibration"]["by_model_mean"]], [1] * 6)
        self.assertEqual(sum(b["n"] for b in result["calibration"]["by_probability"]), 6)
        self.assertTrue(math.isfinite(result["log_loss"]))

    def test_invalid_probabilities_odds_outcomes_clocks_and_roster_rejected(self):
        invalid = [
            {"p_over": float("nan")}, {"p_over": .7}, {"over_odds": -110},
            {"over_odds": 1}, {"actual": None}, {"actual_minutes": None},
            {"actual_minutes": 0}, {"void": 1}, {"actual": 1.5},
            {"line": 5.5}, {"p_over_nonpush": .8}, {"p_open_nonpush": .7},
            {"count_p_actual": .8}, {"actual": 5, "count_p_actual": .1},
            {"as_of": "2025-06-01T23:00:00Z"}, {"as_of": "2025-06-01T12:00:00"},
            {"under_at": "2025-06-01T13:00:00Z"}, {"date": "2025-06-02"},
            {"minutes_probs": [.4, .4]}, {"minutes_values": [0., 10.]},
        ]
        for update in invalid:
            with self.subTest(update=update), self.assertRaises(ValueError):
                validate_rows([forecast(**update)])
        for update in ({"actual": 7}, {"actual_minutes": 21}, {"team_id": "t2"}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                validate_rows([forecast(), forecast("q2", book="book2", **update)])

    def test_identity_deduplication_and_partial_comparator_rejected(self):
        row = forecast()
        self.assertEqual(validate_rows([row, deepcopy(row)]), validate_rows([row]))
        with self.assertRaisesRegex(ValueError, "Conflicting duplicate"):
            validate_rows([row, forecast(over_odds=2.1)])
        with self.assertRaisesRegex(ValueError, "Conflicting duplicate"):
            validate_rows([row, forecast("different-id")])
        absent = forecast("q2", "g2", day=2)
        del absent["baseline_p_over_nonpush"]
        with self.assertRaisesRegex(ValueError, "missing on part"):
            validate_rows([row, absent])

    def test_timestamp_fraction_order_and_first_qualifying_quote(self):
        early = forecast("q-early", over_at="2025-06-01T12:00:00.000000Z")
        later = forecast("q-later", market="assists", p_over=.9, p_push=.05, p_under=.05,
                         over_at="2025-06-01T12:00:00.500000Z", under_at="2025-06-01T12:00:00.500000Z",
                         quote_available_at="2025-06-01T12:00:00.500000Z", as_of="2025-06-01T12:00:00.500000Z")
        self.assertEqual(select_bets([later, early], .1)[0]["quote_id"], "q-early")
        # At .5 the early quote does not qualify; later quote must be considered.
        self.assertEqual(select_bets([early, later], .5)[0]["quote_id"], "q-later")
        self.assertEqual(select_bets([forecast(p_over=.5, p_push=0, p_under=.5)], 0), [])

    def test_grade_refunds_voids_and_same_player_game_controls(self):
        rows = [forecast(), forecast("q2", "g2", day=2, actual=5, count_p_actual=.2),
                forecast("q3", "g3", day=3, actual=None, actual_minutes=0., void=True,
                         count_p_actual=None, baseline_count_p_actual=None),
                forecast("q4", "g4", day=4, actual=2, count_p_actual=.1)]
        result = score_bets(rows, .1, resamples=200)
        candidate = result["candidate"]
        self.assertEqual((candidate["bets"], candidate["wins"], candidate["losses"], candidate["pushes"], candidate["voids"]), (4, 1, 1, 1, 1))
        self.assertEqual((candidate["stake"], candidate["refund"], candidate["profit"]), (3., 2., 0.))
        self.assertAlmostEqual(result["matched_claimed_profit"], 1.08)
        for selection in result["selections"]:
            self.assertEqual(selection["candidate"]["settled_stake"], selection["always_under"]["settled_stake"])
            self.assertEqual(selection["candidate"]["settled_stake"], selection["placebo"]["settled_stake"])
        self.assertEqual(result, score_bets(list(reversed(rows)), .1, resamples=200))

    def test_paired_comparison_is_same_cohort_and_zero_for_identical(self):
        rows = [forecast(baseline_p_over_nonpush=.6 / .8),
                forecast("q2", "g2", day=2, baseline_p_over_nonpush=.6 / .8, actual=2, count_p_actual=.1)]
        result = paired_comparison(rows, resamples=200)
        self.assertEqual(result["n"], 2)
        self.assertAlmostEqual(result["estimate"], 0.)
        self.assertEqual(result["ci95"], [0., 0.])

    def test_missing_same_line_close_reports_common_subset(self):
        rows = [forecast(), forecast("q2", "g2", day=2, close_p_over_nonpush=None)]
        close = score_forecasts(rows, resamples=100)["comparisons"]["close"]
        self.assertEqual((close["n"], close["missing_comparator"]), (1, 1))
        self.assertIsNone(close["ci95"])
        rows[0]["close_p_over_nonpush"] = None
        close = score_forecasts(rows, resamples=100)["comparisons"]["close"]
        self.assertEqual((close["n"], close["missing_comparator"]), (0, 2))
        self.assertIsNone(close["estimate"])

    def test_dnp_mass_scales_expected_profit_and_threshold(self):
        self.assertEqual(select_bets([forecast(p_dnp=.9)], .1), [])
        selected = select_bets([forecast(p_dnp=.9)], .01)
        self.assertAlmostEqual(selected[0]["claimed_ev"], .04)

    def test_whole_date_bootstrap_preserves_within_date_correlation(self):
        # Cloning observations within the same date cannot increase precision.
        single = date_bootstrap(["2025-06-01", "2025-06-02"], [1., -1.])
        repeated = date_bootstrap(["2025-06-01"] * 100 + ["2025-06-02"] * 100, [1.] * 100 + [-1.] * 100)
        self.assertEqual(single, repeated)
        self.assertEqual(single["ci95"], [-1., 1.])
        self.assertEqual(single["resamples"], 10000)
        # Unequal cluster sizes require ratio of sums, not average date means.
        weighted = date_bootstrap(["a", "a", "b"], [1, 1, -1], resamples=200)
        self.assertAlmostEqual(weighted["estimate"], 1 / 3)
        self.assertIsNone(date_bootstrap(["a"], [1], resamples=200)["ci95"])
        self.assertIsNone(date_bootstrap(["a", "b"], [0, 0], [0, 0], resamples=200)["estimate"])

    def test_empty_cohorts_abstain_without_nan(self):
        report = build_report([], resamples=100)
        self.assertIsNone(report["scores"]["log_loss"])
        self.assertEqual(report["economics"]["0.05"]["selections"], [])
        self.assertEqual(verify_saved_report([], report, resamples=100)["status"], "PASS")

    def test_saved_report_reproduces_and_detects_tampering(self):
        rows = [forecast(), forecast("q2", "g2", day=2, actual=2, count_p_actual=.1)]
        report = build_report(rows, resamples=100)
        self.assertEqual(verify_saved_report(rows, report, resamples=100)["status"], "PASS")
        self.assertEqual(build_report(rows + [deepcopy(rows[0])], resamples=100), report)
        for mutate in (lambda r: r["scores"].update(log_loss=.1),
                       lambda r: r["economics"]["0.05"]["selections"][0]["candidate"].update(stake=2),
                       lambda r: r["economics"]["0.05"]["candidate"]["roi"].update(ci95=[1., 2.]),
                       lambda r: r["economics"]["0.05"]["selections"][0].update(placebo_uniform=.5)):
            changed = deepcopy(report)
            mutate(changed)
            with self.assertRaises(ValueError):
                verify_saved_report(rows, changed, resamples=100)
        with self.assertRaises(ValueError):
            verify_saved_report(rows, report, resamples=101)

    def test_gates_reject_absent_buckets_close_and_constant_gain_tripwire(self):
        scores = score_forecasts([forecast(), forecast("q2", "g2", day=2)], resamples=100)
        kwargs = dict(calibration_overall_limit=.015, calibration_bucket_limit=.025,
                      log_loss_limit=.687662, expected_settled=2, expected_markets=["points"],
                      close_gain_tripwire=.001, close_t_tripwire=3.)
        result = evaluate_gates(scores, **kwargs)
        self.assertFalse(result["gates"]["calibration"])
        self.assertTrue(result["leakage_investigation_required"])
        del scores["comparisons"]["close"]
        self.assertFalse(evaluate_gates(scores, **kwargs)["gates"]["close_tripwire_clear"])


if __name__ == "__main__":
    unittest.main()
