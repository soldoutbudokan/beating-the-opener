"""Phase 0 QC gates (G0.1) per sport — run after a backfill completes.

Prints, per sport:
  - offer-file coverage vs (closed events x archived markets)   gate >= 95%
  - opening-line presence on prop rows                          gate >= 70%
  - coherent-open share on modelable markets                    gate >= 40%
  - kill screen: opening-line coverage < 40% or coherent < 20%
  - FD facts (not gated, Phase-2 sport selection input): share of openers
    sourced from FD, share of props FD quotes at close

Usage: python3 src/qc_phase0.py --sport MLB
"""
import argparse
import glob
import os

import numpy as np
import pandas as pd

from odds_utils import amer_to_prob
from sports_cfg import SPORTS

ROOT = os.path.join(os.path.dirname(__file__), "..")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", required=True, choices=sorted(SPORTS))
    args = ap.parse_args()
    sport, sl = args.sport, args.sport.lower()
    cfg = SPORTS[sport]
    n_mkts = len(cfg["prop_markets"]) + len(cfg["game_markets"])

    events = pd.read_pickle(os.path.join(ROOT, "data", f"events_{sl}.pkl"))
    closed = events[events.status == "closed"]
    n_files = len(glob.glob(os.path.join(
        ROOT, "data", "raw", "bp", sl, "offers", "*", "*.json.gz")))
    expected = len(closed) * n_mkts
    cov = n_files / expected if expected else 0.0

    props = pd.read_pickle(os.path.join(ROOT, "data", f"props_{sl}.pkl"))
    props = props[props.market.isin(cfg["prop_markets"].values())]
    cons = props[props.book == 0].drop_duplicates(
        ["event_id", "market", "player"]).copy()
    has_open = cons.open_line.notna() & cons.open_over_cost.notna() \
        & cons.open_under_cost.notna()
    bs = np.asarray(amer_to_prob(cons.open_over_cost)
                    + amer_to_prob(cons.open_under_cost))
    coherent = (has_open & (cons.open_book_over == cons.open_book_under)
                & (cons.open_line_over == cons.open_line_under)
                & (bs >= 1.00) & (bs <= 1.15))

    fd_close = props[props.book == 10].drop_duplicates(
        ["event_id", "market", "player"])
    fd_quote_rate = len(fd_close) / max(len(cons), 1)
    fd_open_share = (cons.open_book == 10).mean()

    def gate(name, val, thresh, higher=True):
        ok = val >= thresh if higher else val <= thresh
        print(f"  {'PASS' if ok else 'FAIL'} {name}: {val:.1%} "
              f"(gate {'>=' if higher else '<='} {thresh:.0%})")
        return ok

    print(f"=== G0.1 {sport} ===")
    print(f"  closed events: {len(closed)}, offer files: {n_files} "
          f"(expected {expected})")
    g1 = gate("offer coverage", cov, 0.95)
    g2 = gate("opening-line presence", has_open.mean(), 0.70)
    g3 = gate("coherent-open share", coherent[has_open].mean(), 0.40)
    print(f"  FD-sourced openers: {fd_open_share:.1%}   "
          f"FD quotes at close: {fd_quote_rate:.1%}   (not gated)")
    print("  by market: coherent-open share / FD-open share / n")
    for m, g in cons.groupby("market"):
        ho = g.open_line.notna() & g.open_over_cost.notna() & g.open_under_cost.notna()
        bsm = np.asarray(amer_to_prob(g.open_over_cost)
                         + amer_to_prob(g.open_under_cost))
        co = (ho & (g.open_book_over == g.open_book_under)
              & (g.open_line_over == g.open_line_under)
              & (bsm >= 1.00) & (bsm <= 1.15))
        print(f"    {m:<20} {co[ho].mean() if ho.any() else float('nan'):6.1%} "
              f"{(g.open_book == 10).mean():6.1%} {len(g):>7}")
    if has_open.mean() < 0.40 or coherent[has_open].mean() < 0.20:
        print("  KILL: the open anchor does not exist for this sport")
    elif g1 and g2 and g3:
        print("  G0.1 PASSED")
    else:
        print("  G0.1 FAILED (investigate before Phase 1)")


if __name__ == "__main__":
    main()
