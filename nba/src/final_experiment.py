"""Final benchmark on the clean (v4) feature set.

  TIER A (strict) : no information from tonight's game whatsoever.
  TIER B (pre-tip): + which established rotation players are dressed tonight,
                    which is public before tip and priced into the closing line.

Seasons <=2018 train; 2019-2023 walk-forward validation (stack + blend weights);
2024-2026 held out and scored once.
"""
import os
import sys

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model as M  # noqa: E402
from market import log_loss_vec  # noqa: E402
from run_experiment import PRED_SEASONS, TEST, VAL, logit, wf_margin, wf_proba  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Features that read tonight's dressed-rotation information.
SAME_GAME = [
    "home_rot_avail_talent", "away_rot_avail_talent",
    "home_rot_missing_talent", "away_rot_missing_talent",
    "home_rot_missing_star", "away_rot_missing_star",
    "home_rot_n_missing", "away_rot_n_missing",
    "home_rot_size", "away_rot_size", "home_rot_share", "away_rot_share",
    "home_rot_top_avail", "away_rot_top_avail",
    "home_rot_avail_min", "away_rot_avail_min",
    "home_rot_missing_min", "away_rot_missing_min",
    "rot_avail_diff", "rot_missing_diff", "rot_star_diff", "rot_share_diff",
    "rot_missing_min_diff", "rot_missing_x_srs",
    "rapm2_home", "rapm2_away", "rapm2_diff", "rapm2_hfa", "rapm2_pred_margin",
    "srs_plus_rapm2", "rapm2_x_rest",
]

BASE_MODELS = {
    "logit": lambda: M.mk_logit(C=0.03),
    "logit_wide": lambda: M.mk_logit(C=0.3),
    "gbm": lambda: M.mk_gbm(),
    "gbm_deep": lambda: M.mk_gbm(max_leaf_nodes=31, learning_rate=0.02,
                                 min_samples_leaf=60),
    "gbm_shallow": lambda: M.mk_gbm(max_leaf_nodes=7, learning_rate=0.04,
                                    min_samples_leaf=150),
}


def run_tier(df, cols, tag):
    names = []
    for name, f in BASE_MODELS.items():
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
                       LogisticRegression(C=1.0, max_iter=3000))
    st.fit(logit(val[names].values), val.home_win.values)
    scol = f"{tag}_stack"
    df[scol] = np.nan
    df.loc[ok, scol] = st.predict_proba(logit(df.loc[ok, names].values))[:, 1]

    bm = ok & df.mkt_mult.notna()
    vb = df[df.season_year.isin(VAL) & bm]
    bl = make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=3000))
    bl.fit(np.column_stack([logit(vb.mkt_mult.values), logit(vb[scol].values)]),
           vb.home_win.values)
    bcol = f"{tag}_blend"
    df[bcol] = np.nan
    df.loc[bm, bcol] = bl.predict_proba(np.column_stack(
        [logit(df.loc[bm, "mkt_mult"].values), logit(df.loc[bm, scol].values)]))[:, 1]
    return scol, bcol, bl.named_steps["logisticregression"].coef_[0]


def main():
    df = pd.read_csv(os.path.join(ROOT, "data", "raw", "dataset_v4.csv"))
    df = df[df.season_type.isin([2, 3, 5])].copy().reset_index(drop=True)

    all_cols = M.blind_features(df)
    strict = [c for c in all_cols if c not in SAME_GAME]
    print(f"Tier A: {len(strict)} features | Tier B: {len(all_cols)} features")

    a_s, a_b, a_c = run_tier(df, strict, "A")
    print(f"Tier A blend coef: market={a_c[0]:+.3f} model={a_c[1]:+.3f}")
    b_s, b_b, b_c = run_tier(df, all_cols, "B")
    print(f"Tier B blend coef: market={b_c[0]:+.3f} model={b_c[1]:+.3f}")

    df.to_csv(os.path.join(ROOT, "data", "raw", "preds_final_v4.csv"), index=False)

    need = [a_s, a_b, b_s, b_b]
    t = df[df.season_year.isin(TEST) & df.mkt_mult.notna()
           & df[need].notna().all(axis=1)]
    y = t.home_win.values
    labels = [(a_s, "Tier A stack (strict)"), (b_s, "Tier B stack (pre-tip)"),
              (a_b, "Tier A + market blend"), (b_b, "Tier B + market blend")]
    rows = [M.evaluate(y, t.mkt_mult.values, "MARKET closing line")]
    for c, lab in labels:
        rows.append(M.evaluate(y, t[c].values, lab))
    r = pd.DataFrame(rows)
    r["vs_mkt"] = r.logloss - r.logloss[0]
    pd.set_option("display.width", 220)
    print(f"\n=== HELD-OUT 2023-24 / 2024-25 / 2025-26 (n={len(t)}) ===")
    print(r.round(5).to_string(index=False))

    print("\n=== Paired bootstrap vs closing line (10k) ===")
    for c, lab in labels:
        s = M.paired_test(y, t[c].values, t.mkt_mult.values)
        flag = "BEATS" if s["ci_hi"] < 0 else ("loses" if s["ci_lo"] > 0 else "tied")
        print(f"  {lab:26s} dLL={s['mean_diff']:+.5f} "
              f"CI[{s['ci_lo']:+.5f},{s['ci_hi']:+.5f}] p={s['p_two_sided']:.4f}  [{flag}]")

    print("\n=== Per-season log loss ===")
    per = []
    for s in TEST:
        ss = t[t.season_year == s]
        row = {"season": s, "n": len(ss),
               "market": float(log_loss_vec(ss.home_win.values, ss.mkt_mult.values).mean())}
        for c, lab in labels:
            row[lab.replace("Tier ", "T").replace(" + market blend", "+mkt")] = float(
                log_loss_vec(ss.home_win.values, ss[c].values).mean())
        per.append(row)
    print(pd.DataFrame(per).round(5).to_string(index=False))
    return df


if __name__ == "__main__":
    main()
