"""results/results.png: wedge capture by season + cumulative bet-sim P&L."""
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from odds_utils import amer_to_dec, ll_binary
from dist_utils import p_over

ROOT = os.path.join(os.path.dirname(__file__), "..")
ORANGE, BLUE, SURFACE, INK = "#eb6834", "#2a78d6", "#fcfcfb", "#333333"


def main():
    ev = pd.read_pickle(os.path.join(ROOT, "results", "preds_v2.pkl"))
    for m in np.unique(ev.market):
        mm = ev.market == m
        ev.loc[mm, "pm"] = p_over(m, ev.loc[mm, "mu_model"], ev.loc[mm, "line_close"])
        ev.loc[mm, "po"] = p_over(m, ev.loc[mm, "mu_open"], ev.loc[mm, "line_close"])
        ev.loc[mm, "pm_ol"] = p_over(m, ev.loc[mm, "mu_model"], ev.loc[mm, "open_line"])

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.2), facecolor=SURFACE)

    # panel A: wedge vs capture by season
    e = ev[ev.actual != ev.line_close].copy()
    yb = (e.actual > e.line_close).astype(float)
    e["wedge"] = ll_binary(e.po, yb) - ll_binary(e.p_close, yb)
    e["capture"] = ll_binary(e.po, yb) - ll_binary(e.pm, yb)
    agg = e.groupby("season")[["wedge", "capture"]].mean()
    x = np.arange(len(agg))
    ax1.set_facecolor(SURFACE)
    b1 = ax1.bar(x - 0.19, agg.wedge * 1000, 0.36, color=BLUE,
                 label="open→close wedge")
    b2 = ax1.bar(x + 0.19, agg.capture * 1000, 0.36, color=ORANGE,
                 label="model capture")
    for bars in (b1, b2):
        ax1.bar_label(bars, fmt="%.1f", fontsize=9, color=INK, padding=2)
    ax1.set_xticks(x, agg.index.astype(str))
    ax1.set_ylabel("log-loss edge over the opener (×1000)")
    ax1.set_title("The opener is beatable: model captures\n~half the open→close wedge",
                  fontsize=11)
    ax1.legend(frameon=False, fontsize=9)

    # panel B: cumulative P&L, EV>2% flat 1u at open prices
    rows = []
    for r in ev[ev.pm_ol.notna()].itertuples():
        for side, pmod, cost in [("over", r.pm_ol, r.open_over_cost),
                                 ("under", 1 - r.pm_ol, r.open_under_cost)]:
            if pd.isna(cost):
                continue
            o = float(amer_to_dec(cost))
            if pmod * o - 1 < 0.02:
                continue
            if r.actual == r.open_line:
                pnl = 0.0
            else:
                pnl = (o - 1) if (r.actual > r.open_line) == (side == "over") else -1.0
            rows.append({"date": r.date, "pnl": pnl})
    b = pd.DataFrame(rows).sort_values("date")
    daily = b.groupby("date").pnl.sum().cumsum()
    xs = pd.to_datetime(daily.index)
    ax2.set_facecolor(SURFACE)
    ax2.plot(xs, daily.values, color=ORANGE, lw=2)
    ax2.axhline(0, color="#bbbbbb", lw=0.8)
    ax2.set_ylabel("cumulative units (flat 1u)")
    ax2.set_title(f"EV>2% at open prices: {len(b)} bets,\n"
                  f"{daily.iloc[-1]:+.0f}u ({b.pnl.mean():+.1%} ROI)", fontsize=11)
    for ax in (ax1, ax2):
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        ax.tick_params(labelsize=9)

    fig.tight_layout()
    out = os.path.join(ROOT, "results", "results.png")
    fig.savefig(out, dpi=150, facecolor=SURFACE)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
