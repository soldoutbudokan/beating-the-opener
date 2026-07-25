"""Walk-forward training + evaluation vs the early and closing lines.

Test seasons 2017-18 .. 2025-26; for each, models train only on strictly
earlier seasons. Models:
  recal  - multinomial logistic on the early line's log-odds (pure recalibration)
  fund   - HistGB on fundamentals only (no odds features)
  blend  - HistGB on fundamentals + early-odds features
Benchmarks: shin-devigged Pinnacle early (p_open) and closing (p_close).
"""
import os

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression

from baselines import DIV_TIER
from odds_utils import OUTCOME_IDX, devig_shin, log_loss_vec

DATA = os.path.join(os.path.dirname(__file__), "..", "data", "features.pkl")
RESULTS = os.path.join(os.path.dirname(__file__), "..", "results")

FUND_COLS = ["elo_h", "elo_a", "elo_diff", "elo_exp_h",
             "ew_gf_h", "ew_ga_h", "ew_gf_a", "ew_ga_a", "att_edge_h", "att_edge_a",
             "form_h", "form_a", "n_played_h", "n_played_a", "rest_h", "rest_a", "div_idx"]
MKT_COLS = ["lo_open_h", "lo_open_d", "lo_open_a", "overround_ps", "b365_ps_dis"]
TEST_SEASONS = [f"{y}-{(y + 1) % 100:02d}" for y in range(2017, 2026)]


def gbm(cat_idx):
    return HistGradientBoostingClassifier(
        max_iter=400, learning_rate=0.06, min_samples_leaf=60,
        l2_regularization=1.0, max_leaf_nodes=31, early_stopping=False,
        categorical_features=cat_idx, random_state=7)


def main():
    os.makedirs(RESULTS, exist_ok=True)
    df = pd.read_pickle(DATA)
    df = df[df["has_ps_early"] & df["has_ps_close"]].copy()
    df["tier"] = df["Div"].map(DIV_TIER)
    df["y"] = df["FTR"].map(OUTCOME_IDX)
    df = df.reset_index(drop=True)

    p_open = devig_shin(df[["PSH", "PSD", "PSA"]].to_numpy(float))
    p_close = devig_shin(df[["PSCH", "PSCD", "PSCA"]].to_numpy(float))
    df[["po_h", "po_d", "po_a"]] = p_open
    df[["pc_h", "pc_d", "pc_a"]] = p_close

    preds = {m: np.full((len(df), 3), np.nan) for m in ["recal", "fund", "blend"]}

    for season in TEST_SEASONS:
        te = df["season"] == season
        if te.sum() == 0:
            continue
        tr = df["season"] < season
        ytr = df.loc[tr, "y"].to_numpy()

        # recalibration of the opener
        Xtr = df.loc[tr, ["lo_open_h", "lo_open_d", "lo_open_a"]].to_numpy()
        Xte = df.loc[te, ["lo_open_h", "lo_open_d", "lo_open_a"]].to_numpy()
        lr = LogisticRegression(max_iter=1000, C=10.0)
        lr.fit(Xtr, ytr)
        preds["recal"][te.to_numpy()] = lr.predict_proba(Xte)

        # fundamentals only
        m = gbm([FUND_COLS.index("div_idx")])
        m.fit(df.loc[tr, FUND_COLS].to_numpy(), ytr)
        preds["fund"][te.to_numpy()] = m.predict_proba(df.loc[te, FUND_COLS].to_numpy())

        # blend
        cols = FUND_COLS + MKT_COLS
        m = gbm([cols.index("div_idx")])
        m.fit(df.loc[tr, cols].to_numpy(), ytr)
        preds["blend"][te.to_numpy()] = m.predict_proba(df.loc[te, cols].to_numpy())
        print(f"{season}: trained on {tr.sum()}, predicted {te.sum()}")

    mask = ~np.isnan(preds["blend"][:, 0])
    d = df[mask].copy()
    y = d["y"].to_numpy()
    po = d[["po_h", "po_d", "po_a"]].to_numpy()
    pc = d[["pc_h", "pc_d", "pc_a"]].to_numpy()

    ll = {"open": log_loss_vec(po, y), "close": log_loss_vec(pc, y)}
    for mname in preds:
        ll[mname] = log_loss_vec(preds[mname][mask], y)

    print(f"\n=== Out-of-sample {TEST_SEASONS[0]}..{TEST_SEASONS[-1]}: {len(d)} matches ===")
    print(f"{'model':8s} {'logloss':>9s} {'vs open':>9s} {'t':>7s} {'p':>10s}")
    for mname in ["open", "close", "recal", "fund", "blend"]:
        diff = ll["open"] - ll[mname]
        t, p = (np.nan, np.nan) if mname == "open" else stats.ttest_1samp(diff, 0)
        print(f"{mname:8s} {ll[mname].mean():9.5f} {diff.mean():+9.5f} {t:7.2f} {p:10.2e}")

    diff_bc = ll["close"] - ll["blend"]
    t, p = stats.ttest_1samp(diff_bc, 0)
    print(f"\nblend vs CLOSE: diff {diff_bc.mean():+.5f} (t={t:.2f}, p={p:.2e})"
          f"  -> {'blend better' if diff_bc.mean() > 0 else 'close better'}")

    # per-season consistency, blend vs open
    d["_do"] = ll["open"] - ll["blend"]
    per_season = d.groupby("season")["_do"].agg(["mean", "size"])
    print("\nblend improvement over open by season (positive = blend better):")
    print(per_season.round(5).to_string())

    d["_dc"] = ll["close"] - ll["blend"]
    print("\nby tier: blend vs open / blend vs close (positive = blend better):")
    per_tier = d.groupby("tier").agg(n=("_do", "size"), vs_open=("_do", "mean"),
                                     vs_close=("_dc", "mean"))
    print(per_tier.round(5).to_string())

    # ---- betting simulation at early prices ----
    pb = preds["blend"][mask]
    for price_cols, label in [(["PSH", "PSD", "PSA"], "Pinnacle early"),
                              (["EMaxH", "EMaxD", "EMaxA"], "best-of-book early")]:
        odds = d[price_cols].to_numpy(float)
        ev = pb * odds - 1
        for thr in (0.02, 0.05):
            sel = np.nan_to_num(ev, nan=-9) > thr
            n = int(sel.sum())
            if n == 0:
                continue
            i, j = np.where(sel)
            won = (y[i] == j).astype(float)
            ret = won * odds[i, j] - 1
            clv = pc[i, j] * odds[i, j] - 1
            print(f"\n[{label} @ EV>{thr:.0%}] bets={n} "
                  f"ROI={ret.mean():+.3%} (t={ret.mean()/ (ret.std()/np.sqrt(n)):.2f}) "
                  f"CLV={clv.mean():+.3%}")

    d_out = d[["Date", "season", "Div", "tier", "HomeTeam", "AwayTeam", "y",
               "po_h", "po_d", "po_a", "pc_h", "pc_d", "pc_a",
               "PSH", "PSD", "PSA", "EMaxH", "EMaxD", "EMaxA"]].copy()
    for mname in preds:
        d_out[[f"{mname}_h", f"{mname}_d", f"{mname}_a"]] = preds[mname][mask]
    d_out.to_pickle(os.path.join(RESULTS, "preds.pkl"))
    print(f"\nsaved per-match predictions -> results/preds.pkl")


if __name__ == "__main__":
    main()
