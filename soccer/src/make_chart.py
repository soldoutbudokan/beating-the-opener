"""Results figure for the README: per-season capture vs wedge + cumulative P&L."""
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from odds_utils import log_loss_vec

RESULTS = os.path.join(os.path.dirname(__file__), "..", "results")

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
BASE = "#c3c2b7"
BLUE = "#2a78d6"    # model
ORANGE = "#eb6834"  # market (open->close wedge)


def style_ax(ax):
    for s in ["top", "right", "left"]:
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(BASE)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.set_facecolor(SURFACE)


def main():
    d = pd.read_pickle(os.path.join(RESULTS, "preds_v4.pkl"))
    ym = d["y"].to_numpy()
    ll_open = log_loss_vec(d[["po_h", "po_d", "po_a"]].to_numpy(), ym)
    ll_close = log_loss_vec(d[["pc_h", "pc_d", "pc_a"]].to_numpy(), ym)
    ll_ens = log_loss_vec(d[["ens_h", "ens_d", "ens_a"]].to_numpy(), ym)
    seasons = d["season"].to_numpy()
    per = pd.DataFrame({
        "wedge": pd.Series(ll_open - ll_close).groupby(seasons).mean(),
        "capture": pd.Series(ll_open - ll_ens).groupby(seasons).mean(),
    })
    cum = pd.read_csv(os.path.join(RESULTS, "cum_pnl.csv"), parse_dates=["date"])

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.2), dpi=150)
    fig.patch.set_facecolor(SURFACE)

    x = np.arange(len(per))
    w = 0.38
    ax1.bar(x - w / 2, per["wedge"] * 1000, w, color=ORANGE,
            label="open→close wedge (available info)", zorder=3)
    ax1.bar(x + w / 2, per["capture"] * 1000, w, color=BLUE,
            label="model capture vs opener", zorder=3)
    style_ax(ax1)
    ax1.set_xticks(x)
    ax1.set_xticklabels([s[2:4] + "/" + s[-2:] for s in per.index], color=INK2)
    ax1.axhline(0, color=BASE, linewidth=1)
    ax1.set_title("Log-loss improvement over the opening line, per season (×1000)",
                  fontsize=10, color=INK, loc="left")
    ax1.legend(frameon=False, fontsize=8.5, labelcolor=INK2, loc="upper right")

    ax2.plot(cum["date"], cum["cum_units"], color=BLUE, linewidth=2)
    style_ax(ax2)
    ax2.set_title("Cumulative P&L, flat 1u @ best-of-book early price, EV>2%\n"
                  "(31,192 bets, +5.2% ROI, +2.2% CLV)",
                  fontsize=10, color=INK, loc="left")
    ax2.tick_params(axis="x", labelcolor=INK2)
    ax2.set_ylabel("units", fontsize=9, color=MUTED)

    fig.tight_layout()
    out = os.path.join(RESULTS, "results.png")
    fig.savefig(out, facecolor=SURFACE, bbox_inches="tight")
    print(f"saved {out}")


if __name__ == "__main__":
    main()
