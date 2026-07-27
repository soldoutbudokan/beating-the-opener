"""Train line-blind win probability models and benchmark against the closing line.

Protocol
--------
* Held-out = the last three seasons (2023-24, 2024-25, 2025-26); never trained on.
* Walk-forward: for each held-out season the model is refit on everything strictly
  before that season, so no future information reaches a prediction.
* The standalone model never sees any market variable.
"""
import os
import sys

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from market import brier_vec, log_loss_vec  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOLDOUT_SEASONS = [2024, 2025, 2026]

MARKET_COLS = {
    "mkt_mult", "mkt_shin", "mkt_add", "mkt_power", "mkt_open_mult",
    "home_ml_close", "away_ml_close", "market_spread", "market_total",
    "mkt_overround", "home_win", "margin", "game_id", "date_utc", "game_date",
    "season_year", "season_type", "home_abbr", "away_abbr",
}


def feature_cols(df):
    cols = [c for c in df.columns if c not in MARKET_COLS]
    return [c for c in cols if pd.api.types.is_numeric_dtype(df[c])]


def evaluate(y, p, label=""):
    return {
        "model": label,
        "n": len(y),
        "logloss": log_loss_vec(y, p).mean(),
        "brier": brier_vec(y, p).mean(),
        "acc": float(((p > 0.5) == y).mean()),
    }


def walk_forward(df, cols, make_model, use_market=None, min_gp=0):
    """Refit before each held-out season; return out-of-sample predictions."""
    preds = np.full(len(df), np.nan)
    for season in HOLDOUT_SEASONS:
        tr = df[df.season_year < season]
        te_mask = df.season_year == season
        te = df[te_mask]
        if min_gp:
            tr = tr[(tr.home_gp >= min_gp) & (tr.away_gp >= min_gp)]
        feats = list(cols) + (list(use_market) if use_market else [])
        Xtr = tr[feats].astype(float).values
        ytr = tr.home_win.values
        Xte = te[feats].astype(float).values
        model = make_model()
        model.fit(Xtr, ytr)
        preds[te_mask.values] = model.predict_proba(Xte)[:, 1]
    return preds


def mk_logit():
    from sklearn.pipeline import make_pipeline
    from sklearn.impute import SimpleImputer
    return make_pipeline(
        SimpleImputer(strategy="median"),
        StandardScaler(),
        LogisticRegression(C=0.05, max_iter=2000),
    )


def mk_gbm(**kw):
    params = dict(
        max_iter=400, learning_rate=0.03, max_depth=4, min_samples_leaf=80,
        l2_regularization=1.0, max_leaf_nodes=15, early_stopping=True,
        validation_fraction=0.15, random_state=0,
    )
    params.update(kw)
    return HistGradientBoostingClassifier(**params)


def main():
    df = pd.read_csv(os.path.join(ROOT, "data", "raw", "dataset.csv"))
    df = df[df.season_type.isin([2, 3, 5])].copy()
    cols = feature_cols(df)
    print(f"{len(df)} games, {len(cols)} features")

    ho = df.season_year.isin(HOLDOUT_SEASONS)
    df["p_logit"] = walk_forward(df, cols, mk_logit)
    df["p_gbm"] = walk_forward(df, cols, mk_gbm)
    df["p_blend_nomkt"] = 0.5 * df.p_logit + 0.5 * df.p_gbm

    test = df[ho & df.mkt_mult.notna()].copy()
    y = test.home_win.values

    rows = [
        evaluate(y, test.mkt_mult.values, "MARKET closing line (devig)"),
        evaluate(y, test.elo_prob.values, "Elo only"),
        evaluate(y, test.p_logit.values, "Logistic (line-blind)"),
        evaluate(y, test.p_gbm.values, "GBM (line-blind)"),
        evaluate(y, test.p_blend_nomkt.values, "Logit+GBM avg (line-blind)"),
    ]
    res = pd.DataFrame(rows)
    mkt_ll = res.loc[0, "logloss"]
    res["vs_market"] = res.logloss - mkt_ll
    pd.set_option("display.width", 200)
    print("\n=== HELD-OUT: 2023-24, 2024-25, 2025-26 ===")
    print(res.round(5).to_string(index=False))

    df.to_csv(os.path.join(ROOT, "data", "raw", "preds_base.csv"), index=False)
    return df


if __name__ == "__main__":
    main()
