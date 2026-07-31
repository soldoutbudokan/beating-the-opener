"""BBL player-composition model (fp programme, PROGRESS.md Market 2,
player-model registration of 2026-07-31 session 3).

Per-player batting/bowling values from cross-league T20 ball-by-ball
(league-adjusted runs-per-ball above/below the league's EW mean, decayed
per match), aggregated over a strictly-prior EXPECTED XI (appearance-
weighted from the team's previous matches — never tonight's XI or the
toss, which postdate the opener). Expected margin = 120 balls x value
diff + home advantage; P(home) via a Normal. All hyperparameters tuned on
pre-2018 BBL only (the pre-odds era).

Usage: python3 src/fp_player_model.py            # tune + dev gates
       python3 src/fp_player_model.py --holdout  # + reserved holdout ONCE
"""
import argparse
import itertools
import os
from collections import defaultdict

import numpy as np
import pandas as pd
from scipy.stats import norm

import fp_benchmark as fb

ROOT = os.path.join(os.path.dirname(__file__), "..")
APP_ALPHA = 0.35          # appearance EW per team match (fixed)


def per_match_tables():
    d = pd.read_parquet(os.path.join(ROOT, "data", "deliveries_cs.parquet"))
    m = pd.read_parquet(os.path.join(ROOT, "data", "matches_cs.parquet"))
    bat = (d.groupby(["match_id", "batter"])
           .agg(balls=("runs_batter", "size"), runs=("runs_batter", "sum"))
           .reset_index())
    bowl = (d.groupby(["match_id", "bowler"])
            .agg(balls=("runs_total", "size"), runs=("runs_total", "sum"),
                 wkts=("wicket", "sum")).reset_index())
    return m, bat, bowl


def ratings_pass(m, bat, bowl, alpha):
    """Chronological pass over ALL matches; returns for each match the
    pre-match expected-XI values per team. Ratings in runs-per-ball vs the
    competition's EW mean."""
    bat_g = {k: v for k, v in bat.groupby("match_id")}
    bowl_g = {k: v for k, v in bowl.groupby("match_id")}
    bval = defaultdict(float)   # batter value (rpb above league)
    bwt = defaultdict(float)    # confidence weight (balls seen, decayed)
    oval = defaultdict(float)   # bowler value (rpb below league)
    owt = defaultdict(float)
    lg_rpb = defaultdict(lambda: 1.25)   # per-comp EW runs per ball
    roster = defaultdict(dict)  # team -> {player: appearance EW}
    out = {}
    for r in m.itertuples():
        # --- pre-match expected-XI team values (prior info only) ---
        vals = {}
        for team in (r.team1, r.team2):
            ros = roster[team]
            if ros:
                top = sorted(ros.items(), key=lambda kv: -kv[1])[:11]
                wsum = sum(w for _, w in top) or 1.0
                tb = sum(w * bval[p] * min(bwt[p] / 100, 1) for p, w in top) / wsum
                to = sum(w * oval[p] * min(owt[p] / 100, 1) for p, w in top) / wsum
            else:
                tb = to = 0.0
            vals[team] = (tb, to)
        out[r.match_id] = vals
        # --- post-match updates ---
        lg = lg_rpb[r.comp]
        for tbl, val, wt, sign in ((bat_g.get(r.match_id), bval, bwt, 1),
                                   (bowl_g.get(r.match_id), oval, owt, -1)):
            if tbl is None:
                continue
            col = "batter" if sign == 1 else "bowler"
            for p, balls, runs in zip(tbl[col], tbl.balls, tbl.runs):
                delta = sign * (runs / balls - lg)
                a = 1 - (1 - alpha) ** balls
                val[p] = (1 - a) * val[p] + a * delta
                wt[p] = wt[p] * (1 - alpha) ** balls + balls
        both = pd.concat([x for x in (bat_g.get(r.match_id),
                                      bowl_g.get(r.match_id)) if x is not None])
        if len(both):
            tot_runs = bat_g.get(r.match_id)
            if tot_runs is not None and tot_runs.balls.sum() > 60:
                lg_rpb[r.comp] = (0.99 * lg
                                  + 0.01 * tot_runs.runs.sum() / tot_runs.balls.sum())
        played = set()
        for tbl, col in ((bat_g.get(r.match_id), "batter"),
                         (bowl_g.get(r.match_id), "bowler")):
            if tbl is not None:
                played |= set(tbl[col])
        for team, xi in ((r.team1, r.xi1), (r.team2, r.xi2)):
            names = set(xi) if len(xi) else played
            ros = roster[team]
            for p in list(ros):
                ros[p] *= (1 - APP_ALPHA)
            for p in names:
                ros[p] = ros.get(p, 0.0) + APP_ALPHA
    return out


def bbl_frame(vals):
    """Join xlsx benchmark rows to cricsheet team values by date+teams."""
    m = pd.read_parquet(os.path.join(ROOT, "data", "matches_cs.parquet"))
    bblm = m[m.comp == "bbl"].copy()
    key = {}
    for r in bblm.itertuples():
        key[(r.date, r.team1)] = (r.match_id, r.team1, r.team2)
        key[(r.date, r.team2)] = (r.match_id, r.team1, r.team2)
    df = fb.load_all()
    rows = []
    for r in df.itertuples():
        k = (r.Date.strftime("%Y-%m-%d"), r._3)
        hit = key.get(k)
        if hit is None:
            rows.append(np.nan)
            continue
        mid, t1, t2 = hit
        home = r._3
        away = t2 if home == t1 else t1
        v = vals[mid]
        (hb, ho), (ab, ao) = v[home], v[away]
        rows.append(120 * ((hb + ho) - (ab + ao)))
    df["diff"] = rows
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout", action="store_true")
    args = ap.parse_args()

    m, bat, bowl = per_match_tables()
    train_mask = lambda df: df.season <= 2017

    best, best_ll = None, np.inf
    for alpha in (0.002, 0.005, 0.01):
        vals = ratings_pass(m, bat, bowl, alpha)
        df = bbl_frame(vals)
        tr = df[train_mask(df) & df.Winner.isin(["H", "A"]) & df["diff"].notna()]
        y = (tr.Winner == "H").astype(float).values
        for sigma, home_adv in itertools.product((15, 25, 40), (0, 3, 6)):
            p = norm.cdf((tr["diff"].values + home_adv) / sigma)
            cur = fb.ll(p, y).mean()
            if cur < best_ll:
                best_ll, best = cur, (alpha, sigma, home_adv, vals)
    alpha, sigma, home_adv, vals = best
    print(f"tuned on pre-2018 BBL: alpha={alpha} sigma={sigma} "
          f"home_adv={home_adv}  LL(train)={best_ll:.5f}")

    df = bbl_frame(vals)
    ev = fb.eval_pop(df)
    ev = ev[ev["diff"].notna()].copy()
    ev["p_model"] = norm.cdf((ev["diff"].values + home_adv) / sigma)

    for name, seasons in [("dev", fb.DEV_SEASONS)] + (
            [("RESERVED HOLDOUT (scored once)", fb.HOLDOUT_SEASONS)]
            if args.holdout else []):
        sub = ev[ev.season.isin(seasons)]
        lm, lo = fb.ll(sub.p_model, sub.home_win), fb.ll(sub.p_open, sub.home_win)
        d = lm - lo
        t = d.mean() / (d.std(ddof=1) / np.sqrt(len(d)))
        print(f"\n== {name}: n={len(sub)} ==")
        print(f"LL(model)={lm.mean():.5f}  LL(open)={lo.mean():.5f}  "
              f"model-open={d.mean():+.5f} (t={t:.1f})")
        cal = sub.p_model.mean() - sub.home_win.mean()
        sd2 = 2 * np.sqrt(sub.home_win.mean() * (1 - sub.home_win.mean())
                          / len(sub))
        print(f"calibration: {100*cal:+.1f}pp  [G2 gate: |.| <= 2 sigma = "
              f"{100*sd2:.1f}pp]")
        lc = fb.ll(sub.p_close, sub.home_win)
        dc = lm - lc
        print(f"vs close: {dc.mean():+.5f} "
              f"(t={dc.mean()/(dc.std(ddof=1)/np.sqrt(len(dc))):.1f})")
        if "HOLDOUT" in name:
            import fp_model as team_model
            team_model.roi(sub)


if __name__ == "__main__":
    main()
