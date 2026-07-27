"""Encompassing test + subset search.

The encompassing regression is the cleanest statement of the result:

    logit(P(home win)) = a + b1*logit(market) + b2*logit(model)

If b2 is indistinguishable from zero, the closing line already contains
everything the model knows, and no combination of the two can beat the line.
Fit on validation seasons, coefficients reported with held-out standard errors.
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from market import log_loss_vec  # noqa: E402
import model as M  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST = [2024, 2025, 2026]


def logit(p, eps=1e-6):
    p = np.clip(p, eps, 1 - eps)
    return np.log(p / (1 - p))


def irls_logit(X, y, iters=60):
    """Plain Newton-Raphson logistic fit returning coefficients and SEs."""
    X = np.column_stack([np.ones(len(X)), X])
    beta = np.zeros(X.shape[1])
    for _ in range(iters):
        eta = X @ beta
        p = 1 / (1 + np.exp(-eta))
        W = p * (1 - p)
        W = np.clip(W, 1e-9, None)
        z = eta + (y - p) / W
        XtW = X.T * W
        try:
            beta_new = np.linalg.solve(XtW @ X, XtW @ z)
        except np.linalg.LinAlgError:
            break
        if np.max(np.abs(beta_new - beta)) < 1e-9:
            beta = beta_new
            break
        beta = beta_new
    eta = X @ beta
    p = 1 / (1 + np.exp(-eta))
    W = np.clip(p * (1 - p), 1e-9, None)
    cov = np.linalg.inv((X.T * W) @ X)
    se = np.sqrt(np.diag(cov))
    return beta, se


def main():
    df = pd.read_csv(os.path.join(ROOT, "data", "raw", "preds_markets.csv"))
    t = df[df.season_year.isin(TEST) & df.mkt_mult.notna()
           & df.A_stack.notna() & df.B_stack.notna()].copy()
    y = t.home_win.values.astype(float)
    print(f"held-out n = {len(t)}\n")

    print("=== ENCOMPASSING REGRESSION (fit on held-out for inference) ===")
    print("    logit(y) ~ 1 + logit(market) + logit(model)\n")
    for c, lab in [("A_stack", "Tier A (strict)"), ("B_stack", "Tier B (pre-tip)")]:
        X = np.column_stack([logit(t.mkt_mult.values), logit(t[c].values)])
        beta, se = irls_logit(X, y)
        zmkt, zmod = beta[1] / se[1], beta[2] / se[2]
        from scipy import stats as st
        p_mod = 2 * (1 - st.norm.cdf(abs(zmod)))
        print(f"  {lab}")
        print(f"    market coef = {beta[1]:+.4f} (se {se[1]:.4f}, z={zmkt:+.2f})")
        print(f"    model  coef = {beta[2]:+.4f} (se {se[2]:.4f}, z={zmod:+.2f}, p={p_mod:.4f})")
        verdict = ("model adds information" if p_mod < 0.05 and beta[2] > 0
                   else "market ENCOMPASSES the model (no added information)")
        print(f"    -> {verdict}\n")

    print("=== SUBSET SEARCH: any regime where the model beats the line? ===")
    t["absmiss"] = t[["home_rot_missing_talent", "away_rot_missing_talent"]].max(axis=1)
    t["fav_strength"] = (t.mkt_mult - 0.5).abs()
    subsets = {
        "early season (<12 gp)": t.early_season == 1,
        "late season": t.days_into_season > 0.8 if "days_into_season" in t else None,
        "playoffs": t.is_playoff == 1,
        "back-to-back involved": (t.home_b2b == 1) | (t.away_b2b == 1),
        "heavy injuries (top quartile)": t.absmiss >= t.absmiss.quantile(0.75),
        "big favourites (|p-.5|>.25)": t.fav_strength > 0.25,
        "close games (|p-.5|<.08)": t.fav_strength < 0.08,
        "long rest edge (|rest diff|>=2)": t.rest_diff.abs() >= 2,
        "high travel": t.travel_diff.abs() > 1500,
    }
    rows = []
    for name, mask in subsets.items():
        if mask is None:
            continue
        s = t[mask]
        if len(s) < 150:
            continue
        mk = float(log_loss_vec(s.home_win.values, s.mkt_mult.values).mean())
        for c, lab in [("A_stack", "TierA"), ("B_stack", "TierB")]:
            md = float(log_loss_vec(s.home_win.values, s[c].values).mean())
            st_ = M.paired_test(s.home_win.values, s[c].values, s.mkt_mult.values)
            rows.append({"subset": name, "model": lab, "n": len(s), "market": mk,
                         "model_ll": md, "diff": md - mk,
                         "p": st_["p_two_sided"]})
    r = pd.DataFrame(rows)
    pd.set_option("display.width", 220)
    print(r.round(5).to_string(index=False))
    wins = r[(r["diff"] < 0) & (r.p < 0.05)]
    print(f"\n  subsets where model significantly beats the line: {len(wins)}")
    if len(wins):
        print(wins.to_string(index=False))
        print("  NOTE: with ~18 comparisons, expect ~1 false positive at p<0.05.")


if __name__ == "__main__":
    main()
