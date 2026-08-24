"""NHL attempts observation model (registration N2, PROGRESS.md Market 4).

Two Kalman states per player, the usg/eff template:
  att = shot attempts per TOI-minute   (obs attempts/toi, R = rvar/toi)
  og  = on-goal fraction               (obs sog/attempts, R = rvar/attempts,
                                        update only when attempts > 0)
Predicted SOG/min = att x og. Attempts from api-web pbp aggregates
(fetch_nhl_pbp.py); the pre-registered QC gate (qc_nhl_pbp.py) must pass
before tuning. Curves/rvar/q/p0 per state fit and tuned strictly before
2019-07-01 (grids and conventions exactly as talent_nhl.py, whose generic
machinery this module reuses).

Usage: python3 src/talent_nhl2.py           # N2-G1 validation
       python3 src/talent_nhl2.py --build   # write data/talent_nhl2.pkl
"""
import argparse
import glob
import os

import numpy as np
import pandas as pd

import talent_nhl as T

ROOT = os.path.join(os.path.dirname(__file__), "..")
STATS2 = ("att", "og")


def load_with_attempts():
    d = T.load_played()
    files = sorted(glob.glob(os.path.join(ROOT, "data", "nhl_pbp",
                                          "attempts_*.parquet")))
    if not files:
        raise SystemExit("no attempts parquet - run fetch_nhl_pbp.py")
    att = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    att = att[["game_id", "pid", "attempts"]]
    d = d.merge(att, on=["game_id", "pid"], how="left")
    d["y_att"] = d.attempts / d.toi_min
    d["den_att"] = d.toi_min.where(d.attempts.notna(), 0.0)
    d["y_og"] = np.where(d.attempts > 0, d.sog / d.attempts.replace(0, np.nan),
                         np.nan)
    d["den_og"] = d.attempts.fillna(0.0)
    return d


def tune(d, stats):
    curves = T.fit_curves(d, T.TUNE_CUTOFF, stats)
    rvar = T.fit_rvar(d, T.TUNE_CUTOFF, stats)
    pre = d[d.date < T.TUNE_CUTOFF].reset_index(drop=True)
    best = {}
    for st in stats:
        best_mse, best_qp = np.inf, None
        for q in T.GRID_Q:
            for p0 in T.GRID_P0:
                pred = T.run_filter(pre, curves, rvar, q, p0, st)
                mse = T.rate_mse(pre, pred, st, "2010-01-01", T.TUNE_CUTOFF)
                if mse < best_mse:
                    best_mse, best_qp = mse, (q, p0)
        best[st] = best_qp
        print(f"{st}: tuned pre-{T.TUNE_CUTOFF} q={best_qp[0]} p0={best_qp[1]}")
    return curves, rvar, best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    args = ap.parse_args()

    d = load_with_attempts()
    cov = d.attempts.notna().mean()
    print(f"rows {len(d)}, attempts coverage {cov:.2%}")
    curves, rvar, best = tune(d, list(STATS2))
    pred_att = T.run_filter(d, curves, rvar, *best["att"], "att")
    pred_og = T.run_filter(d, curves, rvar, *best["og"], "og")
    pred_rate = pred_att * pred_og

    if args.build:
        out = d[["pid", "game_id"]].copy()
        out["talent_sog2"] = pred_rate
        out.to_pickle(os.path.join(ROOT, "data", "talent_nhl2.pkl"))
        print(f"talent_nhl2.pkl written: {len(out)} rows, "
              f"params fit/tuned < {T.TUNE_CUTOFF}")
        return

    # N2-G1: per-game SOG MSE vs the registration-N engine and the EW
    # blend, same window/rows/metric as N-G1.
    d = T.add_ew_baselines(d)
    curves1 = T.fit_curves(d, T.TUNE_CUTOFF, ["sog"])
    rvar1 = T.fit_rvar(d, T.TUNE_CUTOFF, ["sog"])
    pre = d[d.date < T.TUNE_CUTOFF].reset_index(drop=True)
    best1, best_mse1 = None, np.inf
    for q in T.GRID_Q:
        for p0 in T.GRID_P0:
            p = T.run_filter(pre, curves1, rvar1, q, p0, "sog")
            m = T.rate_mse(pre, p, "sog", "2010-01-01", T.TUNE_CUTOFF)
            if m < best_mse1:
                best_mse1, best1 = m, (q, p0)
    pred_n = T.run_filter(d, curves1, rvar1, *best1, "sog")

    toi_blend = (T.W_FAST * d.toi_ewf
                 + (1 - T.W_FAST) * d.toi_ews).fillna(d.toi_ewf).to_numpy(float)
    inc = (T.W_FAST * d.sog_ewf + (1 - T.W_FAST) * d.sog_ews).fillna(
        d.sog_ewf).to_numpy(float)
    act = d.sog.to_numpy(float)
    m = ((d.date >= T.VAL_LO) & (d.date < T.VAL_HI)
         & (d.toi_min >= T.TOI_FLOOR) & np.isfinite(inc)
         & np.isfinite(act)).to_numpy()
    m &= np.isfinite(pred_rate) & np.isfinite(pred_n) & np.isfinite(toi_blend)
    mse2 = float(np.mean((act[m] - pred_rate[m] * toi_blend[m]) ** 2))
    msen = float(np.mean((act[m] - pred_n[m] * toi_blend[m]) ** 2))
    msee = float(np.mean((act[m] - inc[m]) ** 2))
    print(f"\nN2-G1 walk-forward, {T.VAL_LO}..{T.VAL_HI}, toi>={T.TOI_FLOOR:g}"
          f", per-game SOG MSE (n={m.sum()}):")
    print(f"  attempts engine (att x og) = {mse2:.5f}")
    print(f"  N sog engine               = {msen:.5f}")
    print(f"  EW blend                   = {msee:.5f}")
    print(f"N2-G1: {'PASS' if mse2 < msen else 'FAIL'} "
          f"(must beat the N engine)")


if __name__ == "__main__":
    main()
