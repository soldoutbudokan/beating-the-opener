"""What does the closing line know that the model does not?

Regress logit(market) on logit(model); the residual is the market's private
information. Then ask which observable quantities predict that residual. Anything
that does is a feature worth building. Anything that does not is news or order
flow, which no amount of modelling recovers.
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from encompassing import irls_logit, logit  # noqa: E402
from market import log_loss_vec  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST = [2024, 2025, 2026]


def ols(X, y):
    X1 = np.column_stack([np.ones(len(X)), X])
    beta, *_ = np.linalg.lstsq(X1, y, rcond=None)
    resid = y - X1 @ beta
    s2 = resid @ resid / (len(y) - X1.shape[1])
    cov = s2 * np.linalg.pinv(X1.T @ X1)
    se = np.sqrt(np.diag(cov))
    return beta, se, resid


def main():
    df = pd.read_csv(os.path.join(ROOT, "data", "raw", "preds_markets.csv"))
    t = df[df.season_year.isin(TEST) & df.mkt_mult.notna()
           & df.B_stack.notna()].copy().reset_index(drop=True)
    y = t.home_win.values.astype(float)
    lm = logit(t.mkt_mult.values)
    lb = logit(t.B_stack.values)

    # market's private signal = part of the line orthogonal to the model
    beta, se, resid = ols(lb.reshape(-1, 1), lm)
    t["mkt_resid"] = resid
    print(f"logit(market) ~ logit(model):  slope={beta[1]:.4f}  "
          f"R^2={1 - resid.var()/lm.var():.4f}")
    print(f"residual sd = {resid.std():.4f} (in logit units)\n")

    # Does the residual actually predict outcomes? (it must, if the market is better)
    Xb = np.column_stack([lb, resid])
    b2, se2 = irls_logit(Xb, y)
    print("outcome ~ model + market_residual:")
    print(f"  model    {b2[1]:+.4f} (se {se2[1]:.4f})")
    print(f"  residual {b2[2]:+.4f} (se {se2[2]:.4f}, z={b2[2]/se2[2]:+.2f})")
    print("  -> the residual is real, exploitable information the model lacks\n")

    # What observable quantities explain the residual?
    cand = [c for c in df.columns if c not in (
        "game_id", "date_utc", "game_date", "home_abbr", "away_abbr", "home_win",
        "margin", "total_pts", "home_score", "away_score") ]
    cand = [c for c in cand if pd.api.types.is_numeric_dtype(t[c])
            and not c.startswith(("A_", "B_", "mkt", "market", "home_ml", "away_ml",
                                  "pred_margin", "pred_total"))]
    rows = []
    for c in cand:
        v = t[c].astype(float).values
        if not np.isfinite(v).all() or np.std(v) == 0:
            continue
        r = np.corrcoef(v, resid)[0, 1]
        rows.append({"feature": c, "corr_with_market_private_signal": r})
    r = pd.DataFrame(rows).sort_values(
        "corr_with_market_private_signal", key=lambda s: -s.abs())
    print("=== Observables most correlated with the market's private signal ===")
    print(r.head(14).round(4).to_string(index=False))
    top = r.corr_with_market_private_signal.abs().max()
    print(f"\n  strongest |corr| = {top:.4f}  "
          f"-> {'a buildable feature exists' if top > 0.25 else 'nothing observable explains it'}")

    # How much of the residual is explainable by ALL observables jointly?
    good = [c for c in r.feature if np.isfinite(t[c].astype(float).values).all()]
    Xall = t[good].astype(float).values
    Xall = (Xall - Xall.mean(0)) / (Xall.std(0) + 1e-9)
    _, _, res2 = ols(Xall, resid)
    r2 = 1 - res2.var() / resid.var()
    print(f"\n  R^2 of ALL {len(good)} observables jointly on the residual: {r2:.4f}")
    print(f"  -> {1-r2:.1%} of the market's edge is orthogonal to everything measured")

    # Upper bound: if we perfectly recovered the explainable part, what log loss?
    recovered = lb + (resid - res2)          # model + explainable part of residual
    p_rec = 1 / (1 + np.exp(-recovered))
    b3, _ = irls_logit(recovered.reshape(-1, 1), y)
    p_cal = 1 / (1 + np.exp(-(b3[0] + b3[1] * recovered)))
    print(f"\n  ceiling if every observable were perfectly exploited:")
    print(f"    log loss {float(log_loss_vec(y, p_cal).mean()):.5f}   "
          f"(market {float(log_loss_vec(y, t.mkt_mult.values).mean()):.5f}, "
          f"model {float(log_loss_vec(y, t.B_stack.values).mean()):.5f})")
    print("    NOTE: this is an in-sample, optimistic bound -- a real model scores worse.")


if __name__ == "__main__":
    main()
