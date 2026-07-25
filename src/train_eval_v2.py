"""v2: anchor on the opener instead of re-learning it.

v1's GBM lost to the opener (-0.022 LL): trees bucket the odds features and
destroy the market's precision. v2 keeps the opener's logits intact:

  stack    - multinomial logistic on [opener logits + fundamentals + cross-book
             disagreement]; nests "use the opener" as a special case.
  movepred - ridge regression predicting the CLOSING logits from opening-time
             info (the close is the provably better price; the line move is
             the target, outcomes never enter training).
  ens      - 50/50 logit-space blend of the two.

Walk-forward by season as in v1.
"""
import os

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.preprocessing import StandardScaler

from baselines import DIV_TIER
from odds_utils import OUTCOME_IDX, devig_shin, log_loss_vec

DATA = os.path.join(os.path.dirname(__file__), "..", "data", "features.pkl")
RESULTS = os.path.join(os.path.dirname(__file__), "..", "results")
TEST_SEASONS = [f"{y}-{(y + 1) % 100:02d}" for y in range(2017, 2026)]

FUND = ["elo_diff", "elo_exp_h", "att_edge_h", "att_edge_a",
        "form_h", "form_a", "rest_h", "rest_a", "n_played_h", "n_played_a",
        "overround_ps"]


def softmax(z):
    z = z - z.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


def main():
    os.makedirs(RESULTS, exist_ok=True)
    df = pd.read_pickle(DATA)
    df = df[df["has_ps_early"] & df["has_ps_close"]].copy()
    df["tier"] = df["Div"].map(DIV_TIER)
    df["y"] = df["FTR"].map(OUTCOME_IDX)
    df = df.reset_index(drop=True)

    po = devig_shin(df[["PSH", "PSD", "PSA"]].to_numpy(float))
    pc = devig_shin(df[["PSCH", "PSCD", "PSCA"]].to_numpy(float))

    # multinomial logits with draw as base category
    zo = np.log(po[:, [0, 2]] / po[:, [1]])          # (n,2): H,A vs D
    zc = np.log(pc[:, [0, 2]] / pc[:, [1]])

    # cross-book disagreement: B365 early vs PS early, all outcomes (0 if missing)
    b365 = df[["B365H", "B365D", "B365A"]].to_numpy(float)
    okb = ~np.isnan(b365).any(axis=1)
    zb = np.zeros_like(zo)
    zb[okb] = np.log(devig_shin(b365[okb])[:, [0, 2]] /
                     devig_shin(b365[okb])[:, [1]]) - zo[okb]
    df["dis_h"], df["dis_a"] = zb[:, 0], zb[:, 1]
    df["dis_missing"] = (~okb).astype(float)

    # market average vs pinnacle early (0 if missing)
    eavg = df[["EAvgH", "EAvgD", "EAvgA"]].to_numpy(float)
    oka = ~np.isnan(eavg).any(axis=1)
    za = np.zeros_like(zo)
    za[oka] = np.log(devig_shin(eavg[oka])[:, [0, 2]] /
                     devig_shin(eavg[oka])[:, [1]]) - zo[oka]
    df["disavg_h"], df["disavg_a"] = za[:, 0], za[:, 1]

    XCOLS = FUND + ["dis_h", "dis_a", "dis_missing", "disavg_h", "disavg_a"]
    X_fund = df[XCOLS].to_numpy(float)
    y = df["y"].to_numpy()

    preds = {m: np.full((len(df), 3), np.nan) for m in ["stack", "movepred", "ens"]}

    for season in TEST_SEASONS:
        te = (df["season"] == season).to_numpy()
        tr = (df["season"] < season).to_numpy()
        if te.sum() == 0:
            continue

        sc = StandardScaler().fit(X_fund[tr])
        Ftr, Fte = sc.transform(X_fund[tr]), sc.transform(X_fund[te])

        # ---- stack: logistic on opener logits + fundamentals ----
        Str = np.hstack([zo[tr], Ftr])
        Ste = np.hstack([zo[te], Fte])
        lr = LogisticRegression(max_iter=2000, C=1.0)
        lr.fit(Str, y[tr])
        preds["stack"][te] = lr.predict_proba(Ste)

        # ---- movepred: ridge on closing logits (outcomes unused) ----
        zhat = np.zeros((int(te.sum()), 2))
        for j in range(2):
            rg = Ridge(alpha=10.0)
            rg.fit(Str, zc[tr, j])
            zhat[:, j] = rg.predict(Ste)
        pm = softmax(np.hstack([zhat[:, [0]], np.zeros((len(zhat), 1)), zhat[:, [1]]]))
        preds["movepred"][te] = pm

        # ---- ensemble in logit space ----
        zs = np.log(np.clip(preds["stack"][te], 1e-9, 1))
        zm = np.log(np.clip(pm, 1e-9, 1))
        preds["ens"][te] = softmax(0.5 * zs + 0.5 * zm)
        print(f"{season}: n_train={tr.sum()}, n_test={te.sum()}")

    mask = ~np.isnan(preds["ens"][:, 0])
    d = df[mask].copy()
    ym = y[mask]
    pom, pcm = po[mask], pc[mask]

    ll = {"open": log_loss_vec(pom, ym), "close": log_loss_vec(pcm, ym)}
    for m in preds:
        ll[m] = log_loss_vec(preds[m][mask], ym)

    print(f"\n=== OOS {TEST_SEASONS[0]}..{TEST_SEASONS[-1]}: {len(d)} matches ===")
    print(f"{'model':9s} {'logloss':>9s} {'vs open':>9s} {'t':>7s} {'p':>10s}")
    for m in ["open", "close", "stack", "movepred", "ens"]:
        diff = ll["open"] - ll[m]
        if m == "open":
            print(f"{m:9s} {ll[m].mean():9.5f}")
            continue
        t, p = stats.ttest_1samp(diff, 0)
        print(f"{m:9s} {ll[m].mean():9.5f} {diff.mean():+9.5f} {t:7.2f} {p:10.2e}")

    best = min(["stack", "movepred", "ens"], key=lambda m: ll[m].mean())
    print(f"\nbest model: {best}")
    dbc = ll["close"] - ll[best]
    t, p = stats.ttest_1samp(dbc, 0)
    print(f"{best} vs CLOSE: {dbc.mean():+.5f} (t={t:.2f}, p={p:.2e}) -> "
          f"{'model better' if dbc.mean() > 0 else 'close better'}")

    d["_do"] = ll["open"] - ll[best]
    print(f"\n{best} vs open by season:")
    print(d.groupby("season")["_do"].agg(["mean", "size"]).round(5).to_string())
    print(f"\n{best} vs open by tier:")
    print(d.groupby("tier")["_do"].agg(["mean", "size"]).round(5).to_string())

    # how much of the open->close wedge does the model capture?
    wedge = (ll["open"] - ll["close"]).mean()
    capt = (ll["open"] - ll[best]).mean()
    print(f"\nwedge (open-close) = {wedge:.5f}; model captures {capt:.5f} "
          f"({capt / wedge:.0%})")

    # ---- betting sim ----
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
    d.to_pickle(os.path.join(RESULTS, "preds_v2.pkl"))
    print("\nsaved -> results/preds_v2.pkl")


if __name__ == "__main__":
    main()
