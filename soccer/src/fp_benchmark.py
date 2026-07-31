"""Stage A benchmark for the from-scratch programme (PROGRESS.md Market 3).

Scores the market's own devigged average-book prices (book-agnostic, per
owner direction) against results. Eval convention (registered before any
Stage B model code):

- population: FTR in {H,D,A} and full 3-way early-average (EAvgH/D/A) and
  closing-average (AvgCH/D/A) odds
- forecast: proportionally devigged 3-way probabilities
- metric: multiclass log loss on H/D/A
- split: dev = seasons 2017-18 .. 2021-22, holdout = 2022-23 .. 2025-26

Usage: python3 src/fp_benchmark.py
"""
import os

import numpy as np
import pandas as pd

ROOT = os.path.join(os.path.dirname(__file__), "..")
EPS = 1e-9
DEV = ("2017-18", "2018-19", "2019-20", "2020-21", "2021-22")
HOLDOUT = ("2022-23", "2023-24", "2024-25", "2025-26")


def devig3(oh, od, oa):
    ph, pd_, pa = 1.0 / oh, 1.0 / od, 1.0 / oa
    s = ph + pd_ + pa
    return ph / s, pd_ / s, pa / s


def mll(ph, pd_, pa, ftr):
    p = np.where(ftr == "H", ph, np.where(ftr == "D", pd_, pa))
    return -np.log(np.clip(p, EPS, 1 - EPS))


def load_eval():
    m = pd.read_pickle(os.path.join(ROOT, "data", "matches.pkl"))
    cols = ["EAvgH", "EAvgD", "EAvgA", "AvgCH", "AvgCD", "AvgCA"]
    keep = m.FTR.isin(["H", "D", "A"])
    for c in cols:
        keep &= pd.to_numeric(m[c], errors="coerce").gt(1.0)
    ev = m[keep].copy()
    for c in cols:
        ev[c] = ev[c].astype(float)
    ev["po_h"], ev["po_d"], ev["po_a"] = devig3(ev.EAvgH, ev.EAvgD, ev.EAvgA)
    ev["pc_h"], ev["pc_d"], ev["pc_a"] = devig3(ev.AvgCH, ev.AvgCD, ev.AvgCA)
    ev["ll_open"] = mll(ev.po_h, ev.po_d, ev.po_a, ev.FTR)
    ev["ll_close"] = mll(ev.pc_h, ev.pc_d, ev.pc_a, ev.FTR)
    return ev


def clustered_t(diff, dates):
    g = pd.DataFrame({"d": diff, "date": dates}).groupby("date")["d"]
    means, sizes = g.mean(), g.size()
    w = sizes / sizes.sum()
    mu = float((means * w).sum())
    var = float(((means - mu) ** 2 * w**2).sum())
    return mu, mu / np.sqrt(var) if var > 0 else np.nan


def main():
    ev = load_eval()
    print(f"benchmark population: {len(ev)} matches, "
          f"{ev.Date.min().date()} .. {ev.Date.max().date()}, "
          f"{ev.Div.nunique()} leagues")
    for name, seasons in [("dev", DEV), ("holdout", HOLDOUT)]:
        sub = ev[ev.season.isin(seasons)]
        d, t = clustered_t((sub.ll_open - sub.ll_close).values, sub.Date)
        print(f"{name}: n={len(sub)}  LL(open)={sub.ll_open.mean():.5f}  "
              f"LL(close)={sub.ll_close.mean():.5f}  "
              f"open-close={d:+.5f} (clustered t={t:.1f})")
        for out, pcol in [("H", "po_h"), ("D", "po_d"), ("A", "po_a")]:
            rate = (sub.FTR == out).mean()
            print(f"    {out}: rate={rate:.4f} implied={sub[pcol].mean():.4f}")


if __name__ == "__main__":
    main()
