"""DARKO-lite talent engine (v3 programme T1, PROGRESS.md Market 1 v3).

Per-player per-stat PER-MINUTE talent states, updated game by game with a
scalar Kalman filter:

  predict:  x <- x + trajectory_drift(career games)   [informed prior]
            P <- P + Q   (10x Q across a season boundary: offseason drift)
  update:   observation y = stat/minutes, noise R = rvar/minutes
            K = P/(P+R);  x <- x + K(y - x);  P <- (1-K)P

The trajectory drift comes from position-group career curves fit by the
delta method (within-player rate changes between adjacent career-game
buckets, averaged — no survivorship bias from comparing different players).
Rookies start at the position baseline with wide P0. This replaces the EW
blend as the rate estimator: unlike an EW average, the posterior regresses
toward an INFORMED prior, so hot streaks shrink back and career-stage
drift is expected rather than chased (the owner's mean-regression point).

Everything (curves, rvar, Q, P0 grid) is fit strictly before a cutoff;
validation is one-step-ahead, walk-forward, market-free (T1-G1).

Usage: python3 src/talent.py            # T1-G1 validation (2015-2024)
       python3 src/talent.py --build    # write data/talent.pkl (cutoff-free
                                        #  states for the prop model; curves
                                        #  and params from pre-2025 only)
"""
import argparse
import os
from collections import defaultdict

import numpy as np
import pandas as pd

ROOT = os.path.join(os.path.dirname(__file__), "..")

RAW = {"poi": "points", "reb": "rebounds", "ass": "assists",
       "tpm": "three_point_field_goals_made", "ste": "steals",
       "blo": "blocks", "tur": "turnovers"}
BUCKET = 20          # career-game bucket for trajectory curves
MAX_BUCKET = 20      # curves flat beyond 400 career games
POSMAP = {"G": "G", "F": "F", "C": "C"}
GRID_Q = (1e-4, 3e-4, 1e-3, 3e-3)     # process noise, units of rvar
GRID_P0 = (0.05, 0.15, 0.5)           # initial variance, units of rvar
OFFSEASON_Q_MULT = 10.0


def load_played(panel):
    d = panel[panel.minutes.notna() & (panel.minutes > 0)].copy()
    d["pos"] = (d.athlete_position_abbreviation.astype(str).str[0]
                .map(POSMAP).fillna("F"))
    d = d.sort_values(["athlete_id", "game_date", "game_id"]).reset_index()
    d["season_yr"] = pd.to_datetime(d.game_date).dt.year
    for st, col in RAW.items():
        d[f"y_{st}"] = d[col] / d.minutes
    return d


def fit_curves(d, cutoff):
    """Delta-method career curves per (pos, stat): rate level by career-game
    bucket, from within-player changes only. Fit rows strictly < cutoff."""
    f = d[d.game_date < cutoff].copy()
    f["bucket"] = np.minimum(f.gp // BUCKET, MAX_BUCKET)
    curves = {}
    for st in RAW:
        # per (player, bucket) minutes-weighted mean rate
        g = (f.assign(w=f.minutes, wy=f.minutes * f[f"y_{st}"])
             .groupby(["athlete_id", "pos", "bucket"])[["w", "wy"]].sum())
        g = (g.wy / g.w).rename("rate").reset_index()
        g = g.sort_values(["athlete_id", "bucket"])
        g["d_rate"] = g.groupby("athlete_id").rate.diff()
        g["from_b"] = g.groupby("athlete_id").bucket.shift(1)
        steps = g[(g.bucket - g.from_b) == 1]
        for pos in ("G", "F", "C"):
            base = g[(g.pos == pos) & (g.bucket == 0)].rate.mean()
            inc = (steps[steps.pos == pos].groupby("from_b").d_rate.mean()
                   .reindex(range(MAX_BUCKET), fill_value=0.0)
                   .rolling(3, center=True, min_periods=1).mean())
            curves[(pos, st)] = (float(base),
                                 inc.cumsum().to_numpy(float))
    return curves


def curve_level(curves, pos, st, gp):
    base, inc = curves[(pos, st)]
    b = min(int(gp // BUCKET), MAX_BUCKET)
    return base + (inc[b - 1] if b > 0 else 0.0)


def fit_rvar(d, cutoff):
    """Per-stat observation noise: minutes-weighted variance of single-game
    per-minute rates around the player's own mean (rows < cutoff)."""
    f = d[(d.game_date < cutoff) & (d.gp >= 10)]
    out = {}
    for st in RAW:
        pm = f.groupby("athlete_id")[f"y_{st}"].transform("mean")
        out[st] = float((f.minutes * (f[f"y_{st}"] - pm) ** 2).sum()
                        / f.minutes.sum() * f.minutes.mean())
    return out


def run_filter(d, curves, rvar, q, p0, st):
    """One pass; returns pre-game talent per row (aligned to d)."""
    xs = {}
    ps = {}
    last_season = {}
    pred = np.empty(len(d))
    aid = d.athlete_id.to_numpy()
    pos = d.pos.to_numpy()
    gp = d.gp.to_numpy()
    yr = d.season_yr.to_numpy()
    mins = d.minutes.to_numpy(float)
    y = d[f"y_{st}"].to_numpy(float)
    R0, Q, P0 = rvar[st], q * rvar[st], p0 * rvar[st]
    for i in range(len(d)):
        a = aid[i]
        if a not in xs:
            xs[a] = curve_level(curves, pos[i], st, 0)
            ps[a] = P0
            last_season[a] = yr[i]
        else:
            drift = (curve_level(curves, pos[i], st, gp[i])
                     - curve_level(curves, pos[i], st, max(gp[i] - 1, 0)))
            xs[a] += drift
            ps[a] += Q * (OFFSEASON_Q_MULT if yr[i] != last_season[a] else 1.0)
            last_season[a] = yr[i]
        pred[i] = xs[a]
        if np.isfinite(y[i]) and mins[i] > 0:
            R = R0 / mins[i]
            k = ps[a] / (ps[a] + R)
            xs[a] += k * (y[i] - xs[a])
            ps[a] *= (1 - k)
    return pred


def one_step_mse(d, pred, st, lo, hi):
    m = (d.game_date >= lo) & (d.game_date < hi) & (d.minutes >= 10)
    w = d.minutes[m].to_numpy(float)
    err = (d[f"y_{st}"][m].to_numpy(float) - pred[m.to_numpy()]) ** 2
    return float((w * err).sum() / w.sum())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true",
                    help="write data/talent.pkl with pre-2025-fit params")
    args = ap.parse_args()

    panel = pd.read_pickle(os.path.join(ROOT, "data", "panel.pkl"))
    d = load_played(panel)

    cutoff = "2025-01-01" if args.build else "2015-01-01"
    curves = fit_curves(d, cutoff)
    rvar = fit_rvar(d, cutoff)

    best = {}
    for st in RAW:
        best_mse, best_qp = np.inf, None
        for q in GRID_Q:
            for p0 in GRID_P0:
                pred = run_filter(d, curves, rvar, q, p0, st)
                mse = one_step_mse(d, pred, st, "2005-01-01", cutoff)
                if mse < best_mse:
                    best_mse, best_qp = mse, (q, p0)
        best[st] = best_qp
        print(f"{st}: tuned pre-{cutoff[:4]} q={best_qp[0]} p0={best_qp[1]}")

    if args.build:
        out = d[["athlete_id", "game_id"]].copy()
        for st in RAW:
            q, p0 = best[st]
            out[f"talent_{st}"] = run_filter(d, curves, rvar, q, p0, st)
        out.to_pickle(os.path.join(ROOT, "data", "talent.pkl"))
        print(f"talent.pkl written: {len(out)} rows, "
              f"curves/params fit < {cutoff}")
        return

    # T1-G1: 2015-2024 one-step validation vs the panel's fast EW rate
    print("\nT1-G1 walk-forward validation, 2015-2024, minutes>=10, "
          "minutes-weighted MSE (x1000):")
    wins = 0
    for st in RAW:
        q, p0 = best[st]
        pred = run_filter(d, curves, rvar, q, p0, st)
        mse_t = one_step_mse(d, pred, st, "2015-01-01", "2025-01-01")
        ew = d[f"{st}_rate_ewf"].to_numpy(float)
        m = ((d.game_date >= "2015-01-01") & (d.game_date < "2025-01-01")
             & (d.minutes >= 10) & np.isfinite(ew))
        w = d.minutes[m].to_numpy(float)
        mse_e = float((w * (d[f"y_{st}"][m].to_numpy(float)
                            - ew[m.to_numpy()]) ** 2).sum() / w.sum())
        better = mse_t < mse_e
        wins += better
        print(f"  {st}: talent={1000*mse_t:.4f}  ew={1000*mse_e:.4f}  "
              f"{'TALENT' if better else 'ew'}")
    print(f"talent wins {wins}/7 (gate: >=4 of poi/reb/ass/tpm/ste/tur)")


if __name__ == "__main__":
    main()
