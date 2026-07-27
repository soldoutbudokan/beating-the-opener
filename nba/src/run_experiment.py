"""Full experiment: walk-forward predictions, stacking, and market benchmark.

Season roles
------------
  <= 2018        pure training history
  2019 .. 2023   walk-forward validation -- used to fit ensemble weights,
                 calibration, and the market blend
  2024 .. 2026   HELD OUT -- the last three seasons, touched only at scoring time

Every prediction for season S comes from models fit exclusively on seasons < S.
"""
import os
import sys

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from scipy import stats

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model as M  # noqa: E402
from market import log_loss_vec  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRED_SEASONS = [2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026]
VAL = [2019, 2020, 2021, 2022, 2023]
TEST = [2024, 2025, 2026]


def wf_proba(df, cols, factory, seasons):
    out = np.full(len(df), np.nan)
    for s in seasons:
        tr = df[df.season_year < s]
        te = (df.season_year == s).values
        m = factory()
        m.fit(tr[cols].astype(float).values, tr.home_win.values)
        out[te] = m.predict_proba(df[te][cols].astype(float).values)[:, 1]
    return out


def wf_margin(df, cols, factory, seasons):
    out = np.full(len(df), np.nan)
    for s in seasons:
        tr = df[df.season_year < s]
        te = (df.season_year == s).values
        m = factory()
        ytr = tr.margin.values.astype(float)
        m.fit(tr[cols].astype(float).values, ytr)
        sigma = float(np.std(ytr - m.predict(tr[cols].astype(float).values)))
        out[te] = stats.norm.cdf(
            m.predict(df[te][cols].astype(float).values) / sigma)
    return out


def logit(p, eps=1e-6):
    p = np.clip(p, eps, 1 - eps)
    return np.log(p / (1 - p))


def main():
    df = pd.read_csv(os.path.join(ROOT, "data", "raw", "dataset_v3.csv"))
    df = df[df.season_type.isin([2, 3, 5])].copy().reset_index(drop=True)
    cols = M.blind_features(df)
    print(f"{len(df)} games | {len(cols)} line-blind features")

    base_models = {
        "logit": lambda: M.mk_logit(C=0.03),
        "logit_wide": lambda: M.mk_logit(C=0.3),
        "gbm": lambda: M.mk_gbm(),
        "gbm_deep": lambda: M.mk_gbm(max_leaf_nodes=31, learning_rate=0.02,
                                     min_samples_leaf=60),
        "gbm_shallow": lambda: M.mk_gbm(max_leaf_nodes=7, learning_rate=0.04,
                                        min_samples_leaf=150),
    }
    for name, f in base_models.items():
        df[f"p_{name}"] = wf_proba(df, cols, f, PRED_SEASONS)
        print(f"  fitted {name}")

    df["p_marg_gbm"] = wf_margin(df, cols, M.mk_gbm_reg, PRED_SEASONS)
    df["p_marg_ridge"] = wf_margin(df, cols, M.mk_ridge, PRED_SEASONS)
    print("  fitted margin models")

    BASE = [f"p_{n}" for n in base_models] + ["p_marg_gbm", "p_marg_ridge"]

    # ---- stack the blind models on validation seasons only ----
    val = df[df.season_year.isin(VAL) & df[BASE].notna().all(axis=1)]
    stack = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                          LogisticRegression(C=1.0, max_iter=2000))
    stack.fit(logit(val[BASE].values), val.home_win.values)
    df["p_stack"] = np.nan
    ok = df[BASE].notna().all(axis=1)
    df.loc[ok, "p_stack"] = stack.predict_proba(logit(df.loc[ok, BASE].values))[:, 1]
    df["p_mean"] = df[BASE].mean(axis=1)
    print("  stacked (weights fit on 2019-2023 only)")

    # ---- market blend: does the model add information to the closing line? ----
    valm = df[df.season_year.isin(VAL) & df.mkt_mult.notna() & ok]
    blend = make_pipeline(StandardScaler(),
                          LogisticRegression(C=1.0, max_iter=2000))
    Xv = np.column_stack([logit(valm.mkt_mult.values), logit(valm.p_stack.values)])
    blend.fit(Xv, valm.home_win.values)
    df["p_blend"] = np.nan
    bm = ok & df.mkt_mult.notna()
    Xa = np.column_stack([logit(df.loc[bm, "mkt_mult"].values),
                          logit(df.loc[bm, "p_stack"].values)])
    df.loc[bm, "p_blend"] = blend.predict_proba(Xa)[:, 1]
    coef = blend.named_steps["logisticregression"].coef_[0]
    print(f"  blend coefficients (standardised): market={coef[0]:.3f} model={coef[1]:.3f}")

    df.to_csv(os.path.join(ROOT, "data", "raw", "preds_final.csv"), index=False)

    # ---- report ----
    t = df[df.season_year.isin(TEST) & df.mkt_mult.notna() & ok]
    y = t.home_win.values
    rows = [M.evaluate(y, t.mkt_mult.values, "MARKET closing line")]
    for c, lab in [("elo_prob", "Elo only"), ("p_logit", "Logistic (blind)"),
                   ("p_gbm", "GBM (blind)"), ("p_marg_gbm", "GBM margin (blind)"),
                   ("p_mean", "Simple avg (blind)"), ("p_stack", "STACK (blind)"),
                   ("p_blend", "STACK + market blend")]:
        rows.append(M.evaluate(y, t[c].values, lab))
    r = pd.DataFrame(rows)
    r["vs_mkt"] = r.logloss - r.logloss[0]
    pd.set_option("display.width", 220)
    print(f"\n=== HELD-OUT 2023-24 / 2024-25 / 2025-26  (n={len(t)}) ===")
    print(r.round(5).to_string(index=False))

    print("\n=== SIGNIFICANCE vs closing line (paired bootstrap, 10k) ===")
    for c, lab in [("p_stack", "STACK (blind)"), ("p_blend", "STACK + market blend")]:
        s = M.paired_test(y, t[c].values, t.mkt_mult.values)
        print(f"  {lab:22s} dLL={s['mean_diff']:+.5f} "
              f"CI[{s['ci_lo']:+.5f},{s['ci_hi']:+.5f}] "
              f"P(better)={s['p_better']:.3f} t={s['t']:+.2f} p={s['p_two_sided']:.4f}")
    return df


if __name__ == "__main__":
    main()
