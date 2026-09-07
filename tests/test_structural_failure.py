"""Synthetic checks for descriptive attribution, fixed rows and no-overwrite."""
from copy import deepcopy
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from research.diagnostics.structural_failure import (
    compact_row, count_summary, describe, encoded, minutes_summary, moments,
    population, quote_summary, read_run, registration_identity, run_diagnostic,
)
from research.engine.scoring import validate_rows


def forecast(quote_id="q1", game_id="g1", market="points", **updates):
    row = {
        "quote_id": quote_id, "game_id": game_id, "player_id": "p1",
        "team_id": "t1", "market": market, "book": "book1", "date": "2025-06-01",
        "line": 5., "over_odds": 2., "under_odds": 2.,
        "over_at": "2025-06-01T12:00:00Z", "under_at": "2025-06-01T12:00:00Z",
        "quote_available_at": "2025-06-01T12:00:00Z", "as_of": "2025-06-01T12:00:00Z",
        "tip_at": "2025-06-01T23:00:00Z", "p_over": .6, "p_push": .2, "p_under": .2,
        "actual": 6, "actual_minutes": 20., "void": False,
        "p_dnp": .1, "minutes_values": [10., 30.], "minutes_probs": [.5, .5],
        "count_p_actual": .3, "model_mean": 6., "model_median": 6.,
    }
    row.update(updates)
    minute_mean = sum(v * p for v, p in zip(row["minutes_values"], row["minutes_probs"]))
    rate = row["model_mean"] / minute_mean
    row["distribution"] = {
        "schema": "structural-forecast-v1", "recipe_hash": "frozen",
        "p_dnp": row["p_dnp"], "minutes_values": row["minutes_values"], "minutes_probs": row["minutes_probs"],
        "count_components": {market: {"means": [v * rate for v in row["minutes_values"]], "fano": 1.5}},
    }
    return row


def prepared(*rows):
    return validate_rows([compact_row(r) for r in rows])


class StructuralFailureTests(unittest.TestCase):
    def test_total_variance_is_within_plus_between_minutes(self):
        result = moments(forecast())
        self.assertEqual(result["minutes_mean"], 20)
        self.assertEqual(result["minutes_variance"], 100)
        self.assertEqual(result["count_mean"], 6)
        self.assertAlmostEqual(result["per_minute_rate"], .3)
        self.assertEqual(result["count_within_minutes_variance"], 9)
        self.assertEqual(result["count_between_minutes_variance"], 9)
        self.assertEqual(result["count_variance"], 18)

    def test_error_identity_retains_offsetting_terms_and_cross_term(self):
        # Predict 6 counts in 20 minutes; observe 5 in 10 minutes.
        # Total error +1 = minutes term +3 plus realized-production term -2.
        result = count_summary(prepared(forecast(actual=5, actual_minutes=10, count_p_actual=.2)))
        identity = result["mean_error_identity"]
        self.assertEqual(result["bias_predicted_minus_observed"], 1)
        self.assertEqual(identity["minutes_term_mean"], 3)
        self.assertEqual(identity["realized_production_term_mean"], -2)
        self.assertEqual(identity["minutes_term_mean_square"], 9)
        self.assertEqual(identity["realized_production_term_mean_square"], 4)
        self.assertEqual(identity["twice_cross_term_mean"], -12)
        self.assertEqual(result["mse"], 9 + 4 - 12)

    def test_dnp_changes_participation_only_not_conditional_prop_diagnosis(self):
        low = describe(prepared(forecast(p_dnp=.01)))
        high = describe(prepared(forecast(p_dnp=.95)))
        self.assertNotEqual(low["minutes"]["participation"], high["minutes"]["participation"])
        self.assertEqual(low["minutes"]["conditional_on_playing"], high["minutes"]["conditional_on_playing"])
        self.assertEqual(low["quote_totals"], high["quote_totals"])
        self.assertEqual(low["markets"]["points"]["counts"], high["markets"]["points"]["counts"])

    def test_conditional_minutes_exclude_void_and_deduplicate_markets(self):
        playing = forecast(actual_minutes=18)
        duplicate = forecast("q2", market="assists", actual_minutes=18)
        void = forecast("q3", "g2", actual=None, actual_minutes=0., void=True, count_p_actual=None)
        result = describe(prepared(void, duplicate, playing))
        m = result["minutes"]
        self.assertEqual((m["unique_player_games"], m["played"], m["dnp"]), (2, 1, 1))
        self.assertEqual(m["selected_quote_ids"], ["q1", "q3"])
        self.assertEqual(m["conditional_on_playing"]["mae"], 2)
        self.assertEqual(m["participation"]["observed_dnp"], .5)

    def test_push_and_void_preserved_but_excluded_from_binary_loss(self):
        rows = prepared(forecast(), forecast("q2", "g2", actual=5, count_p_actual=.2),
                        forecast("q3", "g3", actual=None, actual_minutes=0, void=True, count_p_actual=None))
        result = describe(rows)
        q = result["quote_totals"]
        self.assertEqual((q["quotes"], q["settled_nonpush"], q["pushes"], q["voids"]), (3, 1, 1, 1))
        self.assertAlmostEqual(q["model_log_loss"], -math.log(.75))
        self.assertAlmostEqual(q["opener_log_loss"], -math.log(.5))
        self.assertEqual(result["markets"]["points"]["counts"]["played_including_push"], 2)

    def test_count_selection_happens_before_buckets_and_uses_quote_clock(self):
        early = forecast("z-early", model_mean=2., model_median=2.)
        late = forecast("a-late", book="book2", model_mean=7., model_median=7.,
                        over_at="2025-06-01T13:00:00Z", under_at="2025-06-01T13:00:00Z",
                        quote_available_at="2025-06-01T13:00:00Z", as_of="2025-06-01T13:00:00Z")
        result = describe(prepared(late, early))["markets"]["points"]
        self.assertEqual(result["quotes"]["quotes"], 2)
        self.assertEqual(result["counts"]["selected_quote_ids"], ["z-early"])
        self.assertEqual(result["mean_buckets"][0]["counts"]["unique_player_game_markets"], 1)
        self.assertEqual(result["mean_buckets"][2]["quotes"]["quotes"], 1)
        self.assertEqual(result["mean_buckets"][2]["counts"]["unique_player_game_markets"], 0)

    def test_every_fixed_bucket_includes_boundary_and_empty_cells(self):
        values = [2., 3., 6., 10., 15., 20.]
        rows = prepared(*(forecast(f"q{i}", f"g{i}", model_mean=value, model_median=int(value))
                          for i, value in enumerate(values)))
        result = describe(rows)
        self.assertEqual([c["quotes"]["quotes"] for c in result["markets"]["points"]["mean_buckets"]], [1] * 6)
        self.assertEqual(len(result["markets"]["assists"]["mean_buckets"]), 6)
        self.assertIsNone(result["markets"]["assists"]["counts"]["mean_predicted_variance"])
        self.assertIsNone(result["markets"]["assists"]["quotes"]["paired_excess_log_loss"])

    def test_loss_decomposition_is_signed_and_normalized_by_whole_cohort(self):
        winner = forecast(p_over=.6, p_push=.2, p_under=.2)
        loser = forecast("q2", "g2", "assists", p_over=.2, p_push=.2, p_under=.6, count_p_actual=.1)
        result = describe(prepared(winner, loser))
        contributions = [m["quotes"]["contribution_to_overall_excess_log_loss"] for m in result["markets"].values()]
        self.assertLess(contributions[0], 0)
        self.assertGreater(contributions[2], 0)
        self.assertAlmostEqual(sum(contributions), result["quote_totals"]["paired_excess_log_loss"])
        self.assertAlmostEqual(contributions[0], (-math.log(.75) + math.log(.5)) / 2)

    def test_invalid_distribution_or_protected_year_refused(self):
        for mutation in ("mean", "rate", "weights", "fano", "dnp", "future"):
            row = forecast()
            if mutation == "mean":
                row["model_mean"] += 1
            elif mutation == "rate":
                row["distribution"]["count_components"]["points"]["means"] = [2., 10.]
            elif mutation == "weights":
                row["minutes_probs"][0] = float("nan")
            elif mutation == "fano":
                row["distribution"]["count_components"]["points"]["fano"] = 3
            elif mutation == "dnp":
                row["distribution"]["p_dnp"] = .8
            else:
                row["date"] = "2026-06-01"
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                compact_row(row)

    def test_conflicting_grade_and_duplicate_identity_refused(self):
        with self.assertRaisesRegex(ValueError, "Conflicting player-game"):
            prepared(forecast(), forecast("q2", book="book2", actual_minutes=21))
        with self.assertRaisesRegex(ValueError, "Conflicting duplicate"):
            prepared(forecast(), forecast("q2"))

    def test_population_ignores_forecast_changes_but_not_grade_changes(self):
        rows = prepared(forecast())
        changed = deepcopy(rows)
        changed[0]["model_mean"] += 1
        changed[0]["as_of"] = "2025-06-01T13:00:00.000000Z"
        self.assertEqual(population(rows), population(changed))
        changed[0]["actual"] += 1
        self.assertNotEqual(population(rows), population(changed))

    def test_tampered_original_receipt_stops_before_forecast_read(self):
        with patch("research.diagnostics.structural_failure.review_structural", side_effect=ValueError("Checksum differs")), \
             patch("research.diagnostics.structural_failure.gzip.open") as opened:
            with self.assertRaisesRegex(ValueError, "Checksum differs"):
                read_run(Path("unread"), float("inf"))
            opened.assert_not_called()

    def test_existing_output_refuses_all_computation(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch("research.diagnostics.structural_failure.registration_identity", return_value={}), \
             patch("research.diagnostics.structural_failure.read_run") as read:
            with self.assertRaises(FileExistsError):
                run_diagnostic(["run1", "run2"], directory, "registration", "a" * 40)
            read.assert_not_called()

    def test_uncommitted_registration_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = root / "plan.md"
            plan.write_text("changed registration")
            with patch("research.diagnostics.structural_failure.ROOT", root), \
                 patch("research.diagnostics.structural_failure.subprocess.run") as git:
                git.return_value.stdout = b"original registration"
                with self.assertRaisesRegex(ValueError, "differs from its committed"):
                    registration_identity(plan, "a" * 40)

    def test_result_serialization_reproduces_with_input_order_reversed(self):
        rows = prepared(forecast(), forecast("q2", "g2", "assists"))
        self.assertEqual(encoded(describe(rows)), encoded(describe(list(reversed(rows)))))


if __name__ == "__main__":
    unittest.main()
