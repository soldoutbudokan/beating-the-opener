"""v4: v3 + totals/Asian-handicap market features + adaptive ensemble weight.

New features (all from the EARLY market only):
  p_over   - devigged P(over 2.5 goals): match-style signal, informs the draw
  ah_line  - Asian handicap size (market's margin expectation)
  p_ah_h   - devigged AH home price (finer favorite strength than 1X2)
  ou/ah missing flags

Ensemble weight w (stack vs gbmmove) is chosen per test season by minimizing
log loss on PRIOR seasons' own out-of-sample predictions - no leakage.
"""
import os

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from baselines import DIV_TIER
from odds_utils import OUTCOME_IDX, devig_shin, log_loss_vec

DATA = os.path.join(os.path.dirname(__file__), "..", "data", "features.pkl")
RESULTS = os.path.join(os.path.dirname(__file__), "..", "results")
TEST_SEASONS = [f"{y}-{(y + 1) % 100:02d}" for y in range(2017, 2026)]
MOM_ALPHA = 0.15

FUND = ["elo_diff", "elo_exp_h", "att_edge_h", "att_edge_a",
        "sot_edge_h", "sot_edge_a", "ew_stf_h", "ew_sta_h", "ew_stf_a", "ew_sta_a",
        "form_h", "form_a", "rest_h", "rest_a", "n_played_h", "n_played_a",
        "overround_ps"]


def softmax(z):
    z = z - z.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


def clustered_t(diff, dates):
    s = pd.Series(diff).groupby(pd.Series(dates).values).mean()
    return stats.ttest_1samp(s.to_numpy(), 0)


def main():
    os.makedirs(RESULTS, exist_ok=True)
    df = pd.read_pickle(DATA)
    df = df[df["has_ps_early"] & df["has_ps_close"]].copy()
    df["tier"] = df["Div"].map(DIV_TIER)
    df["y"] = df["FTR"].map(OUTCOME_IDX)
    df = df.sort_values(["Date", "Div", "HomeTeam"]).reset_index(drop=True)

    po = devig_shin(df[["PSH", "PSD", "PSA"]].to_numpy(float))
    pc = devig_shin(df[["PSCH", "PSCD", "PSCA"]].to_numpy(float))
    zo = np.log(po[:, [0, 2]] / po[:, [1]])
    zc = np.log(pc[:, [0, 2]] / pc[:, [1]])
    move = zc - zo

    # line-move momentum (strictly prior matches)
    mom, nmom = {}, {}
    mom_h = np.zeros(len(df)); mom_a = np.zeros(len(df))
    nm_h = np.zeros(len(df)); nm_a = np.zeros(len(df))
    homes = df["HomeTeam"].to_numpy(); aways = df["AwayTeam"].to_numpy()
    for i in range(len(df)):
        h, a = homes[i], aways[i]
        mom_h[i] = mom.get(h, 0.0); nm_h[i] = min(nmom.get(h, 0), 50)
        mom_a[i] = mom.get(a, 0.0); nm_a[i] = min(nmom.get(a, 0), 50)
        mom[h] = (1 - MOM_ALPHA) * mom.get(h, 0.0) + MOM_ALPHA * move[i, 0]
        mom[a] = (1 - MOM_ALPHA) * mom.get(a, 0.0) + MOM_ALPHA * move[i, 1]
        nmom[h] = nmom.get(h, 0) + 1
        nmom[a] = nmom.get(a, 0) + 1
    df["mom_h"], df["mom_a"] = mom_h, mom_a
    df["nm_h"], df["nm_a"] = nm_h, nm_a

    # cross-book disagreement
    for cols, tag in [(["B365H", "B365D", "B365A"], "dis"),
                      (["EAvgH", "EAvgD", "EAvgA"], "disavg")]:
        o = df[cols].to_numpy(float)
        ok = ~np.isnan(o).any(axis=1)
        z = np.zeros_like(zo)
        pz = devig_shin(o[ok])
        z[ok] = np.log(pz[:, [0, 2]] / pz[:, [1]]) - zo[ok]
        df[f"{tag}_h"], df[f"{tag}_a"] = z[:, 0], z[:, 1]
    df["dis_missing"] = df[["B365H", "B365D", "B365A"]].isna().any(axis=1).astype(float)

    # totals + Asian handicap (2-way devig; NaN -> imputed downstream)
    inv_ov, inv_un = 1 / df["EOv"], 1 / df["EUn"]
    df["p_over"] = inv_ov / (inv_ov + inv_un)
    df["ou_missing"] = df["p_over"].isna().astype(float)
    inv_ah_h, inv_ah_a = 1 / df["EAHH"], 1 / df["EAHA"]
    df["p_ah_h"] = inv_ah_h / (inv_ah_h + inv_ah_a)
    df["ah_line"] = df["EAHh"]
    df["ah_missing"] = df[["p_ah_h", "ah_line"]].isna().any(axis=1).astype(float)

    XCOLS = FUND + ["mom_h", "mom_a", "nm_h", "nm_a",
                    "dis_h", "dis_a", "dis_missing", "disavg_h", "disavg_a",
                    "p_over", "ou_missing", "p_ah_h", "ah_line", "ah_missing"]
    GBM_COLS = XCOLS + ["div_idx"]
    X = df[XCOLS].to_numpy(float)
    Xg = np.hstack([df[GBM_COLS].to_numpy(float), zo])
    cat_idx = [GBM_COLS.index("div_idx")]
    y = df["y"].to_numpy()

    preds = {m: np.full((len(df), 3), np.nan) for m in ["stack", "gbmmove", "ens"]}
    W_GRID = np.arange(0.0, 1.01, 0.1)
    w_used = {}

    for season in TEST_SEASONS:
        te = (df["season"] == season).to_numpy()
        tr = (df["season"] < season).to_numpy()
        if te.sum() == 0:
            continue

        imp = SimpleImputer(strategy="median").fit(X[tr])
        sc = StandardScaler().fit(imp.transform(X[tr]))
        Ftr = sc.transform(imp.transform(X[tr]))
        Fte = sc.transform(imp.transform(X[te]))

        lr = LogisticRegression(max_iter=2000, C=1.0)
        lr.fit(np.hstack([zo[tr], Ftr]), y[tr])
        preds["stack"][te] = lr.predict_proba(np.hstack([zo[te], Fte]))

        zhat = np.zeros((int(te.sum()), 2))
        for j in range(2):
            g = HistGradientBoostingRegressor(
                max_iter=300, learning_rate=0.05, min_samples_leaf=80,
                l2_regularization=1.0, max_leaf_nodes=31, early_stopping=False,
                categorical_features=cat_idx, random_state=7)
            g.fit(Xg[tr], move[tr, j])
            zhat[:, j] = zo[te, j] + g.predict(Xg[te])
        pm = softmax(np.hstack([zhat[:, [0]], np.zeros((len(zhat), 1)), zhat[:, [1]]]))
        preds["gbmmove"][te] = pm

        # adaptive ensemble weight from PRIOR seasons' OOS predictions
        prior = (df["season"] < season).to_numpy() & ~np.isnan(preds["stack"][:, 0])
        w = 0.5
        if prior.sum() > 3000:
            zs_p = np.log(np.clip(preds["stack"][prior], 1e-9, 1))
            zm_p = np.log(np.clip(preds["gbmmove"][prior], 1e-9, 1))
            lls = [log_loss_vec(softmax(wi * zs_p + (1 - wi) * zm_p), y[prior]).mean()
                   for wi in W_GRID]
            w = W_GRID[int(np.argmin(lls))]
        w_used[season] = w

        zs = np.log(np.clip(preds["stack"][te], 1e-9, 1))
        zm = np.log(np.clip(pm, 1e-9, 1))
        preds["ens"][te] = softmax(w * zs + (1 - w) * zm)
        print(f"{season}: n_train={tr.sum()}, n_test={te.sum()}, w_stack={w:.1f}",
              flush=True)

    mask = ~np.isnan(preds["ens"][:, 0])
    d = df[mask].copy()
    ym = y[mask]
    pom, pcm = po[mask], pc[mask]
    dates = d["Date"].to_numpy()

    ll = {"open": log_loss_vec(pom, ym), "close": log_loss_vec(pcm, ym)}
    for m in preds:
        ll[m] = log_loss_vec(preds[m][mask], ym)

    print(f"\n=== OOS {TEST_SEASONS[0]}..{TEST_SEASONS[-1]}: {len(d)} matches ===")
    print(f"{'model':9s} {'logloss':>9s} {'vs open':>9s} {'t':>7s} {'p':>10s} "
          f"{'t_clus':>7s} {'p_clus':>10s}")
    for m in ["open", "close", "stack", "gbmmove", "ens"]:
        diff = ll["open"] - ll[m]
        if m == "open":
            print(f"{m:9s} {ll[m].mean():9.5f}")
            continue
        t, p = stats.ttest_1samp(diff, 0)
        tc, pclus = clustered_t(diff, dates)
        print(f"{m:9s} {ll[m].mean():9.5f} {diff.mean():+9.5f} {t:7.2f} {p:10.2e} "
              f"{tc:7.2f} {pclus:10.2e}")

    best = min(["stack", "gbmmove", "ens"], key=lambda m: ll[m].mean())
    dbc = ll["close"] - ll[best]
    t, p = stats.ttest_1samp(dbc, 0)
    print(f"\nbest={best}; vs CLOSE: {dbc.mean():+.5f} (t={t:.2f}, p={p:.2e})")

    wedge = (ll["open"] - ll["close"]).mean()
    capt = (ll["open"] - ll[best]).mean()
    print(f"wedge={wedge:.5f}, captured={capt:.5f} ({capt / wedge:.0%})")

    d["_do"] = ll["open"] - ll[best]
    print(f"\n{best} vs open by season:")
    print(d.groupby("season")["_do"].agg(["mean", "size"]).round(5).to_string())
    print(f"\n{best} vs open by tier:")
    print(d.groupby("tier")["_do"].agg(["mean", "size"]).round(5).to_string())

    pb = preds[best][mask]
    print("\nbetting sim (flat 1u):")
    for cols, label in [(["PSH", "PSD", "PSA"], "PS early"),
                        (["EMaxH", "EMaxD", "EMaxA"], "best-book early")]:
        odds = d[cols].to_numpy(float)
        ev = pb * odds - 1
        for thr in (0.02, 0.05):
            sel = np.nan_to_num(ev, nan=-9) > thr
            n = int(sel.sum())
            if n == 0:
                continue
            i, j = np.where(sel)
            won = (ym[i] == j).astype(float)
            ret = won * odds[i, j] - 1
            clv = pcm[i, j] * odds[i, j] - 1
            tstat = ret.mean() / (ret.std() / np.sqrt(n))
            print(f"  [{label} EV>{thr:.0%}] bets={n} ROI={ret.mean():+.3%} "
                  f"(t={tstat:.2f}) CLV={clv.mean():+.3%}")

    for m in preds:
        d[[f"{m}_h", f"{m}_d", f"{m}_a"]] = preds[m][mask]
    d[["po_h", "po_d", "po_a"]] = pom
    d[["pc_h", "pc_d", "pc_a"]] = pcm
    d.to_pickle(os.path.join(RESULTS, "preds_v4.pkl"))
    print("\nsaved -> results/preds_v4.pkl")


if __name__ == "__main__":
    main()
