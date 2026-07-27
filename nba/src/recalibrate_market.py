"""Is the de-vigged closing line itself mis-calibrated?

The encompassing regression put the market's coefficient at ~1.15, significantly
above 1, which says the de-vigged probabilities are pulled too far toward 0.5.
That is a property of the vig-removal, not of the bookmaker's opinion: splitting
the overround evenly overtaxes the favourite.

Here we fit a one-parameter sharpening  logit(p') = a + b*logit(p)  on validation
seasons only, then apply it, frozen, to the three held-out seasons.
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model as M  # noqa: E402
from encompassing import irls_logit, logit  # noqa: E402
from market import log_loss_vec  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VAL = [2019, 2020, 2021, 2022, 2023]
TEST = [2024, 2025, 2026]
METHODS = ["mkt_mult", "mkt_add", "mkt_shin", "mkt_power"]


def main():
    df = pd.read_csv(os.path.join(ROOT, "data", "raw", "preds_markets.csv"))
    have = [m for m in METHODS if m in df.columns]
    d = df[df[have].notna().all(axis=1)].copy()
    val = d[d.season_year.isin(VAL)]
    test = d[d.season_year.isin(TEST)]
    print(f"validation n={len(val)}  held-out n={len(test)}\n")

    print("=== Raw de-vig methods on HELD-OUT ===")
    yt = test.home_win.values
    for m in have:
        print(f"  {m:11s} logloss={float(log_loss_vec(yt, test[m].values).mean()):.5f}")

    print("\n=== Sharpening fit on VALIDATION seasons only ===")
    rows = []
    for m in have:
        beta, se = irls_logit(logit(val[m].values).reshape(-1, 1),
                              val.home_win.values.astype(float))
        a, b = beta[0], beta[1]
        print(f"  {m:11s} a={a:+.4f}  b={b:.4f} (se {se[1]:.4f})")
        p_new = 1 / (1 + np.exp(-(a + b * logit(test[m].values))))
        ll_raw = float(log_loss_vec(yt, test[m].values).mean())
        ll_new = float(log_loss_vec(yt, p_new).mean())
        st = M.paired_test(yt, p_new, test[m].values)
        rows.append({"devig": m, "raw": ll_raw, "recalibrated": ll_new,
                     "diff": ll_new - ll_raw, "ci_lo": st["ci_lo"],
                     "ci_hi": st["ci_hi"], "p": st["p_two_sided"]})
        test = test.copy()
        test[f"{m}_recal"] = p_new

    r = pd.DataFrame(rows)
    pd.set_option("display.width", 220)
    print("\n=== HELD-OUT: recalibrated vs raw closing line ===")
    print(r.round(5).to_string(index=False))
    best = r.sort_values("recalibrated").iloc[0]
    print(f"\n  best: {best.devig} recalibrated -> {best.recalibrated:.5f} "
          f"vs raw {best.raw:.5f} (diff {best['diff']:+.5f}, p={best.p:.4f})")

    # Per-season stability of the correction.
    print("\n=== Per-season (mkt_mult) ===")
    beta, _ = irls_logit(logit(val.mkt_mult.values).reshape(-1, 1),
                         val.home_win.values.astype(float))
    for s in TEST:
        ss = test[test.season_year == s]
        pn = 1 / (1 + np.exp(-(beta[0] + beta[1] * logit(ss.mkt_mult.values))))
        print(f"  {s}: raw={float(log_loss_vec(ss.home_win.values, ss.mkt_mult.values).mean()):.5f} "
              f"recal={float(log_loss_vec(ss.home_win.values, pn).mean()):.5f}")

    # Does the sharpening translate into bets? It should not: same prices, same vig.
    print("\n=== Does sharpening create betting value? (closing prices, edge>2%) ===")
    from evaluate_betting import report, simulate
    tt = test.copy()
    tt["p_recal"] = 1 / (1 + np.exp(-(beta[0] + beta[1] * logit(tt.mkt_mult.values))))
    for c, lab in [("mkt_mult", "raw market"), ("p_recal", "recalibrated market")]:
        led = simulate(tt, c, edge_threshold=0.02)
        report(led, lab)


if __name__ == "__main__":
    main()
