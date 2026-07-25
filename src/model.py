"""Model suite + benchmark against the closing line.

Design rules that keep the comparison honest:
  * Held-out = 2023-24, 2024-25, 2025-26. Never used for fitting or tuning.
  * Walk-forward: predictions for season S come from a model fit only on S-1 and
    earlier.
  * "Line-blind" models never see any market variable.
  * Blend models DO use the closing line as an input. They answer a different
    question: does the model carry information the line does not already contain?
"""
import os
import sys

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from market import brier_vec, log_loss_vec  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOLDOUT = [2024, 2025, 2026]

# Anything market-derived or outcome-derived must stay out of blind features.
EXCLUDE = {
    "game_id", "date_utc", "game_date", "season_year", "season_type",
    "home_abbr", "away_abbr", "home_win", "margin",
    "mkt_mult", "mkt_shin", "mkt_add", "mkt_power", "mkt_open_mult",
    "home_ml_close", "away_ml_close", "home_ml_open", "away_ml_open",
    "market_spread", "market_total", "mkt_overround",
    "total_pts", "home_score", "away_score",
}


def blind_features(df):
    cols = [c for c in df.columns if c not in EXCLUDE]
    return [c for c in cols if pd.api.types.is_numeric_dtype(df[c])]


def mk_logit(C=0.03):
    return make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                         LogisticRegression(C=C, max_iter=3000))


def mk_gbm(**kw):
    p = dict(max_iter=500, learning_rate=0.025, max_depth=None, max_leaf_nodes=15,
             min_samples_leaf=100, l2_regularization=2.0, early_stopping=True,
             validation_fraction=0.15, n_iter_no_change=40, random_state=0)
    p.update(kw)
    return HistGradientBoostingClassifier(**p)


def mk_gbm_reg(**kw):
    p = dict(max_iter=500, learning_rate=0.025, max_leaf_nodes=15,
             min_samples_leaf=100, l2_regularization=2.0, early_stopping=True,
             validation_fraction=0.15, n_iter_no_change=40, random_state=0)
    p.update(kw)
    return HistGradientBoostingRegressor(**p)


def mk_ridge():
    return make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                         Ridge(alpha=50.0))


def walk_forward_proba(df, cols, factory, target="home_win"):
    out = np.full(len(df), np.nan)
    for s in HOLDOUT:
        tr = df[df.season_year < s]
        te_mask = (df.season_year == s).values
        m = factory()
        m.fit(tr[cols].astype(float).values, tr[target].values)
        out[te_mask] = m.predict_proba(df[te_mask][cols].astype(float).values)[:, 1]
    return out


def walk_forward_margin(df, cols, factory):
    """Predict margin, then map to win prob via a fitted normal scale."""
    out = np.full(len(df), np.nan)
    for s in HOLDOUT:
        tr = df[df.season_year < s]
        te_mask = (df.season_year == s).values
        m = factory()
        m.fit(tr[cols].astype(float).values, tr.margin.values.astype(float))
        pred_tr = m.predict(tr[cols].astype(float).values)
        sigma = float(np.std(tr.margin.values - pred_tr))
        pred_te = m.predict(df[te_mask][cols].astype(float).values)
        out[te_mask] = stats.norm.cdf(pred_te / sigma)
    return out


def evaluate(y, p, label):
    ok = np.isfinite(p)
    y, p = y[ok], p[ok]
    return {"model": label, "n": len(y),
            "logloss": float(log_loss_vec(y, p).mean()),
            "brier": float(brier_vec(y, p).mean()),
            "acc": float(((p > 0.5) == y).mean())}


def paired_test(y, p_a, p_b, n_boot=10000, seed=0):
    """Paired bootstrap on per-game log-loss difference (a - b)."""
    ok = np.isfinite(p_a) & np.isfinite(p_b)
    d = log_loss_vec(y[ok], p_a[ok]) - log_loss_vec(y[ok], p_b[ok])
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(d), size=(n_boot, len(d)))
    boots = d[idx].mean(axis=1)
    # Diebold-Mariano style t-stat with HAC-free simple SE (games are independent).
    t = d.mean() / (d.std(ddof=1) / np.sqrt(len(d)))
    return {"mean_diff": float(d.mean()),
            "ci_lo": float(np.percentile(boots, 2.5)),
            "ci_hi": float(np.percentile(boots, 97.5)),
            "p_better": float((boots < 0).mean()),
            "t": float(t),
            "p_two_sided": float(2 * (1 - stats.norm.cdf(abs(t))))}
