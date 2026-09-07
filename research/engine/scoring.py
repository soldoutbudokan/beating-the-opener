"""Reproduce research scores and flat-stake decisions from saved forecasts only.

Minimal row schema (one model and one quoted line per row)::

    game_id, player_id, market, quote_id, book: nonempty identifiers
    date: scheduled tip's America/New_York calendar date, YYYY-MM-DD
    line, over_odds, under_odds: count line and DECIMAL prices
    over_at, under_at, quote_available_at, tip_at, as_of: aware ISO times
    p_over, p_push, p_under: conditional-on-playing outcome probabilities
    actual: nonnegative integer; null or zero only on explicitly void rows
    actual_minutes: observed minutes; zero means DNP, never missing
    void: boolean; true exactly when actual_minutes == 0
    p_dnp, minutes_values, minutes_probs: DNP mass and positive conditional grid
    count_p_actual: conditional-on-playing mass at actual; null on void rows
    model_mean, model_median: conditional-on-playing count summaries

Optional complete comparator columns are baseline_p_over_nonpush,
baseline_count_p_actual, and the triple
baseline_p_dnp/baseline_minutes_values/baseline_minutes_probs. Each comparator
must exist on every relevant row. close_p_over_nonpush may be null when no
same-line close exists; that comparison reports its common subset and misses.
Optional p_open_nonpush is checked against
the no-vig probability from the two saved prices. Optional team_id must be
consistent within player-game. Quotes retain both source times, and their
availability is the later one. Prices never enter a forecasting function here.

Minute scores include DNPs and use the unconditional mixture. Repeated markets
are reduced to the earliest available forecast for each player-game. The
selection order is quote availability, quote ID, side; equal-EV sides choose
over. Identical saved rows deduplicate, conflicts fail. Expected profit is
multiplied by (1 - p_dnp), because a nonparticipant refunds the stake.
Binary probability scores exclude pushes and voids. Count NLL includes pushes
and excludes voids. A declared 1e-12 floor makes zero-mass penalties finite.
All intervals resample whole ET game dates, with 10,000 draws and a fixed seed.

No fitting, source loading, protected-window release, or live action occurs.
NumPy is imported only inside date_bootstrap; validation and grading use the
standard library. Gate thresholds are explicit arguments to evaluate_gates.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date as calendar_date, datetime, timezone
import hashlib
import json
import math
from numbers import Real
from zoneinfo import ZoneInfo


BOOTSTRAP_RESAMPLES = 10000
BOOTSTRAP_SEED = 20260907
PROBABILITY_FLOOR = 1e-12
MEAN_BUCKETS = (0, 3, 6, 10, 15, 20, math.inf)
PROBABILITY_BUCKETS = (0, .1, .2, .3, .4, .5, .6, .7, .8, .9, 1.00000001)
EASTERN = ZoneInfo("America/New_York")


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _number(value, label, lower=None, upper=None):
    _require(isinstance(value, Real) and not isinstance(value, bool), f"{label}: expected number")
    value = float(value)
    _require(math.isfinite(value), f"{label}: non-finite number")
    _require(lower is None or value >= lower, f"{label}: below {lower}")
    _require(upper is None or value <= upper, f"{label}: above {upper}")
    return value


def _time(value, label):
    _require(isinstance(value, str), f"{label}: expected ISO timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{label}: invalid timestamp") from error
    _require(result.tzinfo is not None and result.utcoffset() is not None, f"{label}: timezone required")
    return result.astimezone(timezone.utc)


def _grid(row, prefix=""):
    values = row.get(prefix + "minutes_values")
    probabilities = row.get(prefix + "minutes_probs")
    _require(isinstance(values, (list, tuple)) and isinstance(probabilities, (list, tuple)), "Minutes grid missing")
    _require(len(values) > 0 and len(values) == len(probabilities), "Minutes grid shape differs")
    values = [_number(x, "minutes value", 0) for x in values]
    _require(all(x > 0 for x in values) and values == sorted(set(values)), "Minutes grid must increase strictly above zero")
    probabilities = [_number(x, "minutes probability", 0, 1) for x in probabilities]
    _require(abs(sum(probabilities) - 1) <= 1e-9, "Minutes probabilities do not sum to one")
    dnp = _number(row.get(prefix + "p_dnp"), "DNP probability", 0, 1)
    row[prefix + "minutes_values"] = values
    row[prefix + "minutes_probs"] = probabilities
    row[prefix + "p_dnp"] = dnp
    return [0.] + values, [dnp] + [(1 - dnp) * p for p in probabilities]


def _canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError("Saved rows must contain finite JSON values") from error


def _selection_key(row):
    return (row["quote_available_at"], row["quote_id"])


def validate_rows(rows):
    """Return canonical copies, rejecting unresolved grades and identity conflicts."""
    normalized = []
    quote_ids, identities, events, players, outcomes, distributions = {}, {}, {}, {}, {}, {}
    for raw in rows:
        _require(isinstance(raw, dict), "Each forecast must be a dictionary")
        _canonical(raw)
        row = dict(raw)
        for key in ("game_id", "player_id", "market", "quote_id", "book"):
            _require(isinstance(row.get(key), (str, int)) and not isinstance(row.get(key), bool), f"Missing identity: {key}")
            row[key] = str(row[key])
            _require(bool(row[key].strip()), f"Empty identity: {key}")
        times = {key: _time(row.get(key), key) for key in
                 ("over_at", "under_at", "quote_available_at", "tip_at", "as_of")}
        _require(times["quote_available_at"] == max(times["over_at"], times["under_at"]), "Quote availability differs from paired side times")
        _require(times["quote_available_at"] <= times["as_of"] < times["tip_at"], "Quote or forecast is not before tip")
        for key, value in times.items():
            row[key] = value.isoformat(timespec="microseconds").replace("+00:00", "Z")
        try:
            day = calendar_date.fromisoformat(row.get("date", ""))
        except (TypeError, ValueError) as error:
            raise ValueError("Invalid game date") from error
        _require(day == times["tip_at"].astimezone(EASTERN).date(), "Game date differs from Eastern tip date")
        row["date"] = day.isoformat()
        for key in ("line", "model_mean", "model_median"):
            row[key] = _number(row.get(key), key, 0)
        _require(row["model_median"].is_integer(), "Count median must be an integer")
        for key in ("over_odds", "under_odds"):
            row[key] = _number(row.get(key), key, 1)
            _require(row[key] > 1, "Decimal odds must exceed one")
        for key in ("p_over", "p_push", "p_under"):
            row[key] = _number(row.get(key), key, 0, 1)
        _require(abs(row["p_over"] + row["p_push"] + row["p_under"] - 1) <= 1e-9, "Outcome probabilities do not sum to one")
        _require(row["p_push"] < 1, "Non-push probability is undefined")
        _require(row["line"].is_integer() or row["p_push"] == 0, "Noninteger count line cannot have push mass")
        conditional = row["p_over"] / (1 - row["p_push"])
        if "p_over_nonpush" in row:
            _require(abs(_number(row["p_over_nonpush"], "Conditional over probability", 0, 1) - conditional) <= 1e-9, "Saved non-push probability disagrees with outcome masses")
        row["p_over_nonpush"] = conditional
        implied = (1 / row["over_odds"]) / (1 / row["over_odds"] + 1 / row["under_odds"])
        if "p_open_nonpush" in row:
            _require(abs(_number(row["p_open_nonpush"], "Opener probability", 0, 1) - implied) <= 1e-9, "Saved opener probability disagrees with prices")
        row["p_open_nonpush"] = implied
        _require(type(row.get("void")) is bool, "Void must be an explicit boolean")
        row["actual_minutes"] = _number(row.get("actual_minutes"), "Actual minutes", 0)
        _require(row["void"] == (row["actual_minutes"] == 0), "Void and observed minutes disagree")
        if row["void"]:
            _require(row.get("actual") is None or row.get("actual") == 0, "Void count must be null or zero")
            row["actual"] = None
            _require(row.get("count_p_actual") is None, "Void count probability must be null")
            row["count_p_actual"] = None
        else:
            row["actual"] = _number(row.get("actual"), "Actual count", 0)
            _require(row["actual"].is_integer(), "Actual count must be an integer")
            row["count_p_actual"] = _number(row.get("count_p_actual"), "Count outcome probability", 0, 1)
            outcome_mass = row["p_over"] if row["actual"] > row["line"] else row["p_under"] if row["actual"] < row["line"] else row["p_push"]
            _require(row["count_p_actual"] <= outcome_mass + 1e-9, "Count probability exceeds corresponding line outcome mass")
            if row["actual"] == row["line"]:
                _require(abs(row["count_p_actual"] - row["p_push"]) <= 1e-9, "Actual push probability disagrees with count mass")
        _grid(row)
        baseline_grid_keys = ("baseline_p_dnp", "baseline_minutes_values", "baseline_minutes_probs")
        if any(key in row for key in baseline_grid_keys):
            _require(all(key in row for key in baseline_grid_keys), "Incomplete baseline minutes distribution")
            _grid(row, "baseline_")
        for key in ("baseline_p_over_nonpush",):
            if key in row:
                row[key] = _number(row[key], key, 0, 1)
        if row.get("close_p_over_nonpush") is not None:
            row["close_p_over_nonpush"] = _number(row["close_p_over_nonpush"], "close_p_over_nonpush", 0, 1)
        if "baseline_count_p_actual" in row:
            if row["void"]:
                _require(row["baseline_count_p_actual"] is None, "Void baseline count probability must be null")
            else:
                row["baseline_count_p_actual"] = _number(row["baseline_count_p_actual"], "baseline_count_p_actual", 0, 1)
        event = row["game_id"]
        event_value = (row["date"], row["tip_at"])
        _require(event not in events or events[event] == event_value, "Conflicting event date/tip")
        events[event] = event_value
        player = (event, row["player_id"])
        player_value = (row["actual_minutes"], row["void"], str(row.get("team_id", "")))
        _require(player not in players or players[player] == player_value, "Conflicting player-game roster or minutes")
        players[player] = player_value
        outcome = (*player, row["market"])
        _require(outcome not in outcomes or outcomes[outcome] == row["actual"], "Conflicting player-game count outcome")
        outcomes[outcome] = row["actual"]
        distribution = (*player, row["as_of"])
        dist_keys = ("p_dnp", "minutes_values", "minutes_probs", *baseline_grid_keys)
        distribution_value = _canonical({key: row.get(key) for key in dist_keys})
        _require(distribution not in distributions or distributions[distribution] == distribution_value, "Same forecast has inconsistent minutes distributions")
        distributions[distribution] = distribution_value
        identity = (*outcome, row["book"], row["line"], row["over_at"], row["under_at"])
        encoded = _canonical(row)
        _require(row["quote_id"] not in quote_ids or quote_ids[row["quote_id"]] == encoded, "Conflicting duplicate quote ID")
        _require(identity not in identities or identities[identity] == encoded, "Conflicting duplicate quote identity")
        if row["quote_id"] not in quote_ids:
            normalized.append(row)
        quote_ids[row["quote_id"]] = identities[identity] = encoded
    normalized.sort(key=lambda row: (*_selection_key(row), row["game_id"], row["player_id"]))
    for key in ("baseline_p_over_nonpush", "baseline_count_p_actual", "baseline_p_dnp"):
        _require(not normalized or all(key in r for r in normalized) or all(key not in r for r in normalized), f"Comparator {key} is missing on part of the cohort")
    return normalized


def date_bootstrap(dates, numerator, denominator=None, *, resamples=BOOTSTRAP_RESAMPLES, seed=BOOTSTRAP_SEED):
    """Ratio-of-sums interval; paired differences pass per-row score differences.

    One-date/empty cohorts have no interval or t statistic. Zero-denominator
    dates are retained in resampling. A draw with no settled denominator is
    omitted and its count is reported. Memory is bounded to 256 draws per batch.
    """
    import numpy as np

    _require(isinstance(resamples, int) and not isinstance(resamples, bool) and resamples > 0, "Positive resample count required")
    _require(isinstance(seed, int) and not isinstance(seed, bool), "Integer bootstrap seed required")
    dates, numerator = list(dates), list(numerator)
    denominator = [1.] * len(numerator) if denominator is None else list(denominator)
    _require(len(dates) == len(numerator) == len(denominator), "Bootstrap lengths differ")
    groups = defaultdict(lambda: [[], []])
    for day, num, den in zip(dates, numerator, denominator):
        _require(isinstance(day, str), "Bootstrap date must be a string")
        groups[day][0].append(_number(num, "Bootstrap numerator"))
        groups[day][1].append(_number(den, "Bootstrap denominator", 0))
    grouped = np.asarray([[math.fsum(groups[day][0]), math.fsum(groups[day][1])] for day in sorted(groups)], dtype=float).reshape(-1, 2)
    total_den = float(grouped[:, 1].sum())
    estimate = float(grouped[:, 0].sum() / total_den) if total_den else None
    result = {"estimate": estimate, "ci95": None, "se": None, "t": None,
              "dates": len(grouped), "resamples": resamples, "valid_resamples": 0, "seed": seed}
    if len(grouped) < 2 or total_den == 0:
        return result
    rng, batches = np.random.default_rng(seed), []
    for start in range(0, resamples, 256):
        indices = rng.integers(0, len(grouped), size=(min(256, resamples - start), len(grouped)))
        sums = grouped[indices].sum(axis=1)
        valid = sums[:, 1] > 0
        batches.append(sums[valid, 0] / sums[valid, 1])
    draws = np.concatenate(batches)
    result["valid_resamples"] = int(len(draws))
    if len(draws) >= 2:
        se = float(draws.std(ddof=1))
        result.update(ci95=np.quantile(draws, [.025, .975]).tolist(), se=se,
                      t=float(estimate / se) if se > 0 else None)
    return result


def _mean(values):
    return math.fsum(values) / len(values) if values else None


def _log_loss(p, y):
    p = min(1 - PROBABILITY_FLOOR, max(PROBABILITY_FLOOR, p))
    return -math.log(p if y else 1 - p)


def discrete_crps(values, probabilities, actual):
    """Exact E|X-y| - E|X-X'|/2 for a discrete predictive distribution."""
    _require(len(values) == len(probabilities) and len(values) > 0, "CRPS grid shape differs")
    pairs = sorted((_number(x, "CRPS support", 0), _number(p, "CRPS mass", 0, 1)) for x, p in zip(values, probabilities))
    _require(abs(math.fsum(p for _, p in pairs) - 1) <= 1e-9, "CRPS probabilities do not sum to one")
    actual = _number(actual, "CRPS actual", 0)
    first = math.fsum(p * abs(x - actual) for x, p in pairs)
    previous_mass = previous_moment = spread = 0.
    for x, p in pairs:
        spread += p * (x * previous_mass - previous_moment)
        previous_mass += p
        previous_moment += p * x
    return max(0., first - spread)


def _minute_losses(row, prefix=""):
    values = [0.] + row[prefix + "minutes_values"]
    dnp = row[prefix + "p_dnp"]
    probabilities = [dnp] + [(1 - dnp) * p for p in row[prefix + "minutes_probs"]]
    predicted = math.fsum(x * p for x, p in zip(values, probabilities))
    return discrete_crps(values, probabilities, row["actual_minutes"]), abs(predicted - row["actual_minutes"])


def _buckets(rows, key, edges):
    result = []
    for low, high in zip(edges, edges[1:]):
        selected = [r for r in rows if low <= r[key] < high]
        predicted = _mean([r["p_over_nonpush"] for r in selected])
        observed = _mean([float(r["actual"] > r["line"]) for r in selected])
        result.append({"lower": low, "upper": None if math.isinf(high) else min(high, 1.) if key == "p_over_nonpush" else high,
                       "n": len(selected), "predicted": predicted, "observed": observed,
                       "gap": predicted - observed if selected else None})
    return result


def paired_comparison(rows, alternative_probability="baseline_p_over_nonpush", *, resamples=BOOTSTRAP_RESAMPLES, seed=BOOTSTRAP_SEED):
    """Candidate minus comparator loss, using exactly the same non-push rows."""
    rows = validate_rows(rows)
    settled = [r for r in rows if not r["void"] and r["actual"] != r["line"]]
    if alternative_probability == "close_p_over_nonpush":
        selected = [r for r in settled if r.get(alternative_probability) is not None]
    else:
        selected = settled
        _require(all(alternative_probability in r for r in selected), "Missing paired comparator probability")
    differences = [_log_loss(r["p_over_nonpush"], r["actual"] > r["line"]) -
                   _log_loss(_number(r[alternative_probability], "Comparator probability", 0, 1), r["actual"] > r["line"]) for r in selected]
    return {"n": len(selected), "missing_comparator": len(settled) - len(selected),
            "direction": "candidate_minus_comparator", **date_bootstrap(
        [r["date"] for r in selected], differences, resamples=resamples, seed=seed)}


def score_forecasts(rows, *, resamples=BOOTSTRAP_RESAMPLES, seed=BOOTSTRAP_SEED):
    """Return JSON-ready minute, count, calibration and paired market metrics."""
    rows = validate_rows(rows)
    settled = [r for r in rows if not r["void"] and r["actual"] != r["line"]]
    played = [r for r in rows if not r["void"]]
    minute_rows = list({(r["game_id"], r["player_id"]): r for r in reversed(rows)}.values())
    minute_rows.sort(key=lambda r: (r["date"], r["game_id"], r["player_id"]))
    crps, mae = zip(*[_minute_losses(r) for r in minute_rows]) if minute_rows else ([], [])
    minutes = {"n": len(minute_rows), "selection": "earliest_available_forecast_per_player_game",
               "quote_ids": [r["quote_id"] for r in minute_rows], "crps": _mean(crps), "mae": _mean(mae)}
    dnp_predicted = _mean([r["p_dnp"] for r in minute_rows])
    dnp_observed = _mean([float(r["void"]) for r in minute_rows])
    participation = {"n": len(minute_rows), "predicted_dnp": dnp_predicted,
        "observed_dnp": dnp_observed,
        "gap": dnp_predicted - dnp_observed if minute_rows else None,
        "brier": _mean([(r["p_dnp"] - float(r["void"])) ** 2 for r in minute_rows]),
        "by_probability": []}
    for lo, hi in zip(PROBABILITY_BUCKETS, PROBABILITY_BUCKETS[1:]):
        group = [r for r in minute_rows if lo <= r["p_dnp"] < hi]
        participation["by_probability"].append({"lower": lo, "upper": min(hi, 1.),
            "n": len(group), "predicted_dnp": _mean([r["p_dnp"] for r in group]),
            "observed_dnp": _mean([float(r["void"]) for r in group])})
    minutes["participation"] = participation
    if minute_rows and "baseline_p_dnp" in minute_rows[0]:
        bcrps, bmae = zip(*[_minute_losses(r, "baseline_") for r in minute_rows])
        minutes["baseline"] = {"crps": _mean(bcrps), "mae": _mean(bmae)}
        minutes["paired"] = {key: date_bootstrap([r["date"] for r in minute_rows],
            [a - b for a, b in zip(candidate, baseline)], resamples=resamples, seed=seed)
            for key, candidate, baseline in (("crps", crps, bcrps), ("mae", mae, bmae))}
    count = {}
    for market in sorted({r["market"] for r in played}):
        group = [r for r in played if r["market"] == market]
        nll = [-math.log(max(PROBABILITY_FLOOR, r["count_p_actual"])) for r in group]
        count[market] = {"n": len(group), "nll": _mean(nll)}
        if "baseline_count_p_actual" in group[0]:
            baseline = [-math.log(max(PROBABILITY_FLOOR, r["baseline_count_p_actual"])) for r in group]
            count[market].update(baseline_nll=_mean(baseline), paired=date_bootstrap(
                [r["date"] for r in group], [a - b for a, b in zip(nll, baseline)], resamples=resamples, seed=seed))
    model_losses = [_log_loss(r["p_over_nonpush"], r["actual"] > r["line"]) for r in settled]
    model_brier = [(r["p_over_nonpush"] - float(r["actual"] > r["line"])) ** 2 for r in settled]
    calibration = {"predicted": _mean([r["p_over_nonpush"] for r in settled]),
                   "observed": _mean([float(r["actual"] > r["line"]) for r in settled]),
                   "by_probability": _buckets(settled, "p_over_nonpush", PROBABILITY_BUCKETS),
                   "by_model_mean": _buckets(settled, "model_mean", MEAN_BUCKETS)}
    calibration["gap"] = calibration["predicted"] - calibration["observed"] if settled else None
    comparison = {"opener": paired_comparison(rows, "p_open_nonpush", resamples=resamples, seed=seed)}
    for label, key in (("baseline", "baseline_p_over_nonpush"), ("close", "close_p_over_nonpush")):
        if any(key in row for row in rows):
            comparison[label] = paired_comparison(rows, key, resamples=resamples, seed=seed)
    return {"schema": "saved-forecast-scores-v1", "probability_floor": PROBABILITY_FLOOR,
            "quotes": len(rows), "settled_nonpush": len(settled), "played_including_push": len(played),
            "voids": len(rows) - len(played), "pushes": len(played) - len(settled),
            "minutes": minutes, "count_by_market": count,
            "log_loss": _mean(model_losses), "brier": _mean(model_brier),
            "opener_log_loss": _mean([_log_loss(r["p_open_nonpush"], r["actual"] > r["line"]) for r in settled]),
            "calibration": calibration, "comparisons": comparison}


def select_bets(rows, threshold):
    """First quote with max(over EV, under EV) strictly above threshold."""
    threshold = _number(threshold, "EV threshold", 0)
    selected, used = [], set()
    for row in validate_rows(rows):
        ev_over = (1 - row["p_dnp"]) * (row["p_over"] * (row["over_odds"] - 1) - row["p_under"])
        ev_under = (1 - row["p_dnp"]) * (row["p_under"] * (row["under_odds"] - 1) - row["p_over"])
        player = (row["game_id"], row["player_id"])
        if max(ev_over, ev_under) > threshold and player not in used:
            side = "over" if ev_over >= ev_under else "under"
            selected.append({**row, "side": side, "claimed_ev": max(ev_over, ev_under)})
            used.add(player)
    return selected


def _grade(row, side, *, simulated_over=None):
    void = row["void"]
    push = not void and row["actual"] == row["line"]
    win = not void and not push and ((row["actual"] > row["line"] if simulated_over is None else simulated_over) == (side == "over"))
    price = row[side + "_odds"]
    profit = 0. if void or push else price - 1 if win else -1.
    return {"side": side, "odds": price, "stake": 1., "settled_stake": 0. if void else 1.,
            "refund": 1. if void or push else 0., "profit": profit, "win": win,
            "push": push, "void": void}


def _bet_summary(rows, grades, *, resamples, seed):
    profit, stake = [g["profit"] for g in grades], [g["settled_stake"] for g in grades]
    return {"bets": len(grades), "wins": sum(g["win"] for g in grades),
            "losses": sum(g["profit"] < 0 for g in grades), "pushes": sum(g["push"] for g in grades),
            "voids": sum(g["void"] for g in grades), "over_bets": sum(g["side"] == "over" for g in grades),
            "stake": math.fsum(stake), "staked_before_refunds": float(len(grades)),
            "refund": math.fsum(g["refund"] for g in grades), "profit": math.fsum(profit),
            "roi": date_bootstrap([r["date"] for r in rows], profit, stake, resamples=resamples, seed=seed)}


def score_bets(rows, threshold, *, resamples=BOOTSTRAP_RESAMPLES, seed=BOOTSTRAP_SEED):
    """Saved selections plus matched controls; placebo draws use no outcomes.

    Placebo is one deterministic market-null outcome simulation at each selected
    quote's no-vig over probability. It retains observed push/void settlement,
    and keeps the candidate's selected quote and side. It is not another model
    or an estimate of the distribution of placebo strategies.
    """
    chosen = select_bets(rows, threshold)
    candidate, under, placebo, selections = [], [], [], []
    for row in chosen:
        digest = hashlib.sha256(f"{seed}|{row['quote_id']}|placebo".encode()).digest()
        uniform = int.from_bytes(digest[:8], "big") / 2 ** 64
        simulated_over = uniform < row["p_open_nonpush"]
        grade, control, fake = _grade(row, row["side"]), _grade(row, "under"), _grade(row, row["side"], simulated_over=simulated_over)
        candidate.append(grade)
        under.append(control)
        placebo.append(fake)
        selections.append({"quote_id": row["quote_id"], "game_id": row["game_id"], "player_id": row["player_id"],
                           "date": row["date"], "market": row["market"], "book": row["book"], "line": row["line"],
                           "quote_available_at": row["quote_available_at"], "claimed_ev": row["claimed_ev"],
                           "candidate": grade, "always_under": control, "placebo": fake,
                           "placebo_uniform": uniform, "placebo_over_probability": row["p_open_nonpush"],
                           "placebo_over": simulated_over})
    return {"threshold": threshold, "selection_rule": "first_qualifying_quote_per_player_game",
            "placebo_rule": "seeded_market_nonpush_outcome_simulation_same_quotes_and_sides",
            "selections": selections, "candidate": _bet_summary(chosen, candidate, resamples=resamples, seed=seed),
            "always_under": _bet_summary(chosen, under, resamples=resamples, seed=seed),
            "placebo": _bet_summary(chosen, placebo, resamples=resamples, seed=seed),
            "matched_claimed_profit": math.fsum(r["claimed_ev"] for r in chosen if not r["void"])}


def evaluate_gates(scores, *, calibration_overall_limit, calibration_bucket_limit,
                   log_loss_limit, expected_settled, expected_markets,
                   close_gain_tripwire, close_t_tripwire):
    """Apply caller-registered Phase 1 gates; absent evidence cannot pass."""
    overall = scores["calibration"]["gap"]
    buckets = scores["calibration"]["by_model_mean"]
    count = scores["count_by_market"]
    close = scores["comparisons"].get("close")
    calibration = overall is not None and abs(overall) <= calibration_overall_limit and all(
        bucket["gap"] is not None and abs(bucket["gap"]) <= calibration_bucket_limit for bucket in buckets if bucket["n"] > 0)
    count_pass = set(count) == set(expected_markets) and all(
        value.get("baseline_nll") is not None and value["nll"] < value["baseline_nll"] for value in count.values())
    log_loss = scores["log_loss"] is not None and scores["settled_nonpush"] == expected_settled and scores["log_loss"] <= log_loss_limit
    close_measured = close is not None and close["estimate"] is not None and close["dates"] >= 2
    # A constant nonzero gain has zero bootstrap SE: it must still trip.
    tripwire = bool(close_measured and close["estimate"] < -close_gain_tripwire and
                    (close["se"] == 0 or close["t"] is not None and close["t"] < -close_t_tripwire))
    gates = {"calibration": bool(calibration), "count_nll_every_market": bool(count_pass),
             "registered_log_loss": bool(log_loss), "close_tripwire_clear": bool(close_measured and not tripwire)}
    return {"gates": gates, "pass": all(gates.values()), "leakage_investigation_required": tripwire}


def build_report(rows, *, thresholds=(.05, .10), resamples=BOOTSTRAP_RESAMPLES, seed=BOOTSTRAP_SEED):
    """Canonical report containing every score, selected bet and control."""
    rows = validate_rows(rows)
    thresholds = tuple(_number(x, "EV threshold", 0) for x in thresholds)
    _require(len(set(thresholds)) == len(thresholds), "Duplicate betting thresholds")
    return {"schema": "saved-forecast-report-v1", "rows_sha256": hashlib.sha256(_canonical(rows).encode()).hexdigest(),
            "bootstrap": {"resamples": resamples, "seed": seed}, "scores": score_forecasts(rows, resamples=resamples, seed=seed),
            "economics": {str(threshold): score_bets(rows, threshold, resamples=resamples, seed=seed) for threshold in thresholds}}


def verify_saved_report(rows, report, *, thresholds=(.05, .10), resamples=BOOTSTRAP_RESAMPLES, seed=BOOTSTRAP_SEED):
    """Recompute all arithmetic with caller-pinned settings; reject any change."""
    regenerated = build_report(rows, thresholds=thresholds, resamples=resamples, seed=seed)
    _require(_canonical(regenerated) == _canonical(report), "Saved report differs from forecast reproduction")
    return {"status": "PASS", "rows_sha256": regenerated["rows_sha256"],
            "quotes": regenerated["scores"]["quotes"], "bet_rules": len(regenerated["economics"])}
