"""Is the closing prop price better than the opening one? (the wedge screen)

Port of wnba/src/wedge.py, per sport, with the Phase-1 additions from
PLAN.md: per-market gate table, C1 coherence filters at BOTH ends, an
FD-sourced-opener preview cell, and the N2 stale-FanDuel diagnostic.

Pre-registered gates (PLAN.md, frozen before this file first ran):
  Gate A (informative close): same-line LL(open)-LL(close) >= +0.0008,
          date-clustered p < 0.01, n >= 2,000
  Gate B (lazy open): moved lines point at the actual >= 54%,
          binomial p < 0.001, n >= 400

Usage: python3 src/wedge.py --sport MLB   (writes results/wedge_<sport>.csv)
"""
import argparse
import os

import numpy as np
import pandas as pd
from scipy import stats

from odds_utils import amer_to_dec, amer_to_prob, devig_power, ll_binary
from sports_cfg import SPORTS

ROOT = os.path.join(os.path.dirname(__file__), "..")
BOOKSUM_LO, BOOKSUM_HI = 1.00, 1.15


def prop_level(graded, book, coherent_open=True):
    """One row per prop with a coherent open + this book's coherent close."""
    g = graded[(graded.book == book) & (~graded.void.fillna(True))
               & (~graded.is_off) & graded.actual.notna()
               & graded.line.notna() & graded.over_cost.notna()
               & graded.under_cost.notna() & graded.open_line.notna()
               & graded.open_over_cost.notna() & graded.open_under_cost.notna()]
    # close coherence: same line both sides + sane booksum (C1)
    same_close_line = g.line_under.isna() | (g.line_under == g.line)
    bs_close = np.asarray(amer_to_prob(g.over_cost) + amer_to_prob(g.under_cost))
    g = g[same_close_line & (bs_close >= BOOKSUM_LO) & (bs_close <= BOOKSUM_HI)]
    if coherent_open:
        # open coherence: same book, same line, sane booksum (C1/H2 -
        # independently-archived over/under openers fabricate EV otherwise)
        bs_open = np.asarray(amer_to_prob(g.open_over_cost)
                             + amer_to_prob(g.open_under_cost))
        g = g[(g.open_book_over == g.open_book_under)
              & (g.open_line_over == g.open_line_under)
              & (bs_open >= BOOKSUM_LO) & (bs_open <= BOOKSUM_HI)]
    g = g.drop_duplicates(["event_id", "market", "player"]).copy()
    g["p_open"] = devig_power(amer_to_prob(g.open_over_cost),
                              amer_to_prob(g.open_under_cost))
    g["p_close"] = devig_power(amer_to_prob(g.over_cost),
                               amer_to_prob(g.under_cost))
    return g


def market_block(g, sport, source, market, out_rows):
    moved = g.line != g.open_line
    s = g[~moved]
    s = s[s.actual != s.line]  # drop pushes
    row = {"sport": sport, "close_source": source, "market": market,
           "n": len(g), "move_rate": moved.mean(),
           "mean_abs_move": (g.line - g.open_line)[moved].abs().mean(),
           "n_same": len(s), "ll_diff": np.nan, "t_iid": np.nan,
           "t_date": np.nan, "p_date": np.nan,
           "n_moved": np.nan, "directional": np.nan, "p_binom": np.nan,
           "gate_a": False, "gate_b": False}
    if len(s) > 30:
        y = (s.actual > s.line).astype(float)
        d = ll_binary(s.p_open, y) - ll_binary(s.p_close, y)
        row["ll_diff"] = d.mean()
        row["t_iid"] = stats.ttest_1samp(d, 0)[0]
        byd = s.assign(d=d).groupby("date")["d"].mean()
        if len(byd) > 3:
            t2, p2 = stats.ttest_1samp(byd, 0)
            row["t_date"], row["p_date"] = t2, p2
            row["gate_a"] = (d.mean() >= 0.0008 and p2 < 0.01
                             and t2 > 0 and len(s) >= 2000)
    m = g[moved]
    m = m[np.sign(m.actual - m.open_line) != 0]
    if len(m) > 20:
        correct = np.sign(m.line - m.open_line) == np.sign(m.actual - m.open_line)
        bt = stats.binomtest(int(correct.sum()), len(m), 0.5)
        row["n_moved"] = len(m)
        row["directional"] = correct.mean()
        row["p_binom"] = bt.pvalue
        row["gate_b"] = (correct.mean() >= 0.54 and bt.pvalue < 0.001
                         and len(m) >= 400)
    out_rows.append(row)
    return row


def fmt(row):
    def f(v, spec, na="     -"):
        return na if pd.isna(v) else format(v, spec)
    flag = ("AB" if row["gate_a"] and row["gate_b"]
            else "A " if row["gate_a"] else " B" if row["gate_b"] else "  ")
    return (f"  {row['market']:<20} n={row['n']:>6} move={f(row['move_rate'],'5.1%')} "
            f"| same n={row['n_same']:>6} dLL={f(row['ll_diff'],'+.5f')} "
            f"t_dt={f(row['t_date'],'5.1f')} "
            f"| moved n={f(row['n_moved'],'6.0f')} dir={f(row['directional'],'5.1%')} "
            f"p={f(row['p_binom'],'8.2g')} [{flag}]")


def n2_diagnostic(graded, sport):
    """Stale-FD population: consensus close moved, FD close == FD open.

    Reports the fraction and the mechanical CLV of taking FD's stale price
    on the consensus-favored side (AUDIT N2: a price that never moves pays
    -vig deterministically; this measures whether an exploitable lag exists
    at all before anyone designs a live gate around it).
    """
    fd = prop_level(graded, 10)
    fd = fd[fd.open_book == 10]
    cons = prop_level(graded, 0)[
        ["event_id", "market", "player", "line", "p_close", "open_line"]]
    cons.columns = ["event_id", "market", "player", "c_line", "c_p_close",
                    "c_open_line"]
    j = fd.merge(cons, on=["event_id", "market", "player"], how="inner")
    if not len(j):
        print(f"\nN2 diagnostic: no FD-sourced coherent openers with a "
              f"consensus close ({sport})")
        return
    fd_stale = ((j.line == j.open_line)
                & (np.abs(j.over_cost - j.open_over_cost) <= 15)
                & (np.abs(j.under_cost - j.open_under_cost) <= 15))
    cons_moved = j.c_line != j.c_open_line
    lag = j[fd_stale & cons_moved].copy()
    print(f"\nN2 diagnostic (FD-sourced coherent openers, n={len(j)}):")
    print(f"  FD close still == FD open: {fd_stale.mean():.1%}")
    print(f"  consensus line moved:      {cons_moved.mean():.1%}")
    print(f"  exploitable-lag cell (both): {len(lag)} ({(fd_stale & cons_moved).mean():.1%})")
    if len(lag):
        # consensus close re-expressed at FD's line is approximated by the
        # same line when lines are equal; where they differ this is a lower
        # bound diagnostic, not a tradeable EV claim
        p_over = lag.c_p_close
        ev_over = p_over * amer_to_dec(lag.over_cost) - 1
        ev_under = (1 - p_over) * amer_to_dec(lag.under_cost) - 1
        best = np.maximum(ev_over, ev_under)
        same_line = (lag.c_line == lag.line)
        print(f"  same-line subset n={same_line.sum()}: mechanical CLV of "
              f"best side at FD's stale price: "
              f"{best[same_line].mean():+.2%} (median {best[same_line].median():+.2%})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", required=True, choices=sorted(SPORTS))
    args = ap.parse_args()
    sport = args.sport
    sl = sport.lower()
    graded = pd.read_pickle(os.path.join(ROOT, "data", f"graded_{sl}.pkl"))
    prop_markets = set(SPORTS[sport]["prop_markets"].values())
    graded = graded[graded.market.isin(prop_markets)]

    out_rows = []
    for book, label in [(0, "consensus"), (10, "FanDuel"), (12, "DraftKings")]:
        g = prop_level(graded, book)
        if len(g) < 100:
            print(f"\n=== close source: {label}: only {len(g)} usable props ===")
            continue
        print(f"\n=== {sport} close source: {label} (n={len(g)}, "
              f"{g.date.nunique()} dates) ===")
        for market, gm in g.groupby("market"):
            fmt_row = market_block(gm, sport, label, market, out_rows)
            print(fmt(fmt_row))
        # all-market pooled row
        pooled = market_block(g, sport, label, "_ALL_", out_rows)
        print(fmt(pooled))

        # FD-sourced-opener preview cell (H3: the only tradeable population)
        fd_open = g[g.open_book == 10]
        if len(fd_open) > 100:
            prev = market_block(fd_open, sport, label + "|fd_open", "_ALL_",
                                out_rows)
            print("  -- FD-sourced openers only --")
            print(fmt(prev))

    n2_diagnostic(graded, sport)

    out = pd.DataFrame(out_rows)
    os.makedirs(os.path.join(ROOT, "results"), exist_ok=True)
    path = os.path.join(ROOT, "results", f"wedge_{sl}.csv")
    out.to_csv(path, index=False)
    print(f"\nwrote {path}")
    passing = out[(out.close_source == "consensus") & (out.market != "_ALL_")
                  & out.gate_a & out.gate_b]
    print(f"PASSING CELLS (consensus, both gates): "
          f"{sorted(passing.market)} " if len(passing) else
          "PASSING CELLS (consensus, both gates): none")


if __name__ == "__main__":
    main()
