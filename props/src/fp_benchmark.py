"""Stage A benchmark for the from-scratch programme (PROGRESS.md Market 5:
NBA player props).

Scores the market's own devigged consensus prices against actuals. Eval
convention (registered before Stage B):

- population: matched, non-void, coherent consensus open, no push on the
  opening line (modelset_<sport>.pkl already restricts to coherent opens)
- outcome: over = actual > open_line; forecast = devigged p_open; the close
  is scored on the same-line coherent subset
- split (props/PLAN.md dev-ends-early convention): dev = dates through
  2026-02-28, holdout = 2026-03-01 onward (scored once by fp_model)

MLB (PROGRESS.md Market 6, pitcher props): the archive spans two seasons,
so dev = the 2025 season (dates <= 2025-12-31) and holdout = 2026 (scored
once by fp_model_mlb). Population restricted to the registered pitcher
family via --markets; extra columns report the FanDuel venue (FD close
coverage, FD-sourced opener share) and the no-move share (AUDIT N2).
Usage: python3 src/fp_benchmark.py [--sport NBA]
       python3 src/fp_benchmark.py --sport MLB --markets strikeouts outs_recorded
"""
import argparse
import os

import numpy as np
import pandas as pd

ROOT = os.path.join(os.path.dirname(__file__), "..")
EPS = 1e-9
DEV_END = "2026-02-28"
DEV_END_BY_SPORT = {"nba": "2026-02-28", "nhl": "2026-02-28",
                    "mlb": "2025-12-31"}


def ll(p, y):
    p = np.clip(p, EPS, 1 - EPS)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def clustered_t(diff, dates):
    g = pd.DataFrame({"d": diff, "date": dates}).groupby("date")["d"]
    means, sizes = g.mean(), g.size()
    w = sizes / sizes.sum()
    mu = float((means * w).sum())
    var = float(((means - mu) ** 2 * w**2).sum())
    return mu, mu / np.sqrt(var) if var > 0 else np.nan


def load_eval(sport="nba", markets=None):
    ms = pd.read_pickle(os.path.join(ROOT, "data", f"modelset_{sport}.pkl"))
    ev = ms[ms.matched & ~ms.void & ms.open_coherent
            & (ms.actual != ms.open_line)].copy()
    if markets:
        ev = ev[ev.market.isin(markets)].copy()
    ev["over"] = (ev.actual > ev.open_line).astype(int)
    dev_end = DEV_END_BY_SPORT.get(sport, DEV_END)
    ev["split"] = np.where(ev.date <= dev_end, "dev", "holdout")
    return ev


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", default="NBA")
    ap.add_argument("--markets", nargs="*", default=None,
                    help="restrict the eval population (MLB pitcher family)")
    args = ap.parse_args()
    ev = load_eval(args.sport.lower(), args.markets)
    ev["ll_open"] = ll(ev.p_open, ev.over)
    print(f"{args.sport} eval population: {len(ev)} props, "
          f"{ev.date.min()} .. {ev.date.max()}")
    for name in ("dev", "holdout"):
        sub = ev[ev.split == name]
        line = (f"{name}: n={len(sub)}  LL(open)={sub.ll_open.mean():.5f}  "
                f"over rate={sub.over.mean():.4f}  "
                f"implied={sub.p_open.mean():.4f}")
        sl = sub[sub.coh_close & (sub.line_close == sub.open_line)].copy()
        if len(sl):
            sl["ll_close"] = ll(sl.p_close, sl.over)
            d, t = clustered_t((sl.ll_open - sl.ll_close).values, sl.date)
            line += (f"\n  same-line close: n={len(sl)}  "
                     f"LL(close)={sl.ll_close.mean():.5f}  "
                     f"open-close={d:+.5f} (clustered t={t:.1f})")
        if "coh_fd" in sub.columns:
            nomove = sub.coh_close & (sub.line_close == sub.open_line) \
                & (sub.oc_close == sub.open_over_cost)
            line += (f"\n  venue: FD coherent close on {sub.coh_fd.mean():.1%}"
                     f"  FD-sourced opener {(sub.open_book == 10).mean():.1%}"
                     f"  no-move share {nomove.mean():.1%}")
        print(line)
    for name in ("dev", "holdout"):
        print(f"\n{name} per market:")
        part = ev[ev.split == name]
        for m, sub in part.groupby("market"):
            extra = ""
            sl = sub[sub.coh_close & (sub.line_close == sub.open_line)]
            if len(sl):
                d, t = clustered_t((sl.ll_open - ll(sl.p_close, sl.over)
                                    ).values, sl.date)
                extra = f"  open-close={d:+.5f} (t={t:.1f}, n={len(sl)})"
            if "coh_fd" in sub.columns:
                extra += (f"  FD close {sub.coh_fd.mean():.0%}"
                          f"  FD open {(sub.open_book == 10).mean():.0%}")
            print(f"  {m:14s} n={len(sub):5d}  LL(open)={sub.ll_open.mean():.5f}"
                  f"  over={sub.over.mean():.3f} implied={sub.p_open.mean():.3f}"
                  f"{extra}")


if __name__ == "__main__":
    main()
