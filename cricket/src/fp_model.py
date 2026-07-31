"""Stage B from-scratch BBL model (PROGRESS.md Market 2).

Team-level, market-blind: expanding walk-forward Elo on results with home
advantage and season-boundary regression, P(home) = logistic(rating diff).
Banned inputs per Stage A: toss, batted-first (unknowable at open time),
any odds column. Hyperparameters (K, home adv, season regression, logistic
scale) are grid-searched on the 2011-2017 seasons ONLY — the pre-odds era,
disjoint from both dev (2018-2020) and holdout (2021-2022).

Usage: python3 src/fp_model.py            # tune on 2011-2017, score dev
       python3 src/fp_model.py --holdout  # + score holdout once, with ROI
"""
import argparse
import itertools
from pathlib import Path

import numpy as np
import pandas as pd

import fp_benchmark as fb


def run_elo(df, k, home_adv, regress, scale):
    """Walk-forward Elo; returns P(home) for every row, using prior games
    only. Ratings update on all decided matches (incl. pre-odds era)."""
    ratings = {}
    last_season = None
    probs = np.full(len(df), np.nan)
    for i, r in enumerate(df.itertuples()):
        if last_season is not None and r.season != last_season:
            for t in ratings:
                ratings[t] *= (1 - regress)
        last_season = r.season
        h, a = r._3, r._4  # 'Home Team (H)', 'Away Team (A)'
        rh, ra = ratings.get(h, 0.0), ratings.get(a, 0.0)
        neutral = isinstance(r._6, str) and r._6.strip() != ""
        adv = 0.0 if neutral else home_adv
        p = 1.0 / (1.0 + np.exp(-(rh - ra + adv) / scale))
        probs[i] = p
        if r.Winner in ("H", "A"):
            y = 1.0 if r.Winner == "H" else 0.0
            ratings[h] = rh + k * (y - p)
            ratings[a] = ra - k * (y - p)
    return probs


def overs_float(x):
    """Cricket overs notation: 19.4 = 19 overs 4 balls = 19.667."""
    x = pd.to_numeric(x, errors="coerce")
    whole = np.floor(x)
    return whole + (x - whole) * 10.0 / 6.0


def run_nrr(df, alpha, regress, sigma, home_int):
    """Walk-forward net-run-rate ratings (EW of own RR - opponent RR per
    match); P(home) = Phi((net_h - net_a + home_int) / sigma). Iteration 2,
    tuned on the pre-odds era only."""
    from scipy.stats import norm
    net = {}
    last_season = None
    probs = np.full(len(df), np.nan)
    hs = pd.to_numeric(df["Home Score"], errors="coerce")
    as_ = pd.to_numeric(df["Away Score"], errors="coerce")
    ho = overs_float(df["Home Overs"])
    ao = overs_float(df["Away Overs"])
    for i, r in enumerate(df.itertuples()):
        if last_season is not None and r.season != last_season:
            for t in net:
                net[t] *= (1 - regress)
        last_season = r.season
        h, a = r._3, r._4
        nh, na = net.get(h, 0.0), net.get(a, 0.0)
        neutral = isinstance(r._6, str) and r._6.strip() != ""
        probs[i] = norm.cdf((nh - na + (0 if neutral else home_int)) / sigma)
        if ho[df.index[i]] > 0 and ao[df.index[i]] > 0:
            rr_h = hs[df.index[i]] / ho[df.index[i]]
            rr_a = as_[df.index[i]] / ao[df.index[i]]
            if np.isfinite(rr_h) and np.isfinite(rr_a):
                d = rr_h - rr_a
                net[h] = (1 - alpha) * nh + alpha * d
                net[a] = (1 - alpha) * na - alpha * d
    return probs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout", action="store_true")
    args = ap.parse_args()

    df = fb.load_all()
    decided = df.Winner.isin(["H", "A"])
    y_all = (df.Winner == "H").astype(float).values

    # --- tune on the pre-odds era only ---
    train = decided & (df.season <= 2017)
    best, best_ll = None, np.inf
    for k, adv, reg, sc in itertools.product(
            [10, 20, 30, 40], [5, 15, 30, 50], [0.0, 0.25, 0.5], [80, 120, 200]):
        p = run_elo(df, k, adv, reg, sc)
        cur = fb.ll(p[train], y_all[train]).mean()
        if cur < best_ll:
            best_ll, best = cur, (k, adv, reg, sc)
    k, adv, reg, sc = best
    print(f"tuned on 2011-2017 (n={int(train.sum())}): K={k} home_adv={adv} "
          f"regress={reg} scale={sc}  LL(train)={best_ll:.5f}")
    p_elo = run_elo(df, k, adv, reg, sc)

    # iteration 2: NRR ratings + home intercept + blend, same train era
    best2, best2_ll = None, np.inf
    for al, reg2, sig, hi in itertools.product(
            [0.1, 0.2, 0.3], [0.0, 0.25], [1.5, 2.0, 3.0], [0.0, 0.15, 0.3]):
        p = run_nrr(df, al, reg2, sig, hi)
        cur = fb.ll(p[train], y_all[train]).mean()
        if cur < best2_ll:
            best2_ll, best2 = cur, (al, reg2, sig, hi)
    al, reg2, sig, hi = best2
    print(f"NRR tuned: alpha={al} regress={reg2} sigma={sig} home_int={hi}  "
          f"LL(train)={best2_ll:.5f}")
    p_nrr = run_nrr(df, al, reg2, sig, hi)
    best_w, best_w_ll = 0.5, np.inf
    for w in np.arange(0.0, 1.01, 0.1):
        cur = fb.ll(w * p_elo[train] + (1 - w) * p_nrr[train],
                    y_all[train]).mean()
        if cur < best_w_ll:
            best_w_ll, best_w = cur, w
    print(f"blend: w_elo={best_w:.1f}  LL(train)={best_w_ll:.5f}")
    p_model = best_w * p_elo + (1 - best_w) * p_nrr

    ev = fb.eval_pop(df)
    ev["p_model"] = p_model[ev.index]

    for name, seasons in [("dev", fb.DEV_SEASONS)] + (
            [("HOLDOUT (scored once)", fb.HOLDOUT_SEASONS)] if args.holdout else []):
        sub = ev[ev.season.isin(seasons)]
        lm = fb.ll(sub.p_model, sub.home_win)
        lo = fb.ll(sub.p_open, sub.home_win)
        d = (lm - lo)
        t = d.mean() / (d.std(ddof=1) / np.sqrt(len(d)))
        print(f"\n== {name}: n={len(sub)} ==")
        print(f"LL(model)={lm.mean():.5f}  LL(open)={lo.mean():.5f}  "
              f"model-open={d.mean():+.5f} (t={t:.1f})")
        print(f"calibration: mean P(home)={sub.p_model.mean():.3f} vs "
              f"home rate {sub.home_win.mean():.3f} "
              f"({100*(sub.p_model.mean()-sub.home_win.mean()):+.1f}pp)")
        lc = fb.ll(sub.p_close, sub.home_win)
        dc = lm - lc
        tc = dc.mean() / (dc.std(ddof=1) / np.sqrt(len(dc)))
        print(f"vs close: {dc.mean():+.5f} (t={tc:.1f})")
        if "HOLDOUT" in name:
            roi(sub)


def roi(sub):
    """Flat $1 at the multi-book-average open where model EV > threshold,
    plus the devigged-open placebo."""
    for label, p in [("model", sub.p_model.values),
                     ("PLACEBO (devigged open)", sub.p_open.values)]:
        oh = sub["Home Odds Open"].astype(float).values
        oa = sub["Away Odds Open"].astype(float).values
        ev_h = p * oh - 1
        ev_a = (1 - p) * oa - 1
        side_h = ev_h >= ev_a
        edge = np.where(side_h, ev_h, ev_a)
        won = np.where(side_h, sub.home_win == 1, sub.home_win == 0)
        pnl = np.where(won, np.where(side_h, oh, oa) - 1, -1.0)
        print(f"ROI at average open [{label}]:")
        for thr in (0.02, 0.05):
            take = edge > thr
            n = int(take.sum())
            if n == 0:
                print(f"  EV>{thr:.0%}: 0 bets")
                continue
            r = pnl[take]
            t = r.mean() / (r.std(ddof=1) / np.sqrt(n)) if n > 1 else np.nan
            print(f"  EV>{thr:.0%}: n={n}  ROI={r.mean()*100:+.2f}% (t={t:.1f})")


if __name__ == "__main__":
    main()
