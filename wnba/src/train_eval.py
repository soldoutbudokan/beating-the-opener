"""Walk-forward: can (open anchor + box-score features) beat the CLOSE?

Model: HistGradientBoosting on the standardized residual (actual - mu_open)/sigma,
anchored on the open's implied mean. Trained only on props from strictly earlier
dates; retrained weekly on an expanding window.

Scoreboard:
  1. log loss of P(over close line) - model vs devigged close (paired, clustered)
  2. MAE of implied mean vs actual - open / close / model
  3. bet sim at OPEN prices: EV filter, ROI + CLV vs close
"""
import os

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.ensemble import HistGradientBoostingRegressor

from odds_utils import amer_to_dec, ll_binary
from dist_utils import p_over, sigma, POISSON
from build_modelset import PANEL_FEATS  # single source - live imports it too

ROOT = os.path.join(os.path.dirname(__file__), "..")

MARKETS = ["points", "rebounds", "assists", "threes", "pra",
           "pts_ast", "pts_reb", "reb_ast"]
RETRAIN_DAYS = 7
MIN_TRAIN = 400
BURN_IN_DAYS = 24


def sd(market_arr, mu_arr):
    """Per-row residual scale: sigma for Normal markets, sqrt(mu) for Poisson."""
    out = np.empty(len(mu_arr))
    for mkt in np.unique(market_arr):
        m = market_arr == mkt
        out[m] = (np.sqrt(np.maximum(mu_arr[m], 0.3)) if mkt in POISSON
                  else sigma(mkt, mu_arr[m]))
    return out


def prepare():
    ms = pd.read_pickle(os.path.join(ROOT, "data", "modelset.pkl"))
    ms = ms[ms.market.isin(MARKETS) & ms.actual.notna() & ~ms.void
            & ms.p_close.notna() & ms.mu_open.notna()].copy()
    # only coherent two-way quotes at BOTH ends: a mispaired open or close
    # (different book/line for over vs under, or insane booksum) fabricates
    # the implied mean it anchors on (AUDIT C1/H2/N4)
    if "open_coherent" in ms.columns:
        ms = ms[ms.open_coherent.fillna(False)
                & ms.coh_close.fillna(False)].copy()
    ms["mkt_i"] = ms.market.map({m: i for i, m in enumerate(MARKETS)})
    ms["open_juice"] = ms.p_open - 0.5
    ms["scale"] = sd(ms.market.values, ms.mu_open.values)
    ms["y"] = (ms.actual - ms.mu_open) / ms.scale
    ms = ms.sort_values("date").reset_index(drop=True)
    return ms


def features(ms):
    X = ms[PANEL_FEATS].copy()
    X["mkt_i"] = ms.mkt_i
    X["mu_open"] = ms.mu_open
    X["open_line"] = ms.open_line
    X["open_juice"] = ms.open_juice
    X["open_book"] = ms.open_book
    return X.to_numpy(float)


def walk_forward(ms):
    dates = sorted(ms.date.unique())
    start = pd.Timestamp(dates[0]) + pd.Timedelta(days=BURN_IN_DAYS)
    pred = np.full(len(ms), np.nan)
    X_all = features(ms)
    y = ms.y.to_numpy()
    date_ts = pd.to_datetime(ms.date)

    eval_dates = [d for d in dates if pd.Timestamp(d) >= start]
    block_starts = eval_dates[::RETRAIN_DAYS]
    for i, d0 in enumerate(block_starts):
        d1 = block_starts[i + 1] if i + 1 < len(block_starts) else "9999"
        tr = (date_ts < pd.Timestamp(d0)).to_numpy()
        te = ((ms.date >= d0) & (ms.date < d1)).to_numpy()
        if tr.sum() < MIN_TRAIN or te.sum() == 0:
            continue
        model = HistGradientBoostingRegressor(
            max_iter=400, learning_rate=0.05, max_leaf_nodes=15,
            min_samples_leaf=40, l2_regularization=1.0, random_state=0)
        model.fit(X_all[tr], y[tr])
        pred[te] = model.predict(X_all[te])
    ms["delta"] = pred * ms.scale
    ms["mu_model"] = ms.mu_open + ms.delta
    return ms[ms.delta.notna()].copy()


def evaluate(ev):
    print(f"\nevaluated props: {len(ev)} "
          f"({ev.date.min()} .. {ev.date.max()}), by market:")
    print(ev.groupby('market').size().to_string())

    # -- 1. log loss at the close line
    for mkt in np.unique(ev.market):
        m = ev.market == mkt
        ev.loc[m, "pm"] = p_over(mkt, ev.loc[m, "mu_model"], ev.loc[m, "line_close"])
    e = ev[ev.actual != ev.line_close].copy()  # drop pushes
    yb = (e.actual > e.line_close).astype(float)
    ll_m = ll_binary(e.pm, yb)
    ll_c = ll_binary(e.p_close, yb)
    d = ll_c - ll_m  # >0: model better than close
    t, p = stats.ttest_1samp(d, 0)
    print(f"\nLL at close line: model {ll_m.mean():.5f} close {ll_c.mean():.5f} "
          f"diff {d.mean():+.5f} (t={t:.2f}, p={p:.2g}, n={len(e)})")
    e["d"] = d
    byd = e.groupby("date")["d"].mean()
    t2, p2 = stats.ttest_1samp(byd, 0)
    print(f"  date-clustered: t={t2:.2f}, p={p2:.2g} ({len(byd)} dates)")

    # blend: half market, half model
    e["pb"] = 0.5 * e.pm + 0.5 * e.p_close
    db = ll_binary(e.p_close, yb) - ll_binary(e.pb, yb)
    t3, p3 = stats.ttest_1samp(db, 0)
    print(f"  50/50 blend vs close: diff {db.mean():+.5f} (t={t3:.2f}, p={p3:.2g})")

    # -- 2. MAE of means
    print(f"\nMAE vs actual: open {(ev.mu_open - ev.actual).abs().mean():.3f}  "
          f"model {(ev.mu_model - ev.actual).abs().mean():.3f}  "
          f"close {(ev.mu_close - ev.actual).abs().mean():.3f}")

    # -- 3. bet sim at open prices
    for mkt in np.unique(ev.market):
        m = ev.market == mkt
        ev.loc[m, "pm_openline"] = p_over(mkt, ev.loc[m, "mu_model"],
                                          ev.loc[m, "open_line"])
        c = m & ev.mu_close.notna()
        ev.loc[c, "pc_openline"] = p_over(mkt, ev.loc[c, "mu_close"],
                                          ev.loc[c, "open_line"])
    dec_o = amer_to_dec(ev.open_over_cost) if "open_over_cost" in ev else None
    for thresh in (0.03, 0.06):
        bets = []
        for _, r in ev.iterrows():
            if pd.isna(r.pm_openline) or pd.isna(r.pc_openline):
                continue
            for side, pmod, pcl in [("over", r.pm_openline, r.pc_openline),
                                    ("under", 1 - r.pm_openline, 1 - r.pc_openline)]:
                cost = r.open_over_cost if side == "over" else r.open_under_cost
                if pd.isna(cost):
                    continue
                o = float(amer_to_dec(cost))
                if pmod * o - 1 < thresh:
                    continue
                if r.actual == r.open_line:
                    pnl = 0.0
                elif (r.actual > r.open_line) == (side == "over"):
                    pnl = o - 1
                else:
                    pnl = -1.0
                bets.append({"pnl": pnl, "clv": pcl * o - 1, "date": r.date,
                             "market": r.market})
        b = pd.DataFrame(bets)
        if len(b) < 10:
            print(f"EV>{thresh:.0%}: only {len(b)} bets")
            continue
        clv_t = b.clv.mean() / (b.clv.std() / np.sqrt(len(b)))
        print(f"EV>{thresh:.0%} at open: {len(b)} bets, ROI {b.pnl.mean():+.2%}, "
              f"CLV {b.clv.mean():+.2%} (t={clv_t:.1f})")
    return e


def main():
    ms = prepare()
    ev = walk_forward(ms)
    ev.to_pickle(os.path.join(ROOT, "results", "preds.pkl"))
    evaluate(ev)


if __name__ == "__main__":
    main()
