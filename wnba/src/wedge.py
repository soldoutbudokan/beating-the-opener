"""Is the closing prop price better than the opening one? (the wedge)

If close beats open, the opener is inefficient and a model only has to capture
part of the move — the same logic that worked for soccer 1X2.

Tests, per close-source (consensus book 0 / FanDuel book 10):
  1. how often the LINE itself moves open->close, by market
  2. same-line subset: paired log-loss, devigged P(over) at open vs close
  3. moved-line subset: does the direction of the line move point at the actual?
"""
import os

import numpy as np
import pandas as pd
from scipy import stats

from odds_utils import amer_to_prob, devig_power, ll_binary

ROOT = os.path.join(os.path.dirname(__file__), "..")


def prop_level(graded, book):
    """One row per prop with open + this book's close. Excludes voids/offs."""
    g = graded[(graded.book == book) & (~graded.void.fillna(True))
               & (~graded.is_off) & graded.actual.notna()
               & graded.line.notna() & graded.over_cost.notna()
               & graded.under_cost.notna() & graded.open_line.notna()
               & graded.open_over_cost.notna() & graded.open_under_cost.notna()]
    g = g.drop_duplicates(["event_id", "market", "player"]).copy()
    g["p_open"] = devig_power(amer_to_prob(g.open_over_cost),
                              amer_to_prob(g.open_under_cost))
    g["p_close"] = devig_power(amer_to_prob(g.over_cost),
                               amer_to_prob(g.under_cost))
    return g


def report(g, label):
    print(f"\n=== close source: {label} (n={len(g)}) ===")
    moved = g.line != g.open_line
    print(f"line moved open->close: {moved.mean():.1%} "
          f"(mean |move| when moved: {(g.line - g.open_line)[moved].abs().mean():.2f})")
    by_mkt = g.groupby("market").apply(
        lambda x: (x.line != x.open_line).mean(), include_groups=False)
    print("move rate by market:", {k: f"{v:.0%}" for k, v in by_mkt.items()})

    # -- same-line: pure price-move information test
    s = g[~moved].copy()
    s = s[s.actual != s.line]  # drop pushes
    y = (s.actual > s.line).astype(float)
    ll_o = ll_binary(s.p_open, y)
    ll_c = ll_binary(s.p_close, y)
    d = ll_o - ll_c  # >0 means close better
    t, p = stats.ttest_1samp(d, 0)
    print(f"same-line subset n={len(s)} (pushes dropped): "
          f"LL open {ll_o.mean():.5f} close {ll_c.mean():.5f} "
          f"diff {d.mean():+.5f} (t={t:.2f}, p={p:.2g})")
    # date-clustered
    s["d"] = d
    byd = s.groupby("date")["d"].mean()
    if len(byd) > 3:
        t2, p2 = stats.ttest_1samp(byd, 0)
        print(f"  date-clustered: t={t2:.2f}, p={p2:.2g} ({len(byd)} dates)")

    # -- moved lines: is the move informative about the actual?
    m = g[moved].copy()
    m = m[np.sign(m.actual - m.open_line) != 0]
    if len(m) > 20:
        correct = np.sign(m.line - m.open_line) == np.sign(m.actual - m.open_line)
        bt = stats.binomtest(int(correct.sum()), len(m), 0.5)
        print(f"moved-line subset n={len(m)}: move points at actual "
              f"{correct.mean():.1%} (binomial p={bt.pvalue:.2g})")


def main():
    graded = pd.read_pickle(os.path.join(ROOT, "data", "graded.pkl"))
    for book, label in [(0, "consensus"), (10, "FanDuel"), (12, "DraftKings")]:
        g = prop_level(graded, book)
        if len(g) > 100:
            report(g, label)
        else:
            print(f"\n{label}: only {len(g)} usable props, skipping")


if __name__ == "__main__":
    main()
