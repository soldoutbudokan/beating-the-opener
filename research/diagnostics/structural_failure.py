"""Describe two completed structural failures without fitting or repricing.

Only saved 2025 forecast bytes are read. Existing receipts bind those bytes to
the already reproduced distributions. This diagnostic verifies that binding
and the frozen comparison population; it does not repeat source reconstruction.
Fixed market/mean cells describe where error occurred, never select a model.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
from time import perf_counter

from research.engine.review import review_structural
from research.engine.scoring import MEAN_BUCKETS, PROBABILITY_FLOOR, validate_rows

ROOT = Path(__file__).resolve().parents[2]
MARKETS = ("points", "rebounds", "assists", "threes")
SCENARIOS = ("8h", "24h")
BUDGET_SECONDS = 600
FIELDS = (
    "game_id", "player_id", "team_id", "market", "quote_id", "book", "date",
    "line", "over_odds", "under_odds", "over_at", "under_at", "quote_available_at",
    "tip_at", "as_of", "p_over", "p_push", "p_under", "p_over_nonpush",
    "p_open_nonpush", "actual", "actual_minutes", "void", "p_dnp",
    "minutes_values", "minutes_probs", "count_p_actual", "model_mean", "model_median",
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def average(values):
    values = list(values)
    return math.fsum(values) / len(values) if values else None


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def near(left, right, label):
    require(math.isclose(left, right, rel_tol=1e-10, abs_tol=1e-10), label)


def moments(row):
    """Law of total variance for the SAVED conditional-on-playing mixture."""
    distribution = row["distribution"]
    require(distribution["schema"] == "structural-forecast-v1", "Unknown distribution")
    for key in ("p_dnp", "minutes_values", "minutes_probs"):
        require(row[key] == distribution[key], "Saved distribution and row disagree: " + key)
    values, weights = row["minutes_values"], row["minutes_probs"]
    require(len(values) == len(weights) > 0, "Missing minutes mixture")
    require(all(isinstance(x, (int, float)) and not isinstance(x, bool) and
                math.isfinite(x) and x > 0 for x in values), "Invalid minutes support")
    require(all(isinstance(w, (int, float)) and not isinstance(w, bool) and
                math.isfinite(w) and 0 <= w <= 1 for w in weights), "Invalid mixture weight")
    near(math.fsum(weights), 1., "Mixture weights do not sum to one")
    component = distribution["count_components"][row["market"]]
    means, fano = component["means"], component["fano"]
    require(len(means) == len(values), "Count/minutes mixture lengths disagree")
    require(all(isinstance(x, (int, float)) and not isinstance(x, bool) and
                math.isfinite(x) and x > 0 for x in means), "Invalid count mean")
    require(isinstance(fano, (int, float)) and not isinstance(fano, bool) and
            math.isfinite(fano) and 1 <= fano <= 2, "Invalid registered Fano factor")
    minute_mean = math.fsum(v * w for v, w in zip(values, weights))
    minute_variance = math.fsum(w * (v - minute_mean) ** 2 for v, w in zip(values, weights))
    count_mean = math.fsum(m * w for m, w in zip(means, weights))
    near(count_mean, row["model_mean"], "Saved count mean differs from mixture")
    rate = count_mean / minute_mean
    for m, v in zip(means, values):
        near(m / v, rate, "Mean-error identity needs the saved constant per-minute rate")
    within = fano * count_mean
    between = math.fsum(w * (m - count_mean) ** 2 for m, w in zip(means, weights))
    return {"minutes_mean": minute_mean, "minutes_variance": minute_variance,
            "count_mean": count_mean, "count_variance": within + between,
            "count_within_minutes_variance": within,
            "count_between_minutes_variance": between, "per_minute_rate": rate}


def compact_row(row):
    require(row.get("market") in MARKETS, "Unexpected market")
    require(str(row.get("date", "")).startswith("2025-"), "Only the completed 2025 comparison is allowed")
    out = {key: row[key] for key in FIELDS if key in row}
    out["diagnostic_moments"] = moments(row)
    return out


def earliest(rows, keys):
    result = {}
    for row in sorted(rows, key=lambda r: (r["quote_available_at"], r["quote_id"])):
        result.setdefault(tuple(row[k] for k in keys), row)
    return list(result.values())


def minutes_summary(rows):
    rows = earliest(rows, ("game_id", "player_id"))
    played = [r for r in rows if not r["void"]]
    errors = [r["diagnostic_moments"]["minutes_mean"] - r["actual_minutes"] for r in played]
    variance = [r["diagnostic_moments"]["minutes_variance"] for r in played]
    return {
        "unique_player_games": len(rows), "played": len(played), "dnp": len(rows) - len(played),
        "selection": "earliest quote_available_at then quote_id per player-game",
        "selected_quote_ids": [r["quote_id"] for r in rows],
        "participation": {
            "predicted_dnp": average(r["p_dnp"] for r in rows),
            "observed_dnp": average(float(r["void"]) for r in rows),
            "brier": average((r["p_dnp"] - float(r["void"])) ** 2 for r in rows),
        },
        "conditional_on_playing": {
            "n": len(played),
            "predicted_minutes_mean": average(r["diagnostic_moments"]["minutes_mean"] for r in played),
            "observed_minutes_mean": average(r["actual_minutes"] for r in played),
            "bias_predicted_minus_observed": average(errors),
            "mae": average(abs(e) for e in errors), "mse": average(e * e for e in errors),
            "mean_predicted_variance": average(variance),
            "squared_error_to_predicted_variance": ratio(math.fsum(e * e for e in errors), math.fsum(variance)),
        },
    }


def count_summary(rows):
    rows = earliest(rows, ("game_id", "player_id", "market"))
    played = [r for r in rows if not r["void"]]
    errors, minutes_terms, production_terms, variances = [], [], [], []
    for row in played:
        m = row["diagnostic_moments"]
        error = m["count_mean"] - row["actual"]
        minute_term = m["per_minute_rate"] * (m["minutes_mean"] - row["actual_minutes"])
        production_term = m["per_minute_rate"] * row["actual_minutes"] - row["actual"]
        near(error, minute_term + production_term, "Count error identity does not close")
        errors.append(error)
        minutes_terms.append(minute_term)
        production_terms.append(production_term)
        variances.append(m["count_variance"])
    return {
        "unique_player_game_markets": len(rows), "played_including_push": len(played),
        "voids": len(rows) - len(played),
        "selected_quote_ids": [r["quote_id"] for r in rows],
        "predicted_mean": average(r["model_mean"] for r in played),
        "observed_mean": average(r["actual"] for r in played),
        "bias_predicted_minus_observed": average(errors),
        "mae": average(abs(e) for e in errors), "mse": average(e * e for e in errors),
        "mean_predicted_variance": average(variances),
        "squared_error_to_predicted_variance": ratio(math.fsum(e * e for e in errors), math.fsum(variances)),
        "mean_within_minutes_variance": average(r["diagnostic_moments"]["count_within_minutes_variance"] for r in played),
        "mean_between_minutes_variance": average(r["diagnostic_moments"]["count_between_minutes_variance"] for r in played),
        "mean_error_identity": {
            "minutes_term_mean": average(minutes_terms),
            "realized_production_term_mean": average(production_terms),
            "minutes_term_mean_square": average(x * x for x in minutes_terms),
            "realized_production_term_mean_square": average(x * x for x in production_terms),
            "twice_cross_term_mean": average(2 * a * b for a, b in zip(minutes_terms, production_terms)),
            "meaning": "Exact algebra on saved forecasts and realized outcomes; neither term is a causal effect or an evaluated forecast with known minutes.",
        },
    }


def loss(probability, outcome):
    return -math.log(max(PROBABILITY_FLOOR, probability if outcome else 1 - probability))


def quote_summary(rows, total_settled):
    played = [r for r in rows if not r["void"]]
    settled = [r for r in played if r["actual"] != r["line"]]
    predicted = [r["p_over_nonpush"] for r in settled]
    observed = [float(r["actual"] > r["line"]) for r in settled]
    model = [loss(p, y) for p, y in zip(predicted, observed)]
    opener = [loss(r["p_open_nonpush"], y) for r, y in zip(settled, observed)]
    differences = [a - b for a, b in zip(model, opener)]
    return {
        "quotes": len(rows), "voids": len(rows) - len(played),
        "pushes": len(played) - len(settled), "settled_nonpush": len(settled),
        "predicted_over": average(predicted), "observed_over": average(observed),
        "over_gap_predicted_minus_observed": average(p - y for p, y in zip(predicted, observed)),
        "model_log_loss": average(model), "opener_log_loss": average(opener),
        "paired_excess_log_loss": average(differences),
        "paired_excess_log_loss_sum": math.fsum(differences),
        "contribution_to_overall_excess_log_loss": ratio(math.fsum(differences), total_settled),
    }


def bucket(row, low, high):
    return low <= row["model_mean"] < high


def describe(rows):
    """All quote-loss rows retained; count selection occurs BEFORE cell splits."""
    rows = validate_rows(rows)
    require(all(r["date"].startswith("2025-") for r in rows), "Protected/new years are not allowed")
    require(all(r["market"] in MARKETS for r in rows), "Unexpected market")
    settled_n = sum(not r["void"] and r["actual"] != r["line"] for r in rows)
    unique_counts = earliest(rows, ("game_id", "player_id", "market"))
    output = {"quote_totals": quote_summary(rows, settled_n), "minutes": minutes_summary(rows),
              "quote_loss_by_mean_bucket": [], "markets": {}}
    for low, high in zip(MEAN_BUCKETS, MEAN_BUCKETS[1:]):
        output["quote_loss_by_mean_bucket"].append({"lower": low, "upper": None if math.isinf(high) else high,
            "quotes": quote_summary([r for r in rows if bucket(r, low, high)], settled_n)})
    for market in MARKETS:
        quotes = [r for r in rows if r["market"] == market]
        counts = [r for r in unique_counts if r["market"] == market]
        cells = []
        for low, high in zip(MEAN_BUCKETS, MEAN_BUCKETS[1:]):
            qs = [r for r in quotes if bucket(r, low, high)]
            cs = [r for r in counts if bucket(r, low, high)]
            cells.append({"lower": low, "upper": None if math.isinf(high) else high,
                          "quotes": quote_summary(qs, settled_n), "counts": count_summary(cs),
                          "minutes": minutes_summary(cs)})
        output["markets"][market] = {"quotes": quote_summary(quotes, settled_n),
            "counts": count_summary(counts), "minutes": minutes_summary(counts), "mean_buckets": cells}
    contribution = math.fsum(v["quotes"]["paired_excess_log_loss_sum"] for v in output["markets"].values())
    near(contribution, output["quote_totals"]["paired_excess_log_loss_sum"], "Market contributions do not close")
    cells = [c for m in output["markets"].values() for c in m["mean_buckets"]]
    require(sum(c["quotes"]["quotes"] for c in cells) == len(rows), "Cells dropped a quote")
    require(sum(c["counts"]["unique_player_game_markets"] for c in cells) == len(unique_counts), "Cells duplicated a count outcome")
    near(math.fsum(c["quotes"]["paired_excess_log_loss_sum"] for c in cells), contribution, "Cell contributions do not close")
    return output


def population(rows):
    # Clocks for observations may differ across the 8h/24h sensitivity. Quotes,
    # grades and all identities may not. No filter changes this fixed population.
    keys = ("quote_id", "game_id", "player_id", "team_id", "market", "book", "date",
            "line", "over_odds", "under_odds", "over_at", "under_at", "quote_available_at",
            "tip_at", "actual", "actual_minutes", "void")
    return sorted([tuple(r.get(k) for k in keys) for r in rows])


def read_run(path, deadline):
    path = Path(path)
    result = review_structural(path / "results.json", path / "receipt.json")
    require(result["status"] == "FAIL" and result["decision"] == "stop", "Only completed failed runs are allowed")
    recipe = json.loads((path / "recipe.json").read_text())
    lock = json.loads((path / "evaluation_lock.json").read_text())
    fit = json.loads((path / "fit.json").read_text())
    recipe_sha = digest(path / "recipe.json")
    require(lock["attempt"] == result["attempt"] and lock["recipe_hash"] == recipe["recipe_hash"] and
            lock["recipe_sha256"] == recipe_sha and fit["recipes"][str(recipe["shrinkage"]) ] == recipe_sha,
            "Recipe, fit and evaluation lock differ")
    require(recipe["through_season"] == 2024, "Unexpected fit cutoff")
    forecast_sha = digest(path / "forecasts.jsonl.gz")
    groups = defaultdict(list)
    with gzip.open(path / "forecasts.jsonl.gz", "rt") as stream:
        for number, line in enumerate(stream):
            require(perf_counter() <= deadline, "Diagnostic computation budget exhausted")
            record = json.loads(line)
            require(record["scenario"] in SCENARIOS, "Unknown scenario")
            row = record["row"]
            require(row["distribution"]["recipe_hash"] == recipe["recipe_hash"], "Row recipe identity differs")
            groups[record["scenario"]].append(compact_row(row))
    require(set(groups) == set(SCENARIOS), "Missing scenario")
    require(digest(path / "forecasts.jsonl.gz") == forecast_sha, "Forecast bytes changed during reading")
    scores = json.loads((path / "scores.json").read_text())
    summaries, populations = {}, {}
    for scenario in SCENARIOS:
        rows = validate_rows(groups[scenario])
        require(len(rows) == len(groups[scenario]) == 7604, "Frozen quote population changed or duplicated")
        populations[scenario] = population(rows)
        summary = describe(rows)
        q = summary["quote_totals"]
        require((q["settled_nonpush"], q["voids"], q["pushes"]) == (7473, 131, 0), "Frozen grades differ")
        original = scores[scenario]["scores"]
        for saved, computed in ((original["log_loss"], q["model_log_loss"]),
                                (original["opener_log_loss"], q["opener_log_loss"]),
                                (original["comparisons"]["opener"]["estimate"], q["paired_excess_log_loss"]),
                                (original["calibration"]["gap"], q["over_gap_predicted_minus_observed"])):
            near(saved, computed, "Descriptive totals differ from the completed evaluation")
        require(original["minutes"]["n"] == summary["minutes"]["unique_player_games"], "Player-game population differs")
        summaries[scenario] = summary
    require(populations["8h"] == populations["24h"], "Timing sensitivity changed quotes or grades")
    return {"attempt": result["attempt"], "recipe_shrinkage": recipe["shrinkage"],
            "recipe_sha256": recipe_sha, "recipe_hash": recipe["recipe_hash"],
            "source_receipt_sha256": digest(path / "receipt.json"), "source_results_sha256": digest(path / "results.json"),
            "source_forecasts_sha256": forecast_sha, "scenarios": summaries}, populations["8h"]


def registration_identity(path, commit):
    path = Path(path).resolve()
    require(re.fullmatch(r"[0-9a-f]{40}", commit) is not None, "Exact published registration commit required")
    require(path.is_relative_to(ROOT), "Registration must be in this repository")
    relative = path.relative_to(ROOT).as_posix()
    saved = subprocess.run(["git", "show", commit + ":" + relative], cwd=ROOT,
                           check=True, capture_output=True).stdout
    require(saved == path.read_bytes(), "Local registration differs from its committed bytes")
    return {"path": relative, "commit": commit, "sha256": digest(path)}


def render(result):
    lines = ["# Why the structural models failed: saved-forecast diagnosis", "",
        "This is descriptive analysis of two failed models on reused 2025 development data. "
        "It does not reopen either evaluation, select a model, estimate a betting edge or establish a cause.", "",
        "| Recipe / timing | Conditional minutes bias / MAE | Excess log loss vs opener |",
        "| --- | ---: | ---: |"]
    for run in result["runs"]:
        for scenario in SCENARIOS:
            s = run["scenarios"][scenario]
            m = s["minutes"]["conditional_on_playing"]
            q = s["quote_totals"]
            lines.append(f"| {run['recipe_shrinkage']} / {scenario} | {m['bias_predicted_minus_observed']:+.3f} / {m['mae']:.3f} minutes | {q['paired_excess_log_loss']:+.6f} |")
    lines += ["", "Positive excess loss means worse probabilities than the opener. Conditional minutes "
        "exclude nonparticipants; their MAE therefore differs from the original unconditional minutes score.", "",
        "The count-error identity separates a minutes term from realized production at the predicted rate. "
        "Those terms can offset one another and contain game-level noise; their squared errors include a cross term. "
        "They are not independent causal contributions or predictions using known actual minutes.", "",
        "Count variance equals expected within-minute variance plus variance across the saved minutes mixture. "
        "The squared-error/variance ratio is descriptive, not a new pass/fail gate. "
        "All fixed cells, including empty cells, appear in results.json. No cutpoints were selected from outcomes.", "",
        "DNP probabilities are reported separately: altering them alone does not change these conditional prop probabilities. "
        "No model was fitted, forecast repriced, uncertainty interval recomputed or protected 2026 row read.", ""]
    return "\n".join(lines)


def run_diagnostic(paths, output, registration, commit):
    require(len(paths) == 2 and len({Path(p).resolve() for p in paths}) == 2, "Exactly two distinct completed runs required")
    reg = registration_identity(registration, commit)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    deadline = perf_counter() + BUDGET_SECONDS
    runs, reference = [], None
    for path in paths:
        run, cohort = read_run(path, deadline)
        require(reference is None or reference == cohort, "Recipes changed the fixed comparison population")
        reference = cohort
        runs.append(run)
    require({r["attempt"] for r in runs} == {1, 2} and
            {r["recipe_shrinkage"] for r in runs} == {1, 2}, "Both original frozen recipes required")
    runs.sort(key=lambda r: r["attempt"])
    require(perf_counter() <= deadline, "Diagnostic computation budget exhausted")
    result = {"schema": "structural-failure-diagnosis-v1", "evidence_kind": "descriptive-reused-development",
              "research_year": 2025, "registration": reg, "budget_seconds": BUDGET_SECONDS,
              "new_models_fit": 0, "new_forecasts": 0, "protected_rows_read": 0,
              "selection": "original quote loss weights; earliest quote per player-game for minutes and per player-game-market for counts",
              "runs": runs}
    (output / "results.json").write_bytes(encoded(result))
    (output / "note.md").write_text(render(result))
    implementation = [Path(__file__), ROOT / "research/engine/scoring.py", ROOT / "research/engine/review.py"]
    receipt = {"schema": "structural-failure-diagnosis-receipt-v1", "status": "complete", "registration": reg,
        "implementation": [{"path": p.resolve().relative_to(ROOT).as_posix(), "sha256": digest(p)} for p in implementation],
        "artifacts": [{"path": name, "sha256": digest(output / name)} for name in ("results.json", "note.md")],
        "verification": "Original complete receipts and artifact/code hashes verified; recipe/lock binding, fixed rows, grades and summary arithmetic match. Previous full forecast/source reproduction is not repeated."}
    (output / "receipt.json").write_bytes(encoded(receipt))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--registration-commit", required=True)
    args = parser.parse_args(argv)
    run_diagnostic(args.run, args.output, args.registration, args.registration_commit)
    print("Completed descriptive diagnostic; no fitted or promoted model.")


if __name__ == "__main__":
    main()
