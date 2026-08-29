"""Stage B for the cricket revisit (registration P, PROGRESS.md): score the
frozen 2026-07-31 player-composition model on the Polymarket benchmark
population built by pm_benchmark.py.

Iteration 1: (alpha, sigma, home_adv) tuned on pre-2018 BBL only — the
2026-07-31 rule, re-derived on the enlarged competition set.
Iteration 2 (--iter2, only if 1 fails P-G1): same grid re-tuned on all
Cricsheet matches dated before 2024-06-01 (pre-benchmark era, market-free).
Home advantage applies only when outcome 0's team shares a name token with
the venue city (neutral tournament cricket gets 0).

Usage: python3 src/pm_model.py [--iter2]
"""
import argparse
import itertools
import os
import re

import numpy as np
import pandas as pd
from scipy.stats import norm

import fp_benchmark as fb
import fp_player_model as pm
from pm_benchmark import ll, clustered_t

ROOT = os.path.join(os.path.dirname(__file__), "..")
GRID = {"alpha": (0.002, 0.005, 0.01), "sigma": (15, 25, 40), "home": (0, 3, 6)}
PRE_BENCH = "2024-06-01"
SPREAD = 0.01
STOP = {"the", "and", "of", "super", "kings", "royals", "capitals", "titans",
        "indians", "giants", "riders", "knight", "challengers", "sunrisers",
        "united", "gladiators", "zalmi", "sultans", "qalandars", "heat",
        "sixers", "thunder", "stars", "renegades", "strikers", "scorchers",
        "hurricanes", "fire", "spirit", "brave", "phoenix", "rockets",
        "originals", "invincibles", "warriors", "patriots", "tallawahs",
        "amazon", "falcons", "cricket", "club", "xi"}


def home_flag(team, city):
    toks = {w for w in re.sub(r"[^a-z ]", " ", str(team).lower()).split()
            if len(w) >= 4 and w not in STOP}
    ctoks = set(re.sub(r"[^a-z ]", " ", str(city).lower()).split())
    return int(bool(toks & ctoks))


def tune_bbl(m, bat, bowl):
    """The 2026-07-31 rule verbatim: grid on pre-2018 BBL rows of the asb
    benchmark frame."""
    best, best_ll = None, np.inf
    for alpha in GRID["alpha"]:
        vals = pm.ratings_pass(m, bat, bowl, alpha)
        df = pm.bbl_frame(vals)
        tr = df[(df.season <= 2017) & df.Winner.isin(["H", "A"]) & df["diff"].notna()]
        y = (tr.Winner == "H").astype(float).values
        for sigma, home in itertools.product(GRID["sigma"], GRID["home"]):
            cur = fb.ll(norm.cdf((tr["diff"].values + home) / sigma), y).mean()
            if cur < best_ll:
                best_ll, best = cur, (alpha, sigma, home, vals)
    return best, best_ll, len(tr)


def tune_prebench(m, bat, bowl):
    """Iteration 2: grid on all Cricsheet matches before PRE_BENCH, team1 as
    outcome 0, home rule as in scoring."""
    best, best_ll = None, np.inf
    tr = m[(m.date < PRE_BENCH) & (m.result == "normal") & m.winner.notna()]
    y = (tr.winner == tr.team1).astype(float).values
    h = np.array([home_flag(t, c) for t, c in zip(tr.team1, tr.city)])
    for alpha in GRID["alpha"]:
        vals = pm.ratings_pass(m, bat, bowl, alpha)
        diff = np.array([120 * ((vals[r.match_id][r.team1][0] + vals[r.match_id][r.team1][1])
                                - (vals[r.match_id][r.team2][0] + vals[r.match_id][r.team2][1]))
                         for r in tr.itertuples()])
        for sigma, home in itertools.product(GRID["sigma"], GRID["home"]):
            cur = fb.ll(norm.cdf((diff + home * h) / sigma), y).mean()
            if cur < best_ll:
                best_ll, best = cur, (alpha, sigma, home, vals)
    return best, best_ll, len(tr)


def roi(b, pcol, label):
    """Flat $1 at the open, paying a 1c spread; EV tiers; clustered t."""
    p = b[pcol].to_numpy(float)
    buy0, buy1 = np.minimum(b.p_open + SPREAD, 0.999), np.minimum(1 - b.p_open + SPREAD, 0.999)
    ev0, ev1 = p / buy0 - 1, (1 - p) / buy1 - 1
    side0 = ev0 >= ev1
    edge = np.where(side0, ev0, ev1)
    won = np.where(side0, b.y == 1, b.y == 0)
    pnl = np.where(won, np.where(side0, 1 / buy0, 1 / buy1) - 1, -1.0)
    print(f"ROI at Polymarket open (+1c spread), {label}:")
    for thr in (0.02, 0.05):
        take = edge > thr
        n = int(take.sum())
        if n == 0:
            print(f"  EV>{thr:.0%}: 0 bets"); continue
        d, t = clustered_t(pnl[take], b.date.values[take])
        print(f"  EV>{thr:.0%}: n={n}  ROI={d*100:+.2f}% (clustered t={t:.1f})  side0={int(side0[take].sum())}/{n}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iter2", action="store_true")
    args = ap.parse_args()
    m, bat, bowl = pm.per_match_tables()
    m = m.sort_values(["date", "match_id"]).reset_index(drop=True)
    if args.iter2:
        (alpha, sigma, home_adv, vals), tll, ntr = tune_prebench(m, bat, bowl)
        print(f"iteration 2: tuned on all Cricsheet < {PRE_BENCH} (n={ntr}): "
              f"alpha={alpha} sigma={sigma} home_adv={home_adv} LL(train)={tll:.5f}")
    else:
        (alpha, sigma, home_adv, vals), tll, ntr = tune_bbl(m, bat, bowl)
        print(f"iteration 1: tuned on pre-2018 BBL (n={ntr}): alpha={alpha} "
              f"sigma={sigma} home_adv={home_adv} LL(train)={tll:.5f}")

    b = pd.read_parquet(os.path.join(ROOT, "data", "pm_benchmark.parquet"))
    xw = pd.read_parquet(os.path.join(ROOT, "data", "pm_crosswalk.parquet"))
    b = b.merge(xw[xw.status == "ok"][["market_id", "match_id", "team1", "team2", "outcome0_is_team1"]], on="market_id")
    city = m.set_index("match_id").city
    diffs, homes = [], []
    for r in b.itertuples():
        v = vals.get(r.match_id)
        t0, t1 = (r.team1, r.team2) if r.outcome0_is_team1 else (r.team2, r.team1)
        if v is None or t0 not in v or t1 not in v:
            diffs.append(np.nan); homes.append(0); continue
        diffs.append(120 * ((v[t0][0] + v[t0][1]) - (v[t1][0] + v[t1][1])))
        homes.append(home_flag(t0, city.get(r.match_id, "")))
    b["diff"], b["home0"] = diffs, homes
    b = b[b["diff"].notna()].copy()
    b["p_model"] = norm.cdf((b["diff"] + home_adv * b.home0) / sigma)
    b["ll_model"], b["ll_open"], b["ll_close"] = ll(b.p_model, b.y), ll(b.p_open, b.y), ll(b.p_close, b.y)
    tag = "iteration 2" if args.iter2 else "iteration 1"
    print(f"\n== dev [{tag}]: n={len(b)} ({b.date.min()} .. {b.date.max()}), home0 share {b.home0.mean():.0%} ==")
    d, t = clustered_t((b.ll_model - b.ll_open).values, b.date)
    print(f"LL(model)={b.ll_model.mean():.5f}  LL(open T-24h)={b.ll_open.mean():.5f}  model-open={d:+.5f} (clustered t={t:.1f})"
          f"  [P-G1: <= +0.010]")
    cal = 100 * (b.p_model.mean() - b.y.mean())
    print(f"calibration: {cal:+.2f}pp  [P-G2: |.| <= 2.5pp]")
    d2, t2 = clustered_t((b.ll_model - b.ll_close).values, b.date)
    flag = "  ** TRIPWIRE **" if (d2 < -0.005 and t2 < -2) else ""
    print(f"vs pre-toss close: {d2:+.5f} (t={t2:.1f}){flag}")
    for c, g in b.groupby("comp"):
        dc, tc = clustered_t((g.ll_model - g.ll_open).values, g.date)
        print(f"  {c:5s} n={len(g):4d} gap={dc:+.4f} (t={tc:.1f}) cal={100*(g.p_model.mean()-g.y.mean()):+.1f}pp")
    roi(b, "p_model", "dev")
    roi(b.assign(p_model=b.p_open), "p_model", "dev PLACEBO")
    b.to_parquet(os.path.join(ROOT, "data", f"pm_preds_{'iter2' if args.iter2 else 'iter1'}.parquet"), index=False)


if __name__ == "__main__":
    main()
