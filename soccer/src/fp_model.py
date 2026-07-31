"""Stage B from-scratch soccer 1X2 model (PROGRESS.md Market 3).

Walk-forward, market-blind, per league: EW league scoring rates, per-team
multiplicative attack/defence ratings updated after each match,
lam_home = lg_home * att_H * def_A (away analogous), P(H/D/A) from the
independent-Poisson score matrix with a diagonal draw inflation (1+rho),
renormalized. rho and all update rates are tuned on seasons <= 2018-19
ONLY (pre-benchmark era; closing-average odds start 2019), walk-forward
within it. No odds column is ever read by the model.

Usage: python3 src/fp_model.py            # tune on <=2018-19, score dev
       python3 src/fp_model.py --holdout  # + score holdout once, with ROI
"""
import argparse
import itertools
import os

import numpy as np
import pandas as pd
from scipy import stats as sps

import fp_benchmark as fb

MAXG = 10
TRAIN_MAX_SEASON = "2018-19"
BURN_SEASONS = ("2008-09", "2009-10", "2010-11", "2011-12")


def run_ratings(m, alpha, beta, init_att, init_def, by_team=False):
    """One chronological pass; returns lam_h, lam_a arrays (pre-match).

    by_team=True keys ratings by team alone so they survive promotion /
    relegation instead of resetting to init (iteration 2; league scoring
    rates stay division-keyed)."""
    att, dfc = {}, {}
    lg_h, lg_a = {}, {}
    lam_h = np.full(len(m), np.nan)
    lam_a = np.full(len(m), np.nan)
    div = m.Div.values
    hg = m.FTHG.values
    ag = m.FTAG.values
    ht = m.HomeTeam.values
    at = m.AwayTeam.values
    for i in range(len(m)):
        d = div[i]
        gh, ga = lg_h.get(d, 1.45), lg_a.get(d, 1.15)
        if by_team:
            kh, ka = ht[i], at[i]
        else:
            kh, ka = (d, ht[i]), (d, at[i])
        ah = att.get(kh, init_att)
        dh = dfc.get(kh, init_def)
        aa = att.get(ka, init_att)
        da = dfc.get(ka, init_def)
        lh = gh * ah * da
        la = ga * aa * dh
        lam_h[i], lam_a[i] = lh, la
        if np.isfinite(hg[i]) and np.isfinite(ag[i]):
            att[kh] = ah * (1 - alpha) + alpha * hg[i] / max(gh * da, 0.2)
            dfc[ka] = da * (1 - alpha) + alpha * hg[i] / max(gh * ah, 0.2)
            att[ka] = aa * (1 - alpha) + alpha * ag[i] / max(ga * dh, 0.2)
            dfc[kh] = dh * (1 - alpha) + alpha * ag[i] / max(ga * aa, 0.2)
            lg_h[d] = gh * (1 - beta) + beta * hg[i]
            lg_a[d] = ga * (1 - beta) + beta * ag[i]
    return lam_h, lam_a


def probs_from_lam(lam_h, lam_a, rho):
    """Vectorized P(H/D/A) from Poisson score matrix, diagonal x (1+rho)."""
    lam_h = np.clip(lam_h, 0.05, 8.0)
    lam_a = np.clip(lam_a, 0.05, 8.0)
    ks = np.arange(MAXG + 1)
    ph = sps.poisson.pmf(ks[None, :], lam_h[:, None])   # (n, MAXG+1)
    pa = sps.poisson.pmf(ks[None, :], lam_a[:, None])
    joint = ph[:, :, None] * pa[:, None, :]             # (n, h, a)
    eye = np.eye(MAXG + 1)[None, :, :]
    joint = joint * (1 + rho * eye)
    joint /= joint.sum(axis=(1, 2), keepdims=True)
    p_h = np.triu(np.ones((MAXG + 1, MAXG + 1)), 1).T[None]  # h > a
    home = (joint * (np.tril(np.ones((MAXG + 1, MAXG + 1)), -1)[None])).sum((1, 2))
    draw = (joint * eye).sum((1, 2))
    away = 1.0 - home - draw
    return home, draw, away


def mll3(ph, pd_, pa, ftr):
    p = np.where(ftr == "H", ph, np.where(ftr == "D", pd_, pa))
    return -np.log(np.clip(p, 1e-9, 1.0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout", action="store_true")
    args = ap.parse_args()

    m = pd.read_pickle(os.path.join(fb.ROOT, "data", "matches.pkl"))
    m = m.sort_values("Date").reset_index(drop=True)
    ftr = m.FTR.values
    train = (m.FTR.isin(["H", "D", "A"])
             & (m.season <= TRAIN_MAX_SEASON)
             & ~m.season.isin(BURN_SEASONS)).values

    # iteration 1 grid hit its alpha/beta boundary; iteration 2 (final):
    # extended grid + team-keyed ratings that survive promotion/relegation.
    best, best_ll = None, np.inf
    for alpha, beta, rho, ini, byt in itertools.product(
            [0.005, 0.01, 0.02, 0.04], [0.002, 0.005, 0.01],
            [0.05, 0.1, 0.15], [(0.9, 1.1)], [True, False]):
        lh, la = run_ratings(m, alpha, beta, *ini, by_team=byt)
        H, D, A = probs_from_lam(lh[train], la[train], rho)
        cur = mll3(H, D, A, ftr[train]).mean()
        if cur < best_ll:
            best_ll, best = cur, (alpha, beta, rho, ini, byt)
    alpha, beta, rho, ini, byt = best
    print(f"tuned on <= {TRAIN_MAX_SEASON} (n={int(train.sum())}): "
          f"alpha={alpha} beta={beta} rho={rho} init={ini} by_team={byt}  "
          f"LL(train)={best_ll:.5f}")

    lh, la = run_ratings(m, alpha, beta, *ini, by_team=byt)
    H, D, A = probs_from_lam(lh, la, rho)
    m["pm_h"], m["pm_d"], m["pm_a"] = H, D, A

    ev = fb.load_eval()
    ev = ev.merge(m[["Div", "Date", "HomeTeam", "AwayTeam",
                     "pm_h", "pm_d", "pm_a"]],
                  on=["Div", "Date", "HomeTeam", "AwayTeam"], how="left")
    ev["ll_model"] = mll3(ev.pm_h.values, ev.pm_d.values, ev.pm_a.values,
                          ev.FTR.values)

    splits = [("dev", fb.DEV)] + (
        [("HOLDOUT (scored once)", fb.HOLDOUT)] if args.holdout else [])
    for name, seasons in splits:
        sub = ev[ev.season.isin(seasons)]
        d, t = fb.clustered_t((sub.ll_model - sub.ll_open).values, sub.Date)
        print(f"\n== {name}: n={len(sub)} ==")
        print(f"LL(model)={sub.ll_model.mean():.5f}  "
              f"LL(open)={sub.ll_open.mean():.5f}  "
              f"model-open={d:+.5f} (clustered t={t:.1f})")
        dc, tc = fb.clustered_t((sub.ll_model - sub.ll_close).values, sub.Date)
        print(f"vs close: {dc:+.5f} (clustered t={tc:.1f})")
        for out, pcol in [("H", "pm_h"), ("D", "pm_d"), ("A", "pm_a")]:
            rate = (sub.FTR == out).mean()
            print(f"    {out}: rate={rate:.4f} model={sub[pcol].mean():.4f} "
                  f"({100*(sub[pcol].mean()-rate):+.1f}pp)")
        if "HOLDOUT" in name:
            roi(sub)


def roi(sub):
    """Flat $1 at EAvg where model EV > threshold; devigged-open placebo."""
    odds = sub[["EAvgH", "EAvgD", "EAvgA"]].values
    y = np.select([sub.FTR == "H", sub.FTR == "D", sub.FTR == "A"], [0, 1, 2])
    for label, P in [("model", sub[["pm_h", "pm_d", "pm_a"]].values),
                     ("PLACEBO (devigged open)",
                      sub[["po_h", "po_d", "po_a"]].values)]:
        evs = P * odds - 1
        best_side = evs.argmax(axis=1)
        edge = evs.max(axis=1)
        won = best_side == y
        pnl = np.where(won, odds[np.arange(len(y)), best_side] - 1, -1.0)
        print(f"ROI at EAvg open [{label}]:")
        for thr in (0.02, 0.05):
            take = edge > thr
            n = int(take.sum())
            if n == 0:
                print(f"  EV>{thr:.0%}: 0 bets")
                continue
            d, t = fb.clustered_t(pnl[take], sub.Date.values[take])
            print(f"  EV>{thr:.0%}: n={n}  ROI={d*100:+.2f}% "
                  f"(clustered t={t:.1f})")


if __name__ == "__main__":
    main()
