"""Corrected, research-only comparison with the existing WNBA talent model.

Nothing here writes to the live model or its registered predictions. Inputs
must already contain strictly earlier-game features. Unlike ``fp_model``, the
league denominators are explicit as-of inputs, never averages of offered props.
Price and outcome columns cannot affect ``predict_means``.

Calibration is frozen before 2025. The probability interface keeps wins,
pushes and losses separate, including the mass at zero for count outcomes.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

RAW = {"poi": "points", "reb": "rebounds", "ass": "assists",
       "tpm": "three_point_field_goals_made", "ste": "steals",
       "blo": "blocks", "tur": "turnovers"}
PARTS = {"points": ["poi"], "rebounds": ["reb"], "assists": ["ass"],
         "threes": ["tpm"], "steals": ["ste"], "blocks": ["blo"],
         "turnovers": ["tur"], "pra": ["poi", "reb", "ass"],
         "pts_reb": ["poi", "reb"], "pts_ast": ["poi", "ass"],
         "reb_ast": ["reb", "ass"], "stl_blk": ["ste", "blo"]}
POISSON = {"threes", "steals", "blocks", "turnovers", "stl_blk"}
FIT_FROM = pd.Timestamp("2010-01-01")
TRAIN_END = pd.Timestamp("2025-01-01")
RESEARCH_END = pd.Timestamp("2026-01-01")


def _dates(frame: pd.DataFrame) -> pd.Series:
    dates = pd.to_datetime(frame["game_date"])
    if dates.dt.tz is not None:
        dates = dates.dt.tz_localize(None)
    if dates.isna().any() or (dates >= RESEARCH_END).any():
        raise ValueError("This experiment accepts only valid dates before 2026.")
    return dates


def _cutoff(cutoff: str | pd.Timestamp) -> pd.Timestamp:
    value = pd.Timestamp(cutoff)
    if value.tzinfo is not None:
        value = value.tz_localize(None)
    if value > TRAIN_END:
        raise ValueError("Calibration/talent fitting must end before 2025.")
    return value


def attach_talent(panel: pd.DataFrame, cutoff="2025-01-01", *, postgame=False):
    """Fit the incumbent filter on pre-cutoff games; return pregame states.

    Historical rows go through the actual predict-then-update filter. Thus a
    player's next-game row includes the immediately preceding game, unlike
    copying the latest *pregame* row into a future live fixture. This function
    deliberately returns tuning choices for the experiment record.
    """
    cutoff = _cutoff(cutoff)
    source = panel.copy()
    source["game_date"] = _dates(source)
    source = source.drop(columns=[c for c in source if c.startswith("talent_")])
    module_path = Path(__file__).resolve().parents[2] / "src" / "talent.py"
    spec = importlib.util.spec_from_file_location("rich_context_incumbent_talent", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    data = module.load_played(source)
    if data.duplicated(["athlete_id", "game_id"]).any():
        raise ValueError("Duplicate athlete/game rows in talent history.")
    curves = module.fit_curves(data, cutoff, list(RAW))
    rvar = module.fit_rvar(data, cutoff, list(RAW))
    chosen = {}
    state = data[["athlete_id", "game_id"]].copy()
    for stat in RAW:
        if not np.isfinite(rvar[stat]) or rvar[stat] <= 0:
            raise ValueError(f"Insufficient pre-cutoff talent data for {stat}.")
        best_loss, best_pred, best_pair = np.inf, None, None
        for q in module.GRID_Q:
            for p0 in module.GRID_P0:
                pred = module.run_filter(data, curves, rvar, q, p0, stat)
                loss = module.one_step_mse(data, pred, stat, "2005-01-01", cutoff)
                if np.isfinite(loss) and loss < best_loss:
                    best_loss, best_pred, best_pair = loss, pred, (q, p0)
        if best_pair is None:
            raise ValueError(f"No valid pre-cutoff talent fit for {stat}.")
        chosen[stat] = {"q": float(best_pair[0]), "p0": float(best_pair[1]),
                        "training_rate_mse": float(best_loss)}
        if postgame:
            best_pred = _next_talent(module, data, curves, rvar, *best_pair, stat)
        state[f"talent_{stat}"] = best_pred
    merged = source.merge(state, on=["athlete_id", "game_id"], how="left", validate="one_to_one")
    return merged, {"fit_before": cutoff.date().isoformat(), "parameters": chosen,
                    "state": "next_pregame_from_postgame" if postgame else "pregame"}


def _next_talent(module, data, curves, rvar, q, p0, stat):
    """Advance each row's observation, then calculate its next-game mean."""
    means, variances, seasons = {}, {}, {}
    result = np.empty(len(data))
    r0, process, initial = rvar[stat], q * rvar[stat], p0 * rvar[stat]
    cols = ["athlete_id", "pos", "gp", "season_yr", f"den_{stat}", f"y_{stat}"]
    for i, (athlete, position, gp, year, den, actual) in enumerate(
            data[cols].itertuples(index=False, name=None)):
        level = lambda n: module.curve_level(curves, position, stat, n)
        if athlete not in means:
            means[athlete], variances[athlete] = level(0), initial
        else:
            means[athlete] += level(gp) - level(max(gp - 1, 0))
            variances[athlete] += process * (
                module.OFFSEASON_Q_MULT if year != seasons[athlete] else 1.0)
        seasons[athlete] = year
        if np.isfinite(actual) and den > 0:
            gain = variances[athlete] / (variances[athlete] + r0 / den)
            means[athlete] += gain * (actual - means[athlete])
            variances[athlete] *= 1 - gain
        result[i] = means[athlete] + level(gp + 1) - level(gp)
    return result


def build_talent_snapshots(player_history: pd.DataFrame, cutoff="2015-01-01", *,
                           eligible_game_ids=None):
    """Postgame talent states to join to the feature store's as-of snapshots.

    The default freezes ALL fitting before 2015, so training/validation games
    from 2015 onward do not help fit their own talent prior or tuning choices.
    Snapshots remain unavailable until the caller's recorded availability
    timestamp. Pass the feature store's ``eligible_game_ids`` to share its
    exhibition/All-Star exclusions. Join on athlete/game IDs before querying.
    """
    history = player_history.copy()
    history["game_date"] = _dates(history)
    for column in ["athlete_id", "game_id"]:
        history[column] = history[column].astype("string").str.replace(r"\.0$", "", regex=True)
    if eligible_game_ids is not None:
        eligible = pd.Series(list(eligible_game_ids), dtype="string").str.replace(r"\.0$", "", regex=True)
        history = history[history.game_id.isin(set(eligible))].copy()
    elif "team_abbreviation" in history:
        # Schedule-based exclusion is preferable; this catches known historic
        # exhibition labels for callers that provide an already-filtered box.
        exhibition = {"CLA", "COL", "TMW", "TMS", "USA", "WNBA", "LIB", "WIL", "SPO", "COOP"}
        history = history[~history.team_abbreviation.isin(exhibition)].copy()
    history = history[history.minutes.notna() & (history.minutes > 0)
                      & history.athlete_id.notna()].copy()
    if "did_not_play" in history:
        history = history[~history.did_not_play.fillna(False).astype(bool)].copy()
    history = history.sort_values(["athlete_id", "game_date", "game_id"])
    history["gp"] = history.groupby("athlete_id").cumcount()
    rows, metadata = attach_talent(history, cutoff, postgame=True)
    return rows[["athlete_id", "game_id"] + [f"talent_{s}" for s in RAW]], metadata


def predict_means(features: pd.DataFrame, cal: dict | None = None) -> pd.DataFrame:
    """Seven stat means using the incumbent T1 formula and fixed context.

    Required context: ``lg_pace_asof`` and ``lg_pts_against_asof`` calculated
    from the complete team history available at forecast time. Never derive
    these values from this input batch. Rows can be permuted or duplicated
    without changing any individual forecast.
    """
    f = features
    required = ["lg_pace_asof", "lg_pts_against_asof", "tm_pace_ew",
                "opp_pace_ew", "opp_pts_against_ew", "min_ewf", "min_ews"]
    missing = sorted(set(required) - set(f))
    if missing:
        raise ValueError(f"Missing strictly prior baseline inputs: {missing}")
    pace = ((f.tm_pace_ew + f.opp_pace_ew) /
            (f.tm_pace_ew + f.lg_pace_asof)).fillna(1.0)
    defense = (f.opp_pts_against_ew / f.lg_pts_against_asof).pow(0.5).fillna(1.0)
    minutes = (0.6 * f.min_ewf + 0.4 * f.min_ews).fillna(f.min_ewf)
    means = pd.DataFrame(index=f.index)
    for stat in RAW:
        game = f[f"{stat}_ewf"]
        if f"{stat}_ews" in f:
            game = (0.6 * game + 0.4 * f[f"{stat}_ews"]).fillna(game)
        rate = f[f"{stat}_rate_ewf"]
        if f"talent_{stat}" in f:
            rate = f[f"talent_{stat}"].fillna(rate)
        elif cal is not None:
            rate = (f.gp * rate + 3.0 * cal["lg_rate"][stat]) / (f.gp + 3.0)
        rate_mean = rate * minutes
        mean = 0.5 * game.fillna(rate_mean) + 0.5 * rate_mean.fillna(game)
        mean = mean * pace * (defense if stat in {"poi", "tpm"} else 1.0)
        if cal is not None:
            mean = mean * cal["c"][stat] * cal["home"][stat] ** (f.home - 0.5)
        means[stat] = mean.clip(lower=0.0)
    return means


def predict_markets(features: pd.DataFrame, cal: dict) -> pd.DataFrame:
    """Return all market means before any line/price/outcome is joined."""
    means = predict_means(features, cal)
    return pd.DataFrame({market: sum(means[p] for p in parts)
                         for market, parts in PARTS.items()}, index=features.index)


def fit_baseline(panel: pd.DataFrame, cutoff="2025-01-01") -> dict:
    """Incumbent bias, home and dispersion fits with the corrected context."""
    cutoff = _cutoff(cutoff)
    dates = _dates(panel)
    d = panel[(dates >= FIT_FROM) & (dates < cutoff) &
              (panel.gp >= 4) & panel.minutes.notna() & (panel.minutes > 0)].copy()
    if len(d) < 100:
        raise ValueError("Need at least 100 eligible pre-cutoff player games.")
    cal = {"c": {}, "home": {}, "lg_rate": {}, "sigma": {},
           "gamma": 0.0, "fit_before": cutoff.date().isoformat(),
           "training_rows": int(len(d)), "context": "explicit_market_independent_asof",
           "normal_support": "negative_normal_mass_at_zero"}
    means = predict_means(d)
    for stat, actual_col in RAW.items():
        cal["lg_rate"][stat] = float(d[actual_col].sum() / d.minutes.sum())
        ok = np.isfinite(means[stat]) & d[actual_col].notna()
        home, away = ok & d.home.eq(1), ok & d.home.eq(0)
        if not ok.any() or not home.any() or not away.any():
            raise ValueError(f"Insufficient calibration data for {stat}.")
        cal["c"][stat] = float(d.loc[ok, actual_col].mean() / means.loc[ok, stat].mean())
        rh = d.loc[home, actual_col].mean() / means.loc[home, stat].mean()
        ra = d.loc[away, actual_col].mean() / means.loc[away, stat].mean()
        cal["home"][stat] = float(rh / ra)
    calibrated = predict_means(d, cal)
    for market, parts in PARTS.items():
        if market in POISSON:
            continue
        mean = sum(calibrated[p] for p in parts)
        actual = sum(d[RAW[p]] for p in parts)
        ok = np.isfinite(mean) & actual.notna()
        slope, intercept = np.polyfit(mean[ok], (actual[ok] - mean[ok]) ** 2, 1)
        cal["sigma"][market] = [float(max(intercept, 0.1)), float(max(slope, 0.2))]
    return cal


def outcome_probabilities(market: str, mu, line, cal: dict) -> dict[str, np.ndarray]:
    """Vectorized P(over), P(push), P(under) for a nonnegative integer count.

    The incumbent uses a Normal/negative-binomial mixture for larger counts.
    Round the Normal onto integers and place its below-zero tail at zero;
    otherwise a line of zero would give positive probability to an impossible
    negative stat. Half-lines have exactly zero push mass.
    """
    if market not in PARTS:
        raise ValueError(f"Unsupported market: {market}")
    mu, line = np.broadcast_arrays(np.asarray(mu, float), np.asarray(line, float))
    if not np.isfinite(mu).all() or not np.isfinite(line).all() or (mu < 0).any() or (line < 0).any():
        raise ValueError("Means and lines must be finite and nonnegative.")
    m = np.maximum(mu, 1e-9)

    def cdf(k):
        if market in POISSON:
            result = stats.poisson.cdf(k, m)
        else:
            intercept, slope = cal["sigma"][market]
            variance = np.maximum(intercept + slope * m, 0.25)
            normal = stats.norm.cdf(k + 0.5, m, np.sqrt(variance))
            overdispersed = variance > m * 1.001
            shape = m * m / np.maximum(variance - m, 1e-12)
            count = np.where(overdispersed,
                             stats.nbinom.cdf(k, shape, shape / (shape + m)),
                             stats.poisson.cdf(k, m))
            result = 0.5 * normal + 0.5 * count
        return np.where(k < 0, 0.0, result)

    lower = np.ceil(line) - 1  # largest integer strictly below the threshold
    upper = np.floor(line)   # largest integer at or below the threshold
    under = cdf(lower)
    over = 1.0 - cdf(upper)
    push = np.where(line == np.floor(line), cdf(upper) - cdf(lower), 0.0)
    return {"p_over": np.clip(over, 0, 1), "p_push": np.clip(push, 0, 1),
            "p_under": np.clip(under, 0, 1)}


def expected_profit(p_win, p_push, decimal_odds):
    """Expected net profit on one dollar; a push returns the original stake."""
    p_win, p_push, dec = np.broadcast_arrays(np.asarray(p_win, float),
                                             np.asarray(p_push, float),
                                             np.asarray(decimal_odds, float))
    if (not np.isfinite(p_win).all() or not np.isfinite(p_push).all() or
            not np.isfinite(dec).all() or (p_win < 0).any() or
            (p_push < 0).any() or (p_win + p_push > 1 + 1e-10).any() or (dec <= 1).any()):
        raise ValueError("Invalid probabilities or decimal odds.")
    return p_win * (dec - 1) - (1 - p_win - p_push)
