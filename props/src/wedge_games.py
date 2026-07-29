"""Phase 1G: is the closing GAME price better than the opening one?

Game-market analogue of wedge.py for NHL (moneyline/puck_line/total) and
WNBA (moneyline/spread/total). Gates pre-registered in PLAN.md 2026-07-29
before this file existed:
  Gate A (informative close): same-line devigged logloss(open)-logloss(close)
          >= +0.0008, date-clustered p < 0.01, n_same >= 500. ML rows are
          same-line by construction.
  Gate B (lazy open): moved lines point at the result >= 54%, binomial
          p < 0.001, n_moved >= 150. ML: "moved" = this book's devigged home
          prob changed >= 1pp; correct = the move points at the winner.

One row per event x market x book, coherent two-way quote at BOTH ends (C1),
canonical side = home (ML/spread/PL) or over (totals). Outcomes only from
dual-source-validated scores (qc_game_scores.py).

Usage: python3 src/wedge_games.py --sport NHL|WNBA
       (writes results/wedge_games_<sport>.csv)
"""
import argparse
import os

import numpy as np
import pandas as pd
from scipy import stats

from odds_utils import amer_to_dec, amer_to_prob, devig_power, ll_binary

ROOT = os.path.join(os.path.dirname(__file__), "..")
BOOKSUM_LO, BOOKSUM_HI = 1.00, 1.15
MARKETS = {"NHL": ["moneyline", "puck_line", "total"],
           "WNBA": ["moneyline", "spread", "total"]}


def load(sport):
    sl = sport.lower()
    g = pd.read_pickle(os.path.join(ROOT, "data", f"games_{sl}.pkl"))
    sc = pd.read_pickle(os.path.join(ROOT, "data", f"game_scores_{sl}.pkl"))
    g = g.merge(sc, on="event_id", how="inner")
    g["margin"] = g.home_score - g.away_score
    g["total_actual"] = g.home_score + g.away_score
    return g


SIDE_COLS = ["line", "cost", "is_off", "open_line", "open_cost", "open_book"]


def pair(g, market, book):
    """Coherent two-way rows for one market at one book (canonical side a =
    home for ML/spread/PL, over for totals; side b is the mirror)."""
    m = g[(g.market == market) & (g.book == book)]
    if market == "total":
        a = m[m.selection.str.lower() == "over"]
        b = m[m.selection.str.lower() == "under"]
    else:
        a = m[m.participant == m.home]
        b = m[m.participant == m.visitor]
    keys = ["event_id"]
    a = a.drop_duplicates(keys)
    b = b.drop_duplicates(keys)[keys + SIDE_COLS]
    j = a.merge(b, on=keys, suffixes=("", "_b"))
    j = j[j.cost.notna() & j.cost_b.notna() & ~j.is_off & ~j.is_off_b
          & j.open_cost.notna() & j.open_cost_b.notna()
          # open coherence: the same book supplied both side openers (C1/H2)
          & (j.open_book == j.open_book_b)]
    if market == "total":
        j = j[(j.line == j.line_b) & (j.open_line == j.open_line_b)]
    elif market != "moneyline":
        j = j[(j.line == -j.line_b) & (j.open_line == -j.open_line_b)]
    bs_close = np.asarray(amer_to_prob(j.cost) + amer_to_prob(j.cost_b))
    bs_open = np.asarray(amer_to_prob(j.open_cost) + amer_to_prob(j.open_cost_b))
    j = j[(bs_close >= BOOKSUM_LO) & (bs_close <= BOOKSUM_HI)
          & (bs_open >= BOOKSUM_LO) & (bs_open <= BOOKSUM_HI)].copy()
    if not len(j):
        return j
    j["p_open"] = devig_power(amer_to_prob(j.open_cost),
                              amer_to_prob(j.open_cost_b))
    j["p_close"] = devig_power(amer_to_prob(j.cost), amer_to_prob(j.cost_b))

    # expected-result space: em = the market's estimate of `result`;
    # p_* is always P(result > em) for the canonical side
    if market == "moneyline":
        j["result"], j["em_open"], j["em_close"] = j.margin, 0.0, 0.0
        j["same"] = True
        j["moved"] = (j.p_close - j.p_open).abs() >= 0.01
        j["toward_a"] = j.p_close > j.p_open
    elif market == "total":
        j["result"], j["em_open"], j["em_close"] = j.total_actual, j.open_line, j.line
        j["same"] = j.em_close == j.em_open
        j["moved"] = ~j.same
        j["toward_a"] = j.em_close > j.em_open  # rising total = move toward over
    else:  # spread / puck_line: line is the home handicap, em = -line
        j["result"] = j.margin
        j["em_open"], j["em_close"] = -j.open_line, -j.line
        j["same"] = j.em_close == j.em_open
        j["moved"] = ~j.same
        j["toward_a"] = j.em_close > j.em_open
    return j


def gate_block(j, sport, source, market, out_rows):
    s = j[j.same & (j.result != j.em_open)]
    row = {"sport": sport, "close_source": source, "market": market,
           "n": len(j), "move_rate": j.moved.mean() if len(j) else np.nan,
           "n_same": len(s), "ll_diff": np.nan, "t_iid": np.nan,
           "t_date": np.nan, "p_date": np.nan, "n_moved": np.nan,
           "directional": np.nan, "p_binom": np.nan,
           "gate_a": False, "gate_b": False}
    if len(s) > 30:
        y = (s.result > s.em_open).astype(float)
        d = ll_binary(s.p_open, y) - ll_binary(s.p_close, y)
        row["ll_diff"] = d.mean()
        row["t_iid"] = stats.ttest_1samp(d, 0)[0]
        byd = s.assign(d=d).groupby("date")["d"].mean()
        if len(byd) > 3:
            t2, p2 = stats.ttest_1samp(byd, 0)
            row["t_date"], row["p_date"] = t2, p2
            row["gate_a"] = (d.mean() >= 0.0008 and p2 < 0.01
                             and t2 > 0 and len(s) >= 500)
    m = j[j.moved & (j.result != j.em_open)]
    if len(m) > 20:
        correct = m.toward_a == (m.result > m.em_open)
        bt = stats.binomtest(int(correct.sum()), len(m), 0.5)
        row["n_moved"] = len(m)
        row["directional"] = correct.mean()
        row["p_binom"] = bt.pvalue
        row["gate_b"] = (correct.mean() >= 0.54 and bt.pvalue < 0.001
                         and len(m) >= 150)
    out_rows.append(row)
    return row


def fmt(row):
    def f(v, spec, na="     -"):
        return na if pd.isna(v) else format(v, spec)
    flag = ("AB" if row["gate_a"] and row["gate_b"]
            else "A " if row["gate_a"] else " B" if row["gate_b"] else "  ")
    return (f"  {row['market']:<10} n={row['n']:>5} move={f(row['move_rate'],'5.1%')} "
            f"| same n={row['n_same']:>5} dLL={f(row['ll_diff'],'+.5f')} "
            f"t_dt={f(row['t_date'],'5.1f')} "
            f"| moved n={f(row['n_moved'],'5.0f')} dir={f(row['directional'],'5.1%')} "
            f"p={f(row['p_binom'],'8.2g')} [{flag}]")


def n2_diagnostic(g, sport):
    """Consensus moved while FD's close ~= FD's open (AUDIT N2)."""
    frames = []
    for market in MARKETS[sport]:
        fd = pair(g, market, 10)
        fd = fd[fd.open_book == 10]
        if not len(fd):
            continue
        cons = pair(g, market, 0)[
            ["event_id", "p_close", "em_open", "em_close", "moved"]]
        cons.columns = ["event_id", "c_p_close", "c_em_open", "c_em_close",
                        "c_moved"]
        j = fd.merge(cons, on="event_id", how="inner")
        j["market"] = market
        frames.append(j)
    if not frames:
        print(f"\nN2 diagnostic: no FD-sourced coherent openers ({sport})")
        return
    j = pd.concat(frames, ignore_index=True)
    fd_stale = ((j.em_close == j.em_open)
                & (np.abs(j.cost - j.open_cost) <= 15)
                & (np.abs(j.cost_b - j.open_cost_b) <= 15))
    lag = j[fd_stale & j.c_moved]
    print(f"\nN2 diagnostic (FD-sourced coherent openers, n={len(j)}):")
    print(f"  FD close still ~= FD open: {fd_stale.mean():.1%}")
    print(f"  consensus moved:           {j.c_moved.mean():.1%}")
    print(f"  exploitable-lag cell (both): {len(lag)} "
          f"({(fd_stale & j.c_moved).mean():.1%})")
    if len(lag):
        same_em = lag[lag.c_em_close == lag.em_close]
        if len(same_em):
            ev_a = same_em.c_p_close * amer_to_dec(same_em.cost) - 1
            ev_b = (1 - same_em.c_p_close) * amer_to_dec(same_em.cost_b) - 1
            best = np.maximum(ev_a, ev_b)
            print(f"  same-line subset n={len(same_em)}: mechanical CLV of "
                  f"best side at FD's stale price: "
                  f"{best.mean():+.2%} (median {best.median():+.2%})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", required=True, choices=sorted(MARKETS))
    args = ap.parse_args()
    sport = args.sport
    g = load(sport)
    print(f"{sport}: {g.event_id.nunique()} score-validated events")

    out_rows = []
    for book, label in [(0, "consensus"), (10, "FanDuel"), (12, "DraftKings")]:
        cells = {m: pair(g, m, book) for m in MARKETS[sport]}
        n_tot = sum(len(c) for c in cells.values())
        if n_tot < 100:
            print(f"\n=== close source: {label}: only {n_tot} coherent games ===")
            continue
        dates = pd.concat([c.date for c in cells.values() if len(c)]).nunique()
        print(f"\n=== {sport} games, close source: {label} "
              f"(n={n_tot}, {dates} dates) ===")
        for market, cj in cells.items():
            if len(cj):
                print(fmt(gate_block(cj, sport, label, market, out_rows)))
        pooled = pd.concat([c for c in cells.values() if len(c)],
                           ignore_index=True)
        print(fmt(gate_block(pooled, sport, label, "_ALL_", out_rows)))

        fd_open = pooled[pooled.open_book == 10]
        if len(fd_open) > 100:
            print("  -- FD-sourced openers only --")
            print(fmt(gate_block(fd_open, sport, label + "|fd_open", "_ALL_",
                                 out_rows)))

    n2_diagnostic(g, sport)

    out = pd.DataFrame(out_rows)
    os.makedirs(os.path.join(ROOT, "results"), exist_ok=True)
    path = os.path.join(ROOT, "results", f"wedge_games_{sport.lower()}.csv")
    out.to_csv(path, index=False)
    print(f"\nwrote {path}")
    passing = out[(out.close_source == "consensus") & (out.market != "_ALL_")
                  & out.gate_a & out.gate_b]
    print("PASSING CELLS (consensus, both gates): "
          + (str(sorted(passing.market)) if len(passing) else "none"))


if __name__ == "__main__":
    main()
