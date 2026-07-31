"""Stage A benchmark for the from-scratch (fp) programme — PROGRESS.md.

Scores the market's own devigged consensus prices against actuals, the
yardstick the fp model must beat. No model here; market prices are used ONLY
as forecasts to score. Eval convention (registered before Stage B):

- population: matched, not void, coherent two-way opening quote, and the
  actual does not push on the opening line
- outcome: over = actual > open_line
- forecast: devigged consensus P(over) at the opening line (p_open); the
  close is scored on the same-line subset (line_close == open_line)
- split: season 2025 = dev, season 2026 = held-out (scored once by fp_model)

Usage: python3 src/fp_benchmark.py
"""
import numpy as np
import pandas as pd
import os

ROOT = os.path.join(os.path.dirname(__file__), "..")
EPS = 1e-9


def ll(p, y):
    p = np.clip(p, EPS, 1 - EPS)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def clustered_t(diff, dates):
    """t-stat of mean(diff) with date-clustered s.e."""
    g = pd.DataFrame({"d": diff, "date": dates}).groupby("date")["d"]
    means, sizes = g.mean(), g.size()
    w = sizes / sizes.sum()
    mu = float((means * w).sum())
    var = float(((means - mu) ** 2 * w**2).sum())
    return mu, mu / np.sqrt(var) if var > 0 else np.nan


def main():
    ms = pd.read_pickle(os.path.join(ROOT, "data", "modelset.pkl"))
    ev = ms[ms.matched & ~ms.void & ms.open_coherent
            & (ms.actual != ms.open_line)].copy()
    ev["over"] = (ev.actual > ev.open_line).astype(int)
    ev["ll_open"] = ll(ev.p_open, ev.over)

    print(f"eval population: {len(ev)} props, "
          f"{ev.date.min()} .. {ev.date.max()}")
    for season, sub in ev.groupby("season"):
        line = (f"season {season}: n={len(sub)}  "
                f"LL(open)={sub.ll_open.mean():.5f}  "
                f"over rate={sub.over.mean():.4f}  "
                f"implied P(over)={sub.p_open.mean():.4f}")
        # close on same-line coherent subset
        sl = sub[sub.coh_close & (sub.line_close == sub.open_line)].copy()
        if len(sl):
            sl["ll_close"] = ll(sl.p_close, sl.over)
            d, t = clustered_t((sl.ll_open - sl.ll_close).values, sl.date)
            line += (f"\n  same-line close: n={len(sl)}  "
                     f"LL(close)={sl.ll_close.mean():.5f}  "
                     f"open-close={d:+.5f} (clustered t={t:.1f})")
        print(line)

    # per-market dev-season detail (for gate context, not selection)
    dev = ev[ev.season == 2025]
    print("\ndev-season per market:")
    for m, sub in dev.groupby("market"):
        print(f"  {m:10s} n={len(sub):5d}  LL(open)={sub.ll_open.mean():.5f} "
              f" over={sub.over.mean():.3f} implied={sub.p_open.mean():.3f}")


if __name__ == "__main__":
    main()
