"""Check the saved WNBA research results without fitting or predicting again.

From the repository root::

    python wnba/research/rich_context/tools/verify_saved_results.py

Requires numpy and pandas. Reads the committed forecast/bet CSVs and JSON
reports; it never changes them. If the locally generated prepared.pkl cache
exists, also query its saved 8-hour states to check source chronology. Use
--skip-source-check for the lightweight CSV/JSON checks alone. No downloaded
box scores, odds refresh, fitted model, or 2026 data is needed.

The always-under control was requested AFTER the registered results. This
script verifies that existing diagnostic; it does not register a new strategy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import pickle
import sys

import numpy as np
import pandas as pd

STUDY = Path(__file__).resolve().parents[1]
REPO = STUDY.parents[2]
FAMILIES = ("incumbent", "box_ridge", "rich_ridge", "rich_boost")


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def close(actual, expected, message, tolerance=1e-10):
    require(np.allclose(actual, expected, rtol=0, atol=tolerance), message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def decimal_odds(odds):
    values = np.asarray(odds, float)
    require(np.isfinite(values).all() and (np.abs(values) >= 100).all(), "Invalid stored American odds")
    return 1 + np.where(values > 0, values / 100, 100 / np.abs(values))


def date_bootstrap(dates, numerator, denominator):
    """Independent arithmetic on saved values; resample complete ET dates."""
    days = pd.DataFrame({"date": dates, "num": numerator, "den": denominator})
    days = days.groupby("date", sort=True)[["num", "den"]].sum().to_numpy(float)
    require(len(days) > 1 and days[:, 1].sum() > 0, "Insufficient settled data")
    rng = np.random.default_rng(20260906)
    indices = rng.integers(0, len(days), size=(10000, len(days)))
    draws = days[indices].sum(axis=1)
    draws = draws[draws[:, 1] > 0]
    ratios = draws[:, 0] / draws[:, 1]
    return {"estimate": float(days[:, 0].sum() / days[:, 1].sum()),
            "ci95": np.quantile(ratios, [.025, .975]).tolist()}


def grade(rows, sides):
    sides = np.asarray(sides)
    void = rows.void.to_numpy(bool)
    actual, line = rows.actual.to_numpy(float), rows.open_line.to_numpy(float)
    require(not (~void & ~np.isfinite(actual)).any(), "Unresolved row would be treated as a loss")
    push = (actual == line) & ~void
    wins = np.where(sides == "over", actual > line, actual < line) & ~void
    prices = decimal_odds(np.where(sides == "over", rows.open_over, rows.open_under))
    profit = np.where(void | push, 0., np.where(wins, prices - 1, -1.))
    return profit, (~void).astype(float), wins, push, void


def scores(rows, probability):
    probability = np.clip(np.asarray(probability, float), 1e-8, 1 - 1e-8)
    outcome = rows.actual.gt(rows.open_line).to_numpy(float)
    valid = (~rows.void & rows.actual.notna() & rows.actual.ne(rows.open_line)).to_numpy()
    losses = -(outcome * np.log(probability) + (1 - outcome) * np.log1p(-probability))
    brier = (probability - outcome) ** 2
    return losses, brier, valid


def verify_bets(results_dir, rows, probabilities, family, lag, threshold, expected):
    ev_over = probabilities[:, 0] * (decimal_odds(rows.open_over) - 1) - probabilities[:, 2]
    ev_under = probabilities[:, 2] * (decimal_odds(rows.open_under) - 1) - probabilities[:, 0]
    candidates = rows.copy()
    candidates["checked_ev"] = np.maximum(ev_over, ev_under)
    candidates["checked_side"] = np.where(ev_over >= ev_under, "over", "under")
    selected = candidates[candidates.checked_ev > threshold / 100].copy()
    selected = selected.sort_values(
        ["forecast_at", "checked_ev", "market", "checked_side"],
        ascending=[True, False, True, True], kind="stable")
    selected = selected.drop_duplicates(["game_id", "athlete_id"], keep="first").reset_index(drop=True)
    stored = pd.read_csv(results_dir / f"bets_{lag}_{family}_{threshold}.csv")
    label = f"{lag}/{family}/{threshold}%"
    require(not stored.duplicated(["game_id", "athlete_id"]).any(), f"Repeated player/game: {label}")
    require(not stored.offer_id.duplicated().any(), f"Repeated selected offer: {label}")
    require(set(selected.offer_id) == set(stored.offer_id), f"Chronological selections differ: {label}")
    stored = stored.set_index("offer_id").loc[selected.offer_id].reset_index()
    require((selected.checked_side.to_numpy() == stored.side.to_numpy()).all(), f"Sides differ: {label}")
    close(selected.checked_ev, stored.claimed_ev, f"Expected profit differs: {label}")
    profit, stake, wins, pushes, voids = grade(selected, selected.checked_side)
    close(profit, stored.profit, f"Bet profit differs: {label}")
    close(stake, stored.settled_stake, f"Bet stake differs: {label}")
    require(np.array_equal(pushes, stored.is_push), f"Push flags differ: {label}")
    require(np.array_equal(voids, stored.is_void), f"Void flags differ: {label}")
    counts = {"bets": len(selected), "wins": int(wins.sum()),
              "losses": int((profit < 0).sum()), "pushes": int(pushes.sum()),
              "voids": int(voids.sum()), "over_bets": int(selected.checked_side.eq("over").sum())}
    for key, value in counts.items():
        require(value == expected[key], f"Reported {key} differs: {label}")
    close(profit.sum(), expected["profit"], f"Total profit differs: {label}")
    close(stake.sum(), expected["stake"], f"Total settled stake differs: {label}")
    close(selected.loc[~voids, "checked_ev"].sum(), expected["matched_claimed_profit"],
          f"Expected profit includes wrong population: {label}")
    interval = date_bootstrap(selected.date, profit, stake)
    close(interval["estimate"], expected["roi"]["estimate"], f"ROI differs: {label}")
    close(interval["ci95"], expected["roi"]["ci95"], f"ROI interval differs: {label}")


def verify_control(rows, expected, forecast_path):
    require(digest(forecast_path) == expected["input_forecasts_sha256"], "Control input checksum differs")
    chosen = rows.sort_values(["forecast_at", "market"], kind="stable").drop_duplicates(
        ["game_id", "athlete_id"], keep="first")
    require(chosen.offer_id.tolist() == expected["selection_offer_ids"], "Always-under selections differ")
    profit, stake, wins, pushes, voids = grade(chosen, np.repeat("under", len(chosen)))
    counts = {"input_eligible_quotes": len(rows), "bets": len(chosen), "wins": int(wins.sum()),
              "losses": int((profit < 0).sum()), "pushes": int(pushes.sum()), "voids": int(voids.sum())}
    for key, value in counts.items():
        require(value == expected[key], f"Always-under {key} differs")
    close(profit.sum(), expected["profit"], "Always-under profit differs")
    close(stake.sum(), expected["settled_stake"], "Always-under stake differs")
    require(chosen.market.value_counts().to_dict() == expected["market_mix"], "Control market mix differs")
    interval = date_bootstrap(chosen.date, profit, stake)
    close(interval["estimate"], expected["roi"]["estimate"], "Always-under ROI differs")
    close(interval["ci95"], expected["roi"]["ci95"], "Always-under interval differs")
    return {"profit": float(profit.sum()), **interval}


def verify_sources(rows, cache_dir):
    cache = cache_dir / "prepared.pkl"
    if not cache.exists():
        return "SKIPPED: locally generated prepared.pkl is absent"
    # This cache is generated locally by the research prepare command. No
    # fitted estimator is loaded and no forecast is generated by this check.
    sys.path.insert(0, str(REPO))
    from wnba.research.rich_context.features import query_features

    with cache.open("rb") as handle:
        prepared = pickle.load(handle)
    query = rows[["game_id", "athlete_id", "team_id", "opponent_team_id", "forecast_at", "tip_at", "home"]]
    checked = query_features(prepared["states"], query)
    for column in ("player_source_at", "team_source_at", "opponent_source_at", "league_source_at"):
        require(checked[column].notna().all(), f"Missing eligible source timestamp: {column}")
        require((checked[column] < checked.forecast_at).all(), f"Future source used: {column}")
    same_game = checked.game_id.astype(str).eq(checked.player_source_game_id.astype(str))
    require(not same_game.any(), "Target game's box score used as its own player history")
    return "PASS: 8h saved-state queries; 24h state store was not separately saved"


def verify(results_dir, cache_dir, skip_sources=False):
    summary = json.loads((results_dir / "results.json").read_text())
    receipt = json.loads((results_dir / "evaluation_receipt.json").read_text())
    control = json.loads((results_dir / "control_diagnostics.json").read_text())
    require(receipt["status"] == "complete", "Evaluation receipt is incomplete")
    require(digest(results_dir / "results.json") == receipt["results_sha256"], "Registered result checksum differs")
    require(control["registered_results_sha256"] == receipt["results_sha256"], "Control refers to another result")
    require(control["preregistered"] is False, "Post-results control must remain labeled exploratory")
    checked = {"bet_files_verified": 0, "scenarios": {}}
    main_rows = None
    for lag in ("8h", "24h"):
        forecast_path = results_dir / f"forecasts_{lag}.csv.gz"
        rows = pd.read_csv(forecast_path)
        rows["forecast_at"] = pd.to_datetime(rows.forecast_at, utc=True)
        rows["tip_at"] = pd.to_datetime(rows.tip_at, utc=True)
        require(not rows.duplicated(["event_id", "bp_player_id", "market"]).any(), "Duplicate quote identity")
        require((rows.forecast_at < rows.tip_at).all(), "Quote at or after scheduled tip")
        require(rows.season.eq(2025).all(), "Unexpected season in saved outputs")
        require(len(rows) == summary[lag]["quotes"], "Reported quote count differs")
        opener_ll, opener_bs, valid = scores(rows, rows.p_open)
        close(opener_ll[valid].mean(), summary[lag]["market"]["log_loss"], "Opener log loss differs")
        close(opener_bs[valid].mean(), summary[lag]["market"]["brier"], "Opener Brier score differs")
        losses, model_scores = {}, {}
        for family in FAMILIES:
            p = rows[[f"{family}_p_over", f"{family}_p_push", f"{family}_p_under"]].to_numpy()
            require(np.isfinite(p).all() and (p >= 0).all() and (p <= 1).all(), "Invalid probabilities")
            close(p.sum(axis=1), 1., "Probabilities fail to sum to one")
            conditional = p[:, 0] / (1 - p[:, 1])
            close(conditional, rows[f"{family}_p_over_nonpush"], "Conditional probability differs")
            ll, bs, good = scores(rows, conditional)
            require(np.array_equal(good, valid), "Models do not share the same grading cohort")
            expected = summary[lag]["models"][family]
            close(ll[valid].mean(), expected["log_loss"], f"Log loss differs: {lag}/{family}")
            close(bs[valid].mean(), expected["brier"], f"Brier score differs: {lag}/{family}")
            losses[family], model_scores[family] = ll, float(ll[valid].mean())
            for threshold in (5, 10):
                verify_bets(results_dir, rows, p, family, lag, threshold,
                            expected["economics"][str(threshold / 100)])
                checked["bet_files_verified"] += 1
        for comparison, expected in summary[lag]["comparisons"].items():
            first, second = comparison.split("_minus_")
            diff = (losses[first] - losses[second])[valid]
            interval = date_bootstrap(rows.loc[valid, "date"], diff, np.ones(len(diff)))
            close(interval["estimate"], expected["estimate"], "Paired log-loss difference differs")
            close(interval["ci95"], expected["ci95"], "Paired log-loss interval differs")
        checked["scenarios"][lag] = {"quotes": len(rows), "log_loss": model_scores,
                                       "exploratory_under_control": verify_control(rows, control["controls"][lag], forecast_path)}
        if lag == "8h":
            main_rows = rows
    checked["source_check"] = "SKIPPED by request" if skip_sources else verify_sources(main_rows, cache_dir)
    return checked


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=Path, default=STUDY / "results")
    parser.add_argument("--cache-dir", type=Path, default=STUDY.parents[1] / "data" / "rich_context" / "cache")
    parser.add_argument("--skip-source-check", action="store_true")
    parser.add_argument("--json", action="store_true", help="Print the verification summary as JSON; write nothing.")
    args = parser.parse_args()
    result = verify(args.results_dir, args.cache_dir, args.skip_source_check)
    if args.json:
        print(json.dumps(result, indent=2, allow_nan=False))
    else:
        print(f"PASS: saved probabilities, log loss, Brier scores, paired intervals, and {result['bet_files_verified']} bet files.")
        print("PASS: first qualifying selections, stakes, refunds, profits, ROI intervals, and the existing exploratory control.")
        print(result["source_check"])


if __name__ == "__main__":
    main()
