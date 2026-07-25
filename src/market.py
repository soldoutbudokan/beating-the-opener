"""Market utilities: build a clean game+odds table and de-vig closing moneylines.

The de-vigged closing line is the benchmark the model must beat, so we implement
several removal methods and pick the best-calibrated one -- beating a strawman
version of the market would prove nothing.
"""
import os

import numpy as np
import pandas as pd
from scipy import optimize

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def american_to_prob(odds):
    """American odds -> implied probability (includes vig)."""
    o = np.asarray(odds, dtype=float)
    p = np.where(o < 0, (-o) / ((-o) + 100.0), 100.0 / (o + 100.0))
    return np.where(np.isnan(o), np.nan, p)


def american_to_decimal(odds):
    o = np.asarray(odds, dtype=float)
    return np.where(o < 0, 1.0 + 100.0 / (-o), 1.0 + o / 100.0)


def devig_multiplicative(p_home, p_away):
    tot = p_home + p_away
    return p_home / tot


def devig_additive(p_home, p_away):
    over = p_home + p_away - 1.0
    return p_home - over / 2.0


def devig_power(p_home, p_away):
    """Solve for k where p_i^k sums to 1."""
    out = np.full_like(p_home, np.nan, dtype=float)
    for i, (ph, pa) in enumerate(zip(p_home, p_away)):
        if not np.isfinite(ph) or not np.isfinite(pa):
            continue

        def f(k, ph=ph, pa=pa):
            return ph ** k + pa ** k - 1.0

        try:
            k = optimize.brentq(f, 0.5, 3.0, maxiter=100)
            out[i] = ph ** k
        except (ValueError, RuntimeError):
            out[i] = ph / (ph + pa)
    return out


def devig_shin(p_home, p_away):
    """Shin (1993): removes vig assuming a share z of insider money."""
    out = np.full_like(p_home, np.nan, dtype=float)
    for i, (ph, pa) in enumerate(zip(p_home, p_away)):
        if not np.isfinite(ph) or not np.isfinite(pa):
            continue
        tot = ph + pa

        def f(z, ph=ph, pa=pa, tot=tot):
            s = 0.0
            for p in (ph, pa):
                s += (np.sqrt(z ** 2 + 4 * (1 - z) * p ** 2 / tot) - z) / (2 * (1 - z))
            return s - 1.0

        try:
            z = optimize.brentq(f, 1e-9, 0.35, maxiter=100)
            out[i] = (np.sqrt(z ** 2 + 4 * (1 - z) * ph ** 2 / tot) - z) / (2 * (1 - z))
        except (ValueError, RuntimeError):
            out[i] = ph / tot
    return out


def load_games_odds():
    games = pd.read_csv(os.path.join(ROOT, "data", "raw", "games.csv"))
    odds = pd.read_json(os.path.join(ROOT, "data", "raw", "odds.jsonl"), lines=True)
    games["game_id"] = games.game_id.astype(str)
    odds["game_id"] = odds.game_id.astype(str)
    df = games.merge(odds, on="game_id", how="left")

    df["game_date"] = pd.to_datetime(df["game_date"])
    df["date_utc"] = pd.to_datetime(df["date_utc"], utc=True)
    df = df.sort_values("date_utc").reset_index(drop=True)

    df["home_win"] = (df.home_score > df.away_score).astype(int)
    df["margin"] = df.home_score - df.away_score
    df["total_pts"] = df.home_score + df.away_score

    # Prefer the consensus line; fall back to the preferred single book.
    for side in ("home", "away"):
        for phase in ("close", "open"):
            c, b = f"cons_{side}_ml_{phase}", f"book_{side}_ml_{phase}"
            if c in df and b in df:
                df[f"{side}_ml_{phase}"] = df[c].fillna(df[b])
            elif c in df:
                df[f"{side}_ml_{phase}"] = df[c]

    df["market_total"] = df.get("cons_total_close")
    if "cons_home_spread_close" in df:
        df["market_spread"] = df["cons_home_spread_close"]
    return df


def add_market_probs(df):
    """Attach de-vigged closing (and opening) home win probabilities."""
    ph_raw = american_to_prob(df.home_ml_close.values)
    pa_raw = american_to_prob(df.away_ml_close.values)
    # Sanity: overround must look like a real two-way market.
    over = ph_raw + pa_raw
    bad = ~np.isfinite(over) | (over < 1.0) | (over > 1.25)
    ph_raw = np.where(bad, np.nan, ph_raw)
    pa_raw = np.where(bad, np.nan, pa_raw)

    df["mkt_overround"] = ph_raw + pa_raw
    df["mkt_mult"] = devig_multiplicative(ph_raw, pa_raw)
    df["mkt_add"] = devig_additive(ph_raw, pa_raw)
    df["mkt_shin"] = devig_shin(ph_raw, pa_raw)
    df["mkt_power"] = devig_power(ph_raw, pa_raw)

    if "home_ml_open" in df:
        oh = american_to_prob(df.home_ml_open.values)
        oa = american_to_prob(df.away_ml_open.values)
        o_over = oh + oa
        obad = ~np.isfinite(o_over) | (o_over < 1.0) | (o_over > 1.30)
        oh = np.where(obad, np.nan, oh)
        oa = np.where(obad, np.nan, oa)
        df["mkt_open_mult"] = devig_multiplicative(oh, oa)
    return df


def log_loss_vec(y, p, eps=1e-12):
    p = np.clip(p, eps, 1 - eps)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def brier_vec(y, p):
    return (y - p) ** 2
