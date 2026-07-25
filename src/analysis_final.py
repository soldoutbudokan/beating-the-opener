"""Final robustness analysis on the saved v4 predictions.

Betting sims at three price sources (Pinnacle early / best-of-book early /
average-book early), per-tier and per-outcome breakdowns, cumulative P&L,
and season sign tests. Writes results/final_summary.txt and chart data.
"""
import os

import numpy as np
import pandas as pd
from scipy import stats

RESULTS = os.path.join(os.path.dirname(__file__), "..", "results")


def sim(d, pb, ym, pcm, price_cols, thr):
    odds = d[price_cols].to_numpy(float)
    ev = pb * odds - 1
    sel = np.nan_to_num(ev, nan=-9) > thr
    i, j = np.where(sel)
    if len(i) == 0:
        return None
    won = (ym[i] == j).astype(float)
    ret = won * odds[i, j] - 1
    clv = pcm[i, j] * odds[i, j] - 1
    out = pd.DataFrame({
        "date": d["Date"].to_numpy()[i], "season": d["season"].to_numpy()[i],
        "tier": d["tier"].to_numpy()[i], "side": np.array(["H", "D", "A"])[j],
        "odds": odds[i, j], "ret": ret, "clv": clv,
    })
    return out


def summarize(bets, label):
    n = len(bets)
    roi = bets["ret"].mean()
    t = roi / (bets["ret"].std() / np.sqrt(n))
    clv = bets["clv"].mean()
    tc = clv / (bets["clv"].std() / np.sqrt(n))
    pos_seasons = (bets.groupby("season")["ret"].mean() > 0)
    return (f"{label}: n={n} ROI={roi:+.3%} (t={t:.2f}) "
            f"CLV={clv:+.3%} (t={tc:.2f}) "
            f"+seasons={int(pos_seasons.sum())}/{len(pos_seasons)}")


def main():
    d = pd.read_pickle(os.path.join(RESULTS, "preds_v4.pkl"))
    ym = d["y"].to_numpy()
    pb = d[["ens_h", "ens_d", "ens_a"]].to_numpy()
    pcm = d[["pc_h", "pc_d", "pc_a"]].to_numpy()

    lines = []
    lines.append(f"{len(d)} OOS matches {d['Date'].min().date()}..{d['Date'].max().date()}\n")

    lines.append("== betting sims (flat 1u, ens model) ==")
    for cols, label in [(["PSH", "PSD", "PSA"], "Pinnacle early"),
                        (["EMaxH", "EMaxD", "EMaxA"], "best-book early"),
                        (["EAvgH", "EAvgD", "EAvgA"], "avg-book early")]:
        for thr in (0.02, 0.05):
            bets = sim(d, pb, ym, pcm, cols, thr)
            if bets is None:
                continue
            lines.append(summarize(bets, f"{label} EV>{thr:.0%}"))
        lines.append("")

    bets = sim(d, pb, ym, pcm, ["EMaxH", "EMaxD", "EMaxA"], 0.02)
    lines.append("== best-book EV>2% by tier ==")
    for tier, g in bets.groupby("tier"):
        lines.append(summarize(g, f"  {tier}"))
    lines.append("\n== best-book EV>2% by side ==")
    for side, g in bets.groupby("side"):
        lines.append(summarize(g, f"  {side}"))
    lines.append("\n== best-book EV>2% by odds bucket ==")
    bets["bucket"] = pd.cut(bets["odds"], [1, 2, 3, 5, 10, 100],
                            labels=["<2", "2-3", "3-5", "5-10", ">10"])
    for b, g in bets.groupby("bucket", observed=True):
        lines.append(summarize(g, f"  odds {b}"))

    # season sign test on log-loss improvement (primary endpoint robustness)
    from odds_utils import log_loss_vec
    ll_open = log_loss_vec(d[["po_h", "po_d", "po_a"]].to_numpy(), ym)
    ll_ens = log_loss_vec(pb, ym)
    per_season = pd.Series(ll_open - ll_ens).groupby(d["season"].to_numpy()).mean()
    npos = int((per_season > 0).sum())
    p_sign = stats.binomtest(npos, len(per_season), 0.5).pvalue
    lines.append(f"\n== primary endpoint ==")
    lines.append(f"ens beats open in {npos}/{len(per_season)} seasons "
                 f"(sign test p={p_sign:.4f})")
    t, p = stats.ttest_1samp(ll_open - ll_ens, 0)
    lines.append(f"per-match paired t={t:.2f}, p={p:.2e}")

    # cumulative P&L series for chart
    bets_sorted = bets.sort_values("date")
    cum = bets_sorted.groupby("date")["ret"].sum().cumsum()
    cum.to_frame("cum_units").to_csv(os.path.join(RESULTS, "cum_pnl.csv"))
    lines.append(f"\nmax drawdown (units): "
                 f"{(cum - cum.cummax()).min():.1f} on {len(bets)} bets; "
                 f"final: {cum.iloc[-1]:+.1f}u")

    txt = "\n".join(lines)
    print(txt)
    with open(os.path.join(RESULTS, "final_summary.txt"), "w") as f:
        f.write(txt + "\n")


if __name__ == "__main__":
    main()
