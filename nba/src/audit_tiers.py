"""Information-tier audit.

The availability and RAPM features read tonight's box score to learn WHICH players
dressed. That is public roughly 30 minutes before tip and is therefore priced into
the closing line -- but it is still same-game information, so it deserves to be
isolated rather than buried in a headline number.

  TIER A (strict)  : nothing from tonight's game. Prior results, ratings, schedule.
  TIER B (pre-tip) : Tier A + who is dressed tonight (availability, RAPM lineup).

Tier A is the conservative, unimpeachable model. Tier B is the fair like-for-like
comparison against a closing line that also knows the inactive list.
"""
import os
import sys

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model as M  # noqa: E402
from run_experiment import PRED_SEASONS, TEST, VAL, logit, wf_margin, wf_proba  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Features that read tonight's active list.
SAME_GAME = [
    "home_avail_talent", "away_avail_talent", "home_missing_talent",
    "away_missing_talent", "home_missing_star", "away_missing_star",
    "home_n_missing", "away_n_missing", "home_n_active", "away_n_active",
    "home_top3_talent", "away_top3_talent", "home_talent_share",
    "away_talent_share", "avail_talent_diff", "missing_talent_diff",
    "missing_star_diff", "talent_share_diff", "top3_talent_diff",
    "rapm_home", "rapm_away", "rapm_diff", "rapm_pred_margin", "rapm_hfa",
    "rapm_x_rest", "srs_plus_rapm",
]


def run_tier(df, cols, tag):
    models = {
        "logit": lambda: M.mk_logit(C=0.03),
        "logit_wide": lambda: M.mk_logit(C=0.3),
        "gbm": lambda: M.mk_gbm(),
        "gbm_deep": lambda: M.mk_gbm(max_leaf_nodes=31, learning_rate=0.02,
                                     min_samples_leaf=60),
        "gbm_shallow": lambda: M.mk_gbm(max_leaf_nodes=7, learning_rate=0.04,
                                        min_samples_leaf=150),
    }
    names = []
    for name, f in models.items():
        c = f"{tag}_{name}"
        df[c] = wf_proba(df, cols, f, PRED_SEASONS)
        names.append(c)
    for name, f in (("marg_gbm", M.mk_gbm_reg), ("marg_ridge", M.mk_ridge)):
        c = f"{tag}_{name}"
        df[c] = wf_margin(df, cols, f, PRED_SEASONS)
        names.append(c)

    ok = df[names].notna().all(axis=1)
    val = df[df.season_year.isin(VAL) & ok]
    st = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                       LogisticRegression(C=1.0, max_iter=2000))
    st.fit(logit(val[names].values), val.home_win.values)
    col = f"{tag}_stack"
    df[col] = np.nan
    df.loc[ok, col] = st.predict_proba(logit(df.loc[ok, names].values))[:, 1]

    # market blend, weights fit on validation seasons only
    bm = ok & df.mkt_mult.notna()
    vb = df[df.season_year.isin(VAL) & bm]
    bl = make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=2000))
    bl.fit(np.column_stack([logit(vb.mkt_mult.values), logit(vb[col].values)]),
           vb.home_win.values)
    bcol = f"{tag}_blend"
    df[bcol] = np.nan
    df.loc[bm, bcol] = bl.predict_proba(np.column_stack(
        [logit(df.loc[bm, "mkt_mult"].values), logit(df.loc[bm, col].values)]))[:, 1]
    coef = bl.named_steps["logisticregression"].coef_[0]
    return col, bcol, coef


def main():
    df = pd.read_csv(os.path.join(ROOT, "data", "raw", "dataset_v3.csv"))
    df = df[df.season_type.isin([2, 3, 5])].copy().reset_index(drop=True)

    all_cols = M.blind_features(df)
    strict_cols = [c for c in all_cols if c not in SAME_GAME]
    print(f"Tier A (strict)  : {len(strict_cols)} features")
    print(f"Tier B (pre-tip) : {len(all_cols)} features\n")

    a_stack, a_blend, a_coef = run_tier(df, strict_cols, "A")
    print(f"Tier A blend coef: market={a_coef[0]:.3f} model={a_coef[1]:.3f}")
    b_stack, b_blend, b_coef = run_tier(df, all_cols, "B")
    print(f"Tier B blend coef: market={b_coef[0]:.3f} model={b_coef[1]:.3f}")

    df.to_csv(os.path.join(ROOT, "data", "raw", "preds_tiers.csv"), index=False)

    need = [a_stack, a_blend, b_stack, b_blend]
    t = df[df.season_year.isin(TEST) & df.mkt_mult.notna()
           & df[need].notna().all(axis=1)]
    y = t.home_win.values
    rows = [M.evaluate(y, t.mkt_mult.values, "MARKET closing line")]
    labels = [(a_stack, "Tier A stack (strict, blind)"),
              (b_stack, "Tier B stack (pre-tip, blind)"),
              (a_blend, "Tier A + market blend"),
              (b_blend, "Tier B + market blend")]
    for c, lab in labels:
        rows.append(M.evaluate(y, t[c].values, lab))
    r = pd.DataFrame(rows)
    r["vs_mkt"] = r.logloss - r.logloss[0]
    pd.set_option("display.width", 220)
    print(f"\n=== HELD-OUT (n={len(t)}) ===")
    print(r.round(5).to_string(index=False))

    print("\n=== Paired bootstrap vs closing line ===")
    for c, lab in labels:
        s = M.paired_test(y, t[c].values, t.mkt_mult.values)
        star = "***" if s["p_two_sided"] < 0.01 else ("**" if s["p_two_sided"] < 0.05 else "")
        print(f"  {lab:32s} dLL={s['mean_diff']:+.5f} "
              f"CI[{s['ci_lo']:+.5f},{s['ci_hi']:+.5f}] p={s['p_two_sided']:.4f} {star}")

    print("\n=== Per-season (log loss) ===")
    per = []
    for s in TEST:
        ss = t[t.season_year == s]
        row = {"season": s, "n": len(ss), "market": log_loss(ss, "mkt_mult")}
        for c, lab in labels:
            row[lab.split(" (")[0].replace("Tier ", "T")] = log_loss(ss, c)
        per.append(row)
    print(pd.DataFrame(per).round(5).to_string(index=False))


def log_loss(d, c):
    from market import log_loss_vec
    return float(log_loss_vec(d.home_win.values, d[c].values).mean())


if __name__ == "__main__":
    main()
