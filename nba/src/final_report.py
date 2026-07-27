"""Consolidate every headline number into one JSON blob for the write-up."""
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model as M  # noqa: E402
from market import log_loss_vec  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST = [2024, 2025, 2026]
SEASON_LABEL = {2024: "2023-24", 2025: "2024-25", 2026: "2025-26"}


def main():
    df = pd.read_csv(os.path.join(ROOT, "data", "raw", "preds_markets.csv"))
    t = df[df.season_year.isin(TEST) & df.mkt_mult.notna()
           & df.A_stack.notna() & df.B_stack.notna()].copy()
    y = t.home_win.values
    out = {"n_holdout": int(len(t)), "n_total": int(len(df)),
           "home_win_rate": float(y.mean()),
           "seasons": [SEASON_LABEL[s] for s in TEST]}

    models = [("mkt_mult", "Vegas closing line"), ("mkt_open_mult", "Vegas opening line"),
              ("elo_prob", "Elo baseline"), ("A_stack", "Model - Tier A (strict)"),
              ("B_stack", "Model - Tier B (pre-tip)"),
              ("A_blend", "Tier A + line blend"), ("B_blend", "Tier B + line blend")]
    rows = []
    for c, lab in models:
        if c not in t:
            continue
        p = t[c].values
        ok = np.isfinite(p)
        e = M.evaluate(y[ok], p[ok], lab)
        if c != "mkt_mult":
            st = M.paired_test(y[ok], p[ok], t.mkt_mult.values[ok])
            e.update({"vs_mkt": e["logloss"] - float(
                log_loss_vec(y[ok], t.mkt_mult.values[ok]).mean()),
                "p_value": st["p_two_sided"], "ci_lo": st["ci_lo"],
                "ci_hi": st["ci_hi"]})
        else:
            e.update({"vs_mkt": 0.0, "p_value": None})
        rows.append(e)
    out["main_table"] = rows

    # per-season
    per = []
    for s in TEST:
        ss = t[t.season_year == s]
        row = {"season": SEASON_LABEL[s], "n": int(len(ss))}
        for c, lab in [("mkt_mult", "market"), ("A_stack", "tierA"),
                       ("B_stack", "tierB"), ("B_blend", "blend")]:
            row[lab] = float(log_loss_vec(ss.home_win.values, ss[c].values).mean())
        per.append(row)
    out["per_season"] = per

    # calibration curves
    def calib(p, nb=10):
        qs = np.quantile(p, np.linspace(0, 1, nb + 1))
        qs[-1] += 1e-9
        pts = []
        for i in range(nb):
            m = (p >= qs[i]) & (p < qs[i + 1])
            if m.sum() < 10:
                continue
            pts.append({"pred": float(p[m].mean()), "actual": float(y[m].mean()),
                        "n": int(m.sum())})
        return pts
    out["calibration"] = {"market": calib(t.mkt_mult.values),
                          "tierB": calib(t.B_stack.values)}

    # feature-group ablation summary (log loss on held-out)
    out["ablation"] = [
        {"name": "Elo only", "ll": float(log_loss_vec(y, t.elo_prob.values).mean())},
        {"name": "Tier A (ratings, rest, travel, form)",
         "ll": float(log_loss_vec(y, t.A_stack.values).mean())},
        {"name": "Tier B (+ rotation availability)",
         "ll": float(log_loss_vec(y, t.B_stack.values).mean())},
        {"name": "Vegas closing line",
         "ll": float(log_loss_vec(y, t.mkt_mult.values).mean())},
    ]

    # market efficiency stats
    out["market"] = {
        "overround": float(np.nanmean(t.mkt_overround)),
        "accuracy": float(((t.mkt_mult.values > .5) == y).mean()),
        "logloss": float(log_loss_vec(y, t.mkt_mult.values).mean()),
        "brier": float(((y - t.mkt_mult.values) ** 2).mean()),
    }

    with open(os.path.join(ROOT, "reports", "results.json"), "w") as f:
        json.dump(out, f, indent=2)
    pd.set_option("display.width", 220)
    print(pd.DataFrame(rows).round(5).to_string(index=False))
    print()
    print(pd.DataFrame(per).round(5).to_string(index=False))
    print(f"\nwrote reports/results.json")


if __name__ == "__main__":
    main()
