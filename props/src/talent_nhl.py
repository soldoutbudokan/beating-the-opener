"""NHL talent engine (registration N, PROGRESS.md Market 4 revisit).

The WNBA T1 Kalman architecture (wnba/src/talent.py) ported to NHL skater
per-TOI-minute rates:

  predict:  x <- x + trajectory_drift(career games)   [informed prior]
            P <- P + Q   (10x Q across a season boundary: offseason drift)
  update:   observation y = stat/toi_min, noise R = rvar/toi_min
            K = P/(P+R);  x <- x + K(y - x);  P <- (1-K)P

Registered adaptations (gates in PROGRESS.md, pushed before this file):
position groups {C, L->F, R->F, D}; BUCKET=40 / MAX_BUCKET=20 (curves flat
past 800 career games); season key from game_id (NHL seasons straddle the
new year - dt.year is banned); curves/rvar/q/p0 fit and tuned strictly on
games before 2019-07-01, in validation AND build mode. Data: training era
data/nhl_hist/skater_box_*.parquet (2010-11..2023-24, api-web) + eval era
data/nhl/skater_box_*.parquet (2024-25..2025-26) - the eval fetch, panel,
modelset and benchmark are untouched (isolation rule). The pre-registered
QC gate (qc_nhl_hist.py) must pass before tuning runs.

Usage: python3 src/talent_nhl.py           # N-G1 validation (2019-20..2023-24)
       python3 src/talent_nhl.py --build   # write data/talent_nhl.pkl
                                           #  (states over all data; params
                                           #   from pre-2019-07-01 only)
"""
import argparse
import glob
import os

import numpy as np
import pandas as pd

ROOT = os.path.join(os.path.dirname(__file__), "..")

RAW = {"sog": "sog", "blk": "blocked", "g": "goals", "a": "assists",
       "p": "points"}
GATE_STATS = ("sog", "blk")            # the registered cell candidates
BUCKET = 40          # career-game bucket (82-game seasons; registered)
MAX_BUCKET = 20      # curves flat beyond 800 career games
POSMAP = {"C": "C", "L": "F", "R": "F", "D": "D"}
GRID_Q = (1e-4, 3e-4, 1e-3, 3e-3)     # process noise, units of rvar
GRID_P0 = (0.05, 0.15, 0.5)           # initial variance, units of rvar
OFFSEASON_Q_MULT = 10.0
TUNE_CUTOFF = "2019-07-01"            # everything fit/tuned before this
VAL_LO, VAL_HI = "2019-07-01", "2024-07-01"   # N-G1 window (2019-20..2023-24)
ALPHA_F, ALPHA_S = 0.15, 0.05         # the incumbent NHL EW alphas
W_FAST = 0.6                          # the incumbent fast/slow blend weight
TOI_FLOOR = 5.0                       # registered N-G1 row filter


def toi_min(s):
    t = s.fillna("00:00").astype(str).str.split(":", expand=True)
    return (pd.to_numeric(t[0], errors="coerce").fillna(0)
            + pd.to_numeric(t[1], errors="coerce").fillna(0) / 60)


def shift_ew(s, key, alpha):
    prev = s.groupby(key, sort=False).shift(1)
    return prev.groupby(key, sort=False).transform(
        lambda x, a=alpha: x.ewm(alpha=a, min_periods=1).mean())


def load_played():
    files = (sorted(glob.glob(os.path.join(ROOT, "data", "nhl_hist",
                                           "skater_box_*.parquet")))
             + sorted(glob.glob(os.path.join(ROOT, "data", "nhl",
                                             "skater_box_*.parquet"))))
    if not files:
        raise SystemExit("no skater_box parquet found - run the fetches")
    d = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    d["toi_min"] = toi_min(d.toi)
    d = d[d.toi_min > 0].copy()
    d["pos"] = d.pos.astype(str).str[0].map(POSMAP).fillna("F")
    d["date"] = d.date.astype(str)
    d = d.sort_values(["pid", "date", "game_id"]).reset_index(drop=True)
    d["season"] = (d.game_id // 1_000_000).astype(int)
    d["gp"] = d.groupby("pid").cumcount()
    for st, col in RAW.items():
        d[f"y_{st}"] = d[col] / d.toi_min
        d[f"den_{st}"] = d.toi_min
    return d


def add_ew_baselines(d):
    """Walk-forward incumbent comparators, computed over the full history
    with the panel's own convention (shift-then-ewm, alphas f/s)."""
    for st, col in RAW.items():
        d[f"{st}_ewf"] = shift_ew(d[col], d.pid, ALPHA_F)
        d[f"{st}_ews"] = shift_ew(d[col], d.pid, ALPHA_S)
    d["toi_ewf"] = shift_ew(d.toi_min, d.pid, ALPHA_F)
    d["toi_ews"] = shift_ew(d.toi_min, d.pid, ALPHA_S)
    return d


def fit_curves(d, cutoff, stats):
    f = d[d.date < cutoff].copy()
    f["bucket"] = np.minimum(f.gp // BUCKET, MAX_BUCKET)
    curves = {}
    for st in stats:
        g = (f.assign(w=f[f"den_{st}"], wy=f[f"den_{st}"] * f[f"y_{st}"])
             .groupby(["pid", "pos", "bucket"])[["w", "wy"]].sum())
        g = (g.wy / g.w).rename("rate").reset_index()
        g = g.sort_values(["pid", "bucket"])
        g["d_rate"] = g.groupby("pid").rate.diff()
        g["from_b"] = g.groupby("pid").bucket.shift(1)
        steps = g[(g.bucket - g.from_b) == 1]
        for pos in ("C", "F", "D"):
            base = g[(g.pos == pos) & (g.bucket == 0)].rate.mean()
            inc = (steps[steps.pos == pos].groupby("from_b").d_rate.mean()
                   .reindex(range(MAX_BUCKET), fill_value=0.0)
                   .rolling(3, center=True, min_periods=1).mean())
            curves[(pos, st)] = (float(base), inc.cumsum().to_numpy(float))
    return curves


def curve_level(curves, pos, st, gp):
    base, inc = curves[(pos, st)]
    b = min(int(gp // BUCKET), MAX_BUCKET)
    return base + (inc[b - 1] if b > 0 else 0.0)


def fit_rvar(d, cutoff, stats):
    f = d[(d.date < cutoff) & (d.gp >= 10)]
    out = {}
    for st in stats:
        den = f[f"den_{st}"]
        ok = f[f"y_{st}"].notna() & (den > 0)
        fo, do = f[ok], den[ok]
        pm = fo.groupby("pid")[f"y_{st}"].transform("mean")
        out[st] = float((do * (fo[f"y_{st}"] - pm) ** 2).sum()
                        / do.sum() * do.mean())
    return out


def run_filter(d, curves, rvar, q, p0, st):
    xs, ps, last_season = {}, {}, {}
    pred = np.empty(len(d))
    pid = d.pid.to_numpy()
    pos = d.pos.to_numpy()
    gp = d.gp.to_numpy()
    sea = d.season.to_numpy()
    mins = d[f"den_{st}"].to_numpy(float)
    y = d[f"y_{st}"].to_numpy(float)
    R0, Q, P0 = rvar[st], q * rvar[st], p0 * rvar[st]
    for i in range(len(d)):
        a = pid[i]
        if a not in xs:
            xs[a] = curve_level(curves, pos[i], st, 0)
            ps[a] = P0
            last_season[a] = sea[i]
        else:
            drift = (curve_level(curves, pos[i], st, gp[i])
                     - curve_level(curves, pos[i], st, max(gp[i] - 1, 0)))
            xs[a] += drift
            ps[a] += Q * (OFFSEASON_Q_MULT if sea[i] != last_season[a]
                          else 1.0)
            last_season[a] = sea[i]
        pred[i] = xs[a]
        if np.isfinite(y[i]) and mins[i] > 0:
            R = R0 / mins[i]
            k = ps[a] / (ps[a] + R)
            xs[a] += k * (y[i] - xs[a])
            ps[a] *= (1 - k)
    return pred


def rate_mse(d, pred, st, lo, hi):
    """Tuning metric: TOI-weighted one-step rate MSE (the T1 convention)."""
    m = ((d.date >= lo) & (d.date < hi) & (d.toi_min >= TOI_FLOOR)
         & d[f"y_{st}"].notna() & (d[f"den_{st}"] > 0))
    w = d[f"den_{st}"][m].to_numpy(float)
    err = (d[f"y_{st}"][m].to_numpy(float) - pred[m.to_numpy()]) ** 2
    return float((w * err).sum() / w.sum())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true",
                    help="write data/talent_nhl.pkl (params pre-2019-07-01)")
    args = ap.parse_args()

    d = load_played()
    print(f"rows {len(d)}, players {d.pid.nunique()}, "
          f"seasons {d.season.min()}-{d.season.max()}")
    stats = list(RAW)
    curves = fit_curves(d, TUNE_CUTOFF, stats)
    rvar = fit_rvar(d, TUNE_CUTOFF, stats)

    pre = d[d.date < TUNE_CUTOFF].reset_index(drop=True)
    best = {}
    for st in stats:
        best_mse, best_qp = np.inf, None
        for q in GRID_Q:
            for p0 in GRID_P0:
                pred = run_filter(pre, curves, rvar, q, p0, st)
                mse = rate_mse(pre, pred, st, "2010-01-01", TUNE_CUTOFF)
                if mse < best_mse:
                    best_mse, best_qp = mse, (q, p0)
        best[st] = best_qp
        print(f"{st}: tuned pre-{TUNE_CUTOFF} q={best_qp[0]} p0={best_qp[1]}")

    if args.build:
        out = d[["pid", "game_id"]].copy()
        for st in stats:
            q, p0 = best[st]
            out[f"talent_{st}"] = run_filter(d, curves, rvar, q, p0, st)
        out.to_pickle(os.path.join(ROOT, "data", "talent_nhl.pkl"))
        print(f"talent_nhl.pkl written: {len(out)} rows, "
              f"params fit/tuned < {TUNE_CUTOFF}")
        return

    # N-G1: per-game stat MSE, engine (talent_rate x toi blend) vs the
    # incumbent per-game EW blend, seasons 2019-20..2023-24, toi>=5.
    d = add_ew_baselines(d)
    print(f"\nN-G1 walk-forward validation, {VAL_LO}..{VAL_HI}, "
          f"toi_min>={TOI_FLOOR:g}, per-game stat MSE:")
    toi_blend = (W_FAST * d.toi_ewf + (1 - W_FAST) * d.toi_ews).fillna(
        d.toi_ewf)
    verdicts = {}
    for st in stats:
        q, p0 = best[st]
        pred = run_filter(d, curves, rvar, q, p0, st)
        eng = pred * toi_blend.to_numpy(float)
        inc = (W_FAST * d[f"{st}_ewf"] + (1 - W_FAST) * d[f"{st}_ews"]).fillna(
            d[f"{st}_ewf"]).to_numpy(float)
        act = d[RAW[st]].to_numpy(float)
        m = ((d.date >= VAL_LO) & (d.date < VAL_HI)
             & (d.toi_min >= TOI_FLOOR) & np.isfinite(inc)
             & np.isfinite(eng) & np.isfinite(act)).to_numpy()
        mse_t = float(np.mean((act[m] - eng[m]) ** 2))
        mse_e = float(np.mean((act[m] - inc[m]) ** 2))
        verdicts[st] = mse_t < mse_e
        tag = "TALENT" if verdicts[st] else "ew"
        print(f"  {st}: talent={mse_t:.5f}  ew={mse_e:.5f}  {tag}"
              f"  (n={m.sum()})")
    cell = [{"sog": "shots", "blk": "blocked_shots"}[s]
            for s in GATE_STATS if verdicts[s]]
    print(f"N-G1 gate stats: sog={'PASS' if verdicts['sog'] else 'FAIL'} "
          f"blk={'PASS' if verdicts['blk'] else 'FAIL'} -> registered cell: "
          f"{cell if cell else 'EMPTY (stop; nothing touches dev/holdout)'}")


if __name__ == "__main__":
    main()
