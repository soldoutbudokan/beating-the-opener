"""Post-Pinnacle re-derivation: the LIVE model replayed over all seasons on
the market-AVERAGE anchor and average close (AUDIT H6/N3).

Pinnacle left football-data mid-Jan 2026, so the live pipeline now anchors on
the average book (overround ~6.8% vs ~3.1%) and grades CLV against the
average close - a regime the original backtest (Pinnacle anchor and close)
never measured. This script forces that regime across history - identical
model, features and ensemble weight to live_pipeline - and re-derives the
expectations. Every cell is also scored for the zero-skill PLACEBO that bets
the anchor's own devigged probabilities (AUDIT H4): CLV that the placebo
harvests too is price-shopping against the best-of-book envelope, not model
skill.

Run: python3 src/train_eval_avg.py    (writes results/avg_anchor.pkl)
"""
import os
import sys

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, os.path.dirname(__file__))
from live_pipeline import (build_rows, anchor_probs, softmax, XCOLS,
                           GBM_COLS, ENS_W_STACK)  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
TEST_SEASONS = [f"{y}-{(y + 1) % 100:02d}" for y in range(2017, 2026)]
AVG = ["EAvgH", "EAvgD", "EAvgA"]
AVGC = ["AvgCH", "AvgCD", "AvgCA"]


def sim(df, probs, odds_cols, pc, thresh):
    recs = []
    od = df[odds_cols].to_numpy(float)
    won = np.stack([(df.FTR == s).to_numpy() for s in "HDA"], axis=1)
    for j in range(3):
        sel = (probs[:, j] * od[:, j] - 1 > thresh) & np.isfinite(od[:, j])
        recs.append(pd.DataFrame({
            "pnl": np.where(won[sel, j], od[sel, j] - 1, -1.0),
            "clv": pc[sel, j] * od[sel, j] - 1,
            "date": df.Date.to_numpy()[sel],
            "season": df.season.to_numpy()[sel]}))
    return pd.concat(recs, ignore_index=True)


def rep(tag, b):
    if len(b) < 20:
        print(f"  {tag}: only {len(b)} bets")
        return
    bd = b.groupby("date")["clv"].mean()
    tc = bd.mean() / (bd.std() / np.sqrt(len(bd)))
    br = b.groupby("date")["pnl"].mean()
    tr = br.mean() / (br.std() / np.sqrt(len(br)))
    seas = b.groupby("season")["clv"].mean()
    print(f"  {tag}: n={len(b)}  ROI {b.pnl.mean():+.2%} (date-t {tr:.1f})  "
          f"CLV {b.clv.mean():+.2%} (date-t {tc:.1f})  "
          f"CLV+ seasons {int((seas > 0).sum())}/{len(seas)}")


def main():
    df = pd.read_pickle(os.path.join(ROOT, "data", "matches.pkl"))
    df = df.sort_values(["Date", "Div", "HomeTeam"]).reset_index(drop=True)
    print(f"{len(df)} matches; forcing avg-book anchor + avg close everywhere")
    feat, zo, p_anchor = build_rows(df, np.zeros(len(df), bool),
                                    anchor_cols=tuple(AVG),
                                    close_cols=tuple(AVGC))
    p_close, over_c, _ = anchor_probs(df, AVGC, AVGC)
    ok = (~np.isnan(zo).any(axis=1)) & (~np.isnan(p_close).any(axis=1)) \
        & (df.Date >= "2012-07-01").to_numpy()
    over_a = feat["overround_anchor"].to_numpy()
    print(f"eligible rows 2012+: {ok.sum()}; anchor overround mean "
          f"{np.nanmean(over_a[ok]):.4f} (live regime ~0.068)")

    y = df.FTR.map({"H": 0, "D": 1, "A": 2}).to_numpy(float)
    X = feat[XCOLS].to_numpy(float)
    Xg = np.hstack([feat[GBM_COLS].to_numpy(float), np.nan_to_num(zo)])
    cat_idx = [GBM_COLS.index("div_idx")]
    zc_all = np.full_like(zo, np.nan)
    okc = ~np.isnan(p_close).any(axis=1)
    zc_all[okc] = np.log(p_close[okc][:, [0, 2]] / p_close[okc][:, [1]])

    p_ens = np.full((len(df), 3), np.nan)
    for s in TEST_SEASONS:
        tr = ok & (df.season < s).to_numpy()
        te = ok & (df.season == s).to_numpy()
        if tr.sum() < 5000 or te.sum() == 0:
            continue
        imp = SimpleImputer(strategy="median").fit(X[tr])
        sc = StandardScaler().fit(imp.transform(X[tr]))
        lr = LogisticRegression(max_iter=2000, C=1.0)
        lr.fit(np.hstack([zo[tr], sc.transform(imp.transform(X[tr]))]),
               y[tr].astype(int))
        p_stack = lr.predict_proba(
            np.hstack([zo[te], sc.transform(imp.transform(X[te]))]))
        zhat = np.zeros((int(te.sum()), 2))
        for j in range(2):
            g = HistGradientBoostingRegressor(
                max_iter=300, learning_rate=0.05, min_samples_leaf=80,
                l2_regularization=1.0, max_leaf_nodes=31, early_stopping=False,
                categorical_features=cat_idx, random_state=7)
            g.fit(Xg[tr], zc_all[tr][:, j] - zo[tr][:, j])
            zhat[:, j] = zo[te][:, j] + g.predict(Xg[te])
        p_move = softmax(np.hstack([zhat[:, [0]], np.zeros((len(zhat), 1)),
                                    zhat[:, [1]]]))
        p_ens[te] = softmax(ENS_W_STACK * np.log(np.clip(p_stack, 1e-9, 1))
                            + (1 - ENS_W_STACK) * np.log(np.clip(p_move, 1e-9, 1)))
        print(f"  {s}: trained {tr.sum()}, scored {te.sum()}", flush=True)

    ev = ~np.isnan(p_ens).any(axis=1)
    d = df[ev].copy()
    pm, pa, pc = p_ens[ev], p_anchor[ev], p_close[ev]
    ll = lambda p: -np.log(np.clip(p[np.arange(len(d)), d.FTR.map(
        {"H": 0, "D": 1, "A": 2}).to_numpy()], 1e-9, 1))
    ll_m, ll_a, ll_c = ll(pm), ll(pa), ll(pc)
    dma = ll_a - ll_m
    byd = pd.Series(dma).groupby(d.Date.values).mean()
    t, p = stats.ttest_1samp(byd.to_numpy(), 0)
    print(f"\nLL: anchor {ll_a.mean():.5f}  model {ll_m.mean():.5f}  "
          f"close {ll_c.mean():.5f}")
    print(f"model vs avg anchor: {dma.mean():+.5f} "
          f"(date-clustered t={t:.2f}, p={p:.2g})")

    print("\nsim at BEST-of-book (EMax) prices, CLV vs devigged avg close:")
    for th in (0.02, 0.05):
        rep(f"model   EV>{th:.0%}", sim(d, pm, ["EMaxH", "EMaxD", "EMaxA"], pc, th))
        rep(f"placebo EV>{th:.0%}", sim(d, pa, ["EMaxH", "EMaxD", "EMaxA"], pc, th))
    print("sim at AVERAGE-book prices (what a soft book quotes):")
    for th in (0.01, 0.02):
        rep(f"model   EV>{th:.0%}", sim(d, pm, AVG, pc, th))
        rep(f"placebo EV>{th:.0%}", sim(d, pa, AVG, pc, th))

    out = d[["Date", "season", "Div", "HomeTeam", "AwayTeam", "FTR"]].copy()
    for i, s in enumerate("hda"):
        out[f"pm_{s}"], out[f"pa_{s}"], out[f"pc_{s}"] = pm[:, i], pa[:, i], pc[:, i]
    out.to_pickle(os.path.join(ROOT, "results", "avg_anchor.pkl"))
    print(f"\nwrote results/avg_anchor.pkl ({len(out)} rows)")


if __name__ == "__main__":
    main()
