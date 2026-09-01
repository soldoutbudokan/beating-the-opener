"""Post-hoc dev diagnostics for the cricket v2 model (registration Q).

Reads data/pm_preds_v2.parquet (written by pm_model2.py --dev) and the
Cricsheet match table, and prints where the loss against the exchange's
day-before price sits: by cell, by gender x fixture class, by competition,
and the worst individual rows. Diagnostics only - nothing here is a claim
and nothing here feeds back into the model automatically.

Usage: python3 src/pm_diag.py [--top 25]
"""
import argparse
import os

import numpy as np
import pandas as pd

from pm_benchmark import clustered_t
import pm_model2
from pm_model2 import DEV_END, is_full

ROOT = os.path.join(os.path.dirname(__file__), "..")


def cell_table(b, key, label):
    print(f"\n{label}:")
    rows = []
    for k, g in b.groupby(key):
        d, t = clustered_t((g.ll_model - g.ll_open).values, g.date)
        units = float((g.ll_model - g.ll_open).sum())
        rows.append((k, len(g), d, t, units,
                     100 * (g.p_model.mean() - g.y.mean()),
                     100 * (g.p_open.mean() - g.y.mean())))
    rows.sort(key=lambda r: -r[4])
    print(f"  {'cell':28s} {'n':>4s} {'gap':>8s} {'t':>5s} {'LLunits':>8s} {'cal_m':>6s} {'cal_o':>6s}")
    for k, n, d, t, u, cm, co in rows:
        print(f"  {str(k):28s} {n:4d} {d:+8.4f} {t:5.1f} {u:+8.2f} {cm:+6.1f} {co:+6.1f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--preds", default="data/pm_preds_v2.parquet")
    ap.add_argument("--women-tiers", action="store_true")
    args = ap.parse_args()
    pm_model2.USE_WOMEN_TIERS = bool(args.women_tiers)
    b = pd.read_parquet(os.path.join(ROOT, args.preds))
    b = b[b.date <= DEV_END].copy()
    m = pd.read_parquet(os.path.join(ROOT, "data", "matches_cs.parquet"))
    m = m.set_index("match_id")
    b["gender"] = m.gender.reindex(b.match_id).to_numpy()
    b["team1"] = m.team1.reindex(b.match_id).to_numpy()
    b["team2"] = m.team2.reindex(b.match_id).to_numpy()
    b["winner"] = m.winner.reindex(b.match_id).to_numpy()
    b["event"] = m.event_name.reindex(b.match_id).fillna("").to_numpy()
    b["venue"] = m.city.reindex(b.match_id).fillna("").to_numpy()
    b["t0"] = np.where(b.outcome0_is_team1, b.team1, b.team2)
    b["t1"] = np.where(b.outcome0_is_team1, b.team2, b.team1)
    f0, f1 = is_full(b.t0, b.gender), is_full(b.t1, b.gender)
    b["fclass"] = np.where(b.seg == "franchise", "fr",
                           np.where(f0 & f1, "FF", np.where(~f0 & ~f1, "AA", "FA")))
    b["gap"] = b.ll_model - b.ll_open
    print(f"dev rows {len(b)}: model-open {b.gap.mean():+.5f}; total LL units {b.gap.sum():+.2f}")
    cell_table(b, "seg", "by cell")
    cell_table(b, ["gender", "fclass"], "by gender x fixture class")
    cell_table(b, "comp", "by competition")
    # the confidence question: where the model and market disagree on the favourite
    b["fav_m"] = b.p_model > 0.5
    b["fav_o"] = b.p_open > 0.5
    agree = b.fav_m == b.fav_o
    print(f"\nfavourite agreement with the open: {agree.mean():.1%}; "
          f"model right when they disagree: "
          f"{((b.fav_m == (b.y == 1))[~agree]).mean():.1%} (n={int((~agree).sum())})")
    for lo, hi in ((0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.01)):
        pm_ = np.where(b.p_model > 0.5, b.p_model, 1 - b.p_model)
        ym = np.where(b.p_model > 0.5, b.y, 1 - b.y)
        po = np.where(b.p_open > 0.5, b.p_open, 1 - b.p_open)
        yo = np.where(b.p_open > 0.5, b.y, 1 - b.y)
        km, ko = (pm_ >= lo) & (pm_ < hi), (po >= lo) & (po < hi)
        print(f"  band [{lo:.1f},{hi:.1f}): model n={int(km.sum()):3d} "
              f"implied {pm_[km].mean() if km.any() else np.nan:.3f} won {ym[km].mean() if km.any() else np.nan:.3f} | "
              f"open n={int(ko.sum()):3d} implied {po[ko].mean() if ko.any() else np.nan:.3f} "
              f"won {yo[ko].mean() if ko.any() else np.nan:.3f}")
    print(f"\nworst {args.top} rows (model vs open, LL units):")
    w = b.sort_values("gap", ascending=False).head(args.top)
    for r in w.itertuples():
        print(f"  {r.date} {r.comp:4s} {r.gender[0]} {r.fclass:2s} {r.t0[:22]:22s} v {r.t1[:22]:22s} "
              f"y={r.y} model={r.p_model:.3f} open={r.p_open:.3f} close={r.p_close:.3f} "
              f"gap={r.gap:+.2f}  {r.event[:30]}")
    print(f"\nbest {args.top // 2} rows:")
    w = b.sort_values("gap").head(args.top // 2)
    for r in w.itertuples():
        print(f"  {r.date} {r.comp:4s} {r.gender[0]} {r.fclass:2s} {r.t0[:22]:22s} v {r.t1[:22]:22s} "
              f"y={r.y} model={r.p_model:.3f} open={r.p_open:.3f} close={r.p_close:.3f} "
              f"gap={r.gap:+.2f}  {r.event[:30]}")


if __name__ == "__main__":
    main()
