"""Baseline market efficiency: is the early line worse than the close?

If the closing line significantly beats the early line on log loss, the early
line is by definition not the efficient price — information arriving between
posting and kickoff improves it. That's the wedge this project targets.
"""
import os

import numpy as np
import pandas as pd
from scipy import stats

from odds_utils import OUTCOME_IDX, devig_proportional, devig_shin, log_loss_vec

DATA = os.path.join(os.path.dirname(__file__), "..", "data", "matches.pkl")

TIERS = {
    "top": ["E0", "D1", "I1", "SP1", "F1"],
    "second": ["E1", "D2", "I2", "SP2", "F2"],
    "lower_england": ["E2", "E3", "EC"],
    "scotland": ["SC0", "SC1", "SC2", "SC3"],
    "small_top": ["N1", "B1", "P1", "T1", "G1"],
}
DIV_TIER = {d: t for t, divs in TIERS.items() for d in divs}


def main():
    df = pd.read_pickle(DATA)
    df = df[df["has_ps_early"] & df["has_ps_close"]].copy()
    df["tier"] = df["Div"].map(DIV_TIER)
    y = df["FTR"].map(OUTCOME_IDX).to_numpy()

    early = df[["PSH", "PSD", "PSA"]].to_numpy(float)
    close = df[["PSCH", "PSCD", "PSCA"]].to_numpy(float)

    print(f"{len(df)} matches with Pinnacle early+close "
          f"({df['Date'].min().date()} .. {df['Date'].max().date()})\n")

    for name, devig in [("proportional", devig_proportional), ("shin", devig_shin)]:
        ll_e = log_loss_vec(devig(early), y)
        ll_c = log_loss_vec(devig(close), y)
        d = ll_e - ll_c  # >0 means close better
        t, p = stats.ttest_1samp(d, 0)
        print(f"[{name}] early LL {ll_e.mean():.5f} | close LL {ll_c.mean():.5f} "
              f"| diff {d.mean():+.5f} (t={t:.2f}, p={p:.2e})")

    print("\nBy tier (shin de-vig): early vs close log loss")
    pe, pc = devig_shin(early), devig_shin(close)
    ll_e, ll_c = log_loss_vec(pe, y), log_loss_vec(pc, y)
    df["_lle"], df["_llc"] = ll_e, ll_c
    rows = []
    for tier, g in df.groupby("tier"):
        d = g["_lle"] - g["_llc"]
        t, p = stats.ttest_1samp(d, 0)
        rows.append({"tier": tier, "n": len(g), "early_LL": g["_lle"].mean(),
                     "close_LL": g["_llc"].mean(), "diff": d.mean(), "t": t, "p": p})
    print(pd.DataFrame(rows).set_index("tier").round(5).to_string())

    # How much does the line actually move? (mean abs prob change, home side)
    move = np.abs(pe[:, 0] - pc[:, 0])
    df["_move"] = move
    print("\nMean |home prob move| open->close by tier:")
    print(df.groupby("tier")["_move"].mean().round(4).to_string())

    # Overround comparison: early vs close vig, by tier
    df["_vig_e"] = (1 / early).sum(axis=1) - 1
    df["_vig_c"] = (1 / close).sum(axis=1) - 1
    print("\nPinnacle overround (early vs close) by tier:")
    print(df.groupby("tier")[["_vig_e", "_vig_c"]].mean().round(4).to_string())


if __name__ == "__main__":
    main()
