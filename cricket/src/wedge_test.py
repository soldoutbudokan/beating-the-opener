"""Wedge test: is the BBL opening line inefficient vs the close?

Mirrors the soccer/wnba phase-2 tests on the aussportsbetting BBL file
(odds compiled from OddsPortal multi-book averages):
  1. paired log-loss, devigged open vs close, on the H/A winner
  2. does the open->close move point at the eventual winner?
  3. toss decomposition: how much of the move is post-toss information?
"""
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

RAW = Path(__file__).resolve().parent.parent / "data" / "raw" / "asb"


def load() -> pd.DataFrame:
    f = sorted(RAW.glob("big_bash_league*.xlsx"))[-1]
    df = pd.read_excel(f, sheet_name="Data", header=1)
    df.columns = [c.replace("\n", " ").strip() for c in df.columns]
    print(f"loaded {f.name}: {len(df)} rows")
    keep = df["Winner"].isin(["H", "A"])
    for c in ["Home Odds Open", "Away Odds Open", "Home Odds Close", "Away Odds Close"]:
        keep &= pd.to_numeric(df[c], errors="coerce").gt(1.0)
    df = df[keep].copy()
    print(f"usable (H/A winner + full open/close odds): {len(df)}")
    return df


def devig(oh, oa):
    ph, pa = 1.0 / oh, 1.0 / oa
    return ph / (ph + pa)


def main():
    df = load()
    y = (df["Winner"] == "H").astype(float).values
    p_open = devig(df["Home Odds Open"].values, df["Away Odds Open"].values)
    p_close = devig(df["Home Odds Close"].values, df["Away Odds Close"].values)

    ll_open = -(y * np.log(p_open) + (1 - y) * np.log(1 - p_open))
    ll_close = -(y * np.log(p_close) + (1 - y) * np.log(1 - p_close))
    d = ll_open - ll_close  # >0 means close better
    t, p = stats.ttest_1samp(d, 0.0)
    w = stats.wilcoxon(d)
    print("\n=== 1. open vs close log-loss ===")
    print(f"LL open  {ll_open.mean():.5f}   LL close {ll_close.mean():.5f}")
    print(f"close better by {d.mean():+.5f}  (t={t:.2f} p={p:.2g}; wilcoxon p={w.pvalue:.2g})")

    move = p_close - p_open
    moved = np.abs(move) > 1e-9
    toward_winner = (np.sign(move) == np.sign(y - 0.5)) & moved
    n, k = moved.sum(), toward_winner.sum()
    bt = stats.binomtest(int(k), int(n), 0.5)
    print("\n=== 2. does the move point at the winner? ===")
    print(f"moved in {n}/{len(df)} matches; toward winner {k}/{n} = {k/n:.1%}  (binom p={bt.pvalue:.2g})")
    print(f"mean |move| {np.abs(move[moved]).mean():.4f} prob")

    print("\n=== 3. toss decomposition ===")
    toss = df["Won Toss"].isin(["H", "A"])
    th = (df["Won Toss"] == "H").astype(float).values
    tw_win = (df["Won Toss"] == df["Winner"]) & toss
    print(f"toss winner wins match: {tw_win.sum()}/{toss.sum()} = {tw_win.sum()/toss.sum():.1%}")
    m2, t2 = move[toss.values & moved], th[toss.values & moved]
    toward_toss = np.sign(m2) == np.sign(t2 - 0.5)
    bt2 = stats.binomtest(int(toward_toss.sum()), int(len(m2)), 0.5)
    print(f"move points at toss winner: {toward_toss.sum()}/{len(m2)} = {toward_toss.mean():.1%} (p={bt2.pvalue:.2g})")
    # wedge restricted to matches where the move went AGAINST/neutral to the toss
    against = toss.values & moved & ~(np.sign(move) == np.sign(th - 0.5))
    if against.sum() > 30:
        d_a = d[against]
        t3, p3 = stats.ttest_1samp(d_a, 0.0)
        print(f"close-vs-open LL edge on non-toss-aligned moves (n={against.sum()}): "
              f"{d_a.mean():+.5f} (t={t3:.2f} p={p3:.2g})")

    print("\n=== extras ===")
    print(f"home win rate {y.mean():.1%};  open implies {p_open.mean():.1%}")
    ov_open = 1 / df["Home Odds Open"].values + 1 / df["Away Odds Open"].values
    ov_close = 1 / df["Home Odds Close"].values + 1 / df["Away Odds Close"].values
    print(f"overround open {ov_open.mean():.4f}  close {ov_close.mean():.4f}")

    level_bias_test()


def level_bias_test():
    """4. home-price LEVEL bias check with an out-of-sample split.

    The 2018+ open/close sample shows home 57.6% vs implied 50.6% (p=.03).
    That hypothesis was formed by peeking, so it is tested on the disjoint
    2011-2017 seasons (plain odds columns only there). Verdict: it flips sign
    OOS (47.5% vs 52.0%, flat-home ROI -12.4%) and pools to nothing -> noise.
    """
    f = sorted(RAW.glob("big_bash_league*.xlsx"))[-1]
    df = pd.read_excel(f, sheet_name="Data", header=1)
    df.columns = [c.replace("\n", " ").strip() for c in df.columns]
    df["Date"] = pd.to_datetime(df["Date"])
    df["season"] = np.where(df.Date.dt.month >= 10, df.Date.dt.year, df.Date.dt.year - 1)
    ok = (pd.to_numeric(df["Home Odds"], errors="coerce").gt(1)
          & pd.to_numeric(df["Away Odds"], errors="coerce").gt(1)
          & df["Winner"].isin(["H", "A"])
          & df["Neutral / Alternative Venue"].isna())
    print("\n=== 4. home-price level bias (true home games, plain odds) ===")
    for name, mask in [("2018+ (hypothesis-forming)", ok & df.season.ge(2018)),
                       ("pre-2018 (out-of-sample)", ok & df.season.lt(2018)),
                       ("pooled", ok)]:
        d = df[mask]
        y = (d.Winner == "H").astype(float).values
        oh, oa = d["Home Odds"].values.astype(float), d["Away Odds"].values.astype(float)
        p = (1 / oh) / (1 / oh + 1 / oa)
        ret = np.where(y == 1, oh - 1, -1.0)
        bt = stats.binomtest(int(y.sum()), len(d), p.mean())
        print(f"{name}: n={len(d)}, home {y.mean():.1%} vs implied {p.mean():.1%} "
              f"(binom p={bt.pvalue:.2g}); flat-home ROI {ret.mean():+.1%} "
              f"(t={ret.mean() / (ret.std(ddof=1) / np.sqrt(len(d))):.2f})")


if __name__ == "__main__":
    main()
