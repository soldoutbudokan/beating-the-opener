"""v2: predict the market's own open->close MOVE, not the outcome.

v1 (train_eval.py) regressed the outcome residual and lost to the close by
-0.028 LL: outcome noise (sigma ~5 pts) swamps the signal and the model
disagrees with the market far too much. The open->close mean move is ~6x less
noisy and directly measures what the opener gets wrong (soccer lesson: gbmmove
worked, outcome-GBM lost).

Extra features vs v1:
  move_mom   player-level EW of past standardized open->close moves (leak-free)
  gap_ew     (mu_open - box-score EW projection) / scale: where the opener
             disagrees with fundamentals
"""
import os

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.ensemble import HistGradientBoostingRegressor

from odds_utils import amer_to_dec, ll_binary
from dist_utils import p_over
from train_eval import (MARKETS, PANEL_FEATS, RETRAIN_DAYS, MIN_TRAIN,
                        BURN_IN_DAYS, prepare)

ROOT = os.path.join(os.path.dirname(__file__), "..")

EW_PROJ = {  # market -> EW columns that sum to a fundamentals projection
    "points": ["poi_ewf"], "rebounds": ["reb_ewf"], "assists": ["ass_ewf"],
    "threes": ["tpm_ewf"], "pra": ["poi_ewf", "reb_ewf", "ass_ewf"],
    "pts_ast": ["poi_ewf", "ass_ewf"], "pts_reb": ["poi_ewf", "reb_ewf"],
    "reb_ast": ["reb_ewf", "ass_ewf"],
}


def add_v2_features(ms):
    ms = ms.sort_values("date").reset_index(drop=True)
    ms["move"] = (ms.mu_close - ms.mu_open) / ms.scale
    # player x market EW of past moves (shift -> strictly earlier props)
    ms["move_mom"] = (
        ms.groupby(["player", "market"], sort=False)["move"]
        .transform(lambda s: s.shift(1).ewm(alpha=0.25, min_periods=1).mean()))
    # player-level (all markets pooled) momentum too
    ms["move_mom_all"] = (
        ms.groupby("player", sort=False)["move"]
        .transform(lambda s: s.shift(1).ewm(alpha=0.15, min_periods=1).mean()))
    proj = np.full(len(ms), np.nan)
    for mkt, cols in EW_PROJ.items():
        m = (ms.market == mkt).to_numpy()
        proj[m] = ms.loc[m, cols].sum(axis=1, min_count=len(cols))
    ms["gap_ew"] = (ms.mu_open - proj) / ms.scale
    return ms


V2_EXTRA = ["move_mom", "move_mom_all", "gap_ew"]
# absent_ew_min counts same-day scratches - knowable before tip (props void on
# DNP) but NOT necessarily when the open was still up. Set ABLATE_ABSENT=1 for
# the strictly-open-safe variant.
ABLATE = os.environ.get("ABLATE_ABSENT") == "1"


def features(ms):
    cols = PANEL_FEATS + V2_EXTRA
    if ABLATE:
        cols = [c for c in cols if c != "absent_ew_min"]
    X = ms[cols].copy()
    X["mkt_i"] = ms.mkt_i
    X["mu_open"] = ms.mu_open
    X["open_line"] = ms.open_line
    X["open_juice"] = ms.open_juice
    X["open_book"] = ms.open_book
    return X.to_numpy(float)


def walk_forward(ms):
    X_all = features(ms)
    y = ms.move.to_numpy()
    date_ts = pd.to_datetime(ms.date)
    dates = sorted(ms.date.unique())
    start = pd.Timestamp(dates[0]) + pd.Timedelta(days=BURN_IN_DAYS)
    eval_dates = [d for d in dates if pd.Timestamp(d) >= start]
    pred = np.full(len(ms), np.nan)
    for i, d0 in enumerate(eval_dates[::RETRAIN_DAYS]):
        blocks = eval_dates[::RETRAIN_DAYS]
        d1 = blocks[i + 1] if i + 1 < len(blocks) else "9999"
        tr = ((date_ts < pd.Timestamp(d0)) & ms.move.notna()).to_numpy()
        te = ((ms.date >= d0) & (ms.date < d1)).to_numpy()
        if tr.sum() < MIN_TRAIN or te.sum() == 0:
            continue
        model = HistGradientBoostingRegressor(
            max_iter=300, learning_rate=0.05, max_leaf_nodes=15,
            min_samples_leaf=60, l2_regularization=1.0, random_state=0)
        model.fit(X_all[tr], y[tr])
        pred[te] = model.predict(X_all[te])
    ms["pred_move"] = pred
    ms["mu_model"] = ms.mu_open + ms.pred_move * ms.scale
    return ms[ms.pred_move.notna()].copy()


def evaluate(ev):
    print(f"evaluated props: {len(ev)} ({ev.date.min()} .. {ev.date.max()})")
    print(f"move prediction: corr(pred, actual move) = "
          f"{ev.pred_move.corr(ev.move):.3f}, sd(pred)={ev.pred_move.std():.3f} "
          f"vs sd(move)={ev.move.std():.3f}")

    for mkt in np.unique(ev.market):
        m = ev.market == mkt
        ev.loc[m, "pm"] = p_over(mkt, ev.loc[m, "mu_model"], ev.loc[m, "line_close"])
        ev.loc[m, "po"] = p_over(mkt, ev.loc[m, "mu_open"], ev.loc[m, "line_close"])
    e = ev[ev.actual != ev.line_close].copy()
    yb = (e.actual > e.line_close).astype(float)
    ll_m, ll_c, ll_o = (ll_binary(e.pm, yb), ll_binary(e.p_close, yb),
                        ll_binary(e.po, yb))
    for name, ll in [("open ", ll_o), ("model", ll_m), ("close", ll_c)]:
        print(f"  LL {name}: {ll.mean():.5f}")
    dmo = ll_o - ll_m  # model vs open, >0 model better
    t1, p1 = stats.ttest_1samp(dmo, 0)
    e["dmo"] = dmo
    bd = e.groupby("date")["dmo"].mean()
    t1c, p1c = stats.ttest_1samp(bd, 0)
    print(f"model vs open: {dmo.mean():+.5f} (t={t1:.2f}, p={p1:.2g}; "
          f"clustered t={t1c:.2f}, p={p1c:.2g})")
    dmc = ll_c - ll_m
    t2, p2 = stats.ttest_1samp(dmc, 0)
    print(f"model vs close: {dmc.mean():+.5f} (t={t2:.2f}, p={p2:.2g})")
    wedge = (ll_o - ll_c).mean()
    if wedge > 0:
        print(f"capture: {(dmo.mean()) / wedge:.0%} of the open->close wedge")

    print(f"MAE: open {(ev.mu_open - ev.actual).abs().mean():.4f}  "
          f"model {(ev.mu_model - ev.actual).abs().mean():.4f}  "
          f"close {(ev.mu_close - ev.actual).abs().mean():.4f}")

    # bet sim at open prices
    for mkt in np.unique(ev.market):
        m = ev.market == mkt
        ev.loc[m, "pm_ol"] = p_over(mkt, ev.loc[m, "mu_model"], ev.loc[m, "open_line"])
        c = m & ev.mu_close.notna()
        ev.loc[c, "pc_ol"] = p_over(mkt, ev.loc[c, "mu_close"], ev.loc[c, "open_line"])
    rows = ev[ev.pm_ol.notna() & ev.pc_ol.notna()]
    for thresh in (0.02, 0.04, 0.06):
        recs = []
        for r in rows.itertuples():
            for side, pmod, pcl, cost in [
                    ("over", r.pm_ol, r.pc_ol, r.open_over_cost),
                    ("under", 1 - r.pm_ol, 1 - r.pc_ol, r.open_under_cost)]:
                if pd.isna(cost):
                    continue
                o = float(amer_to_dec(cost))
                if pmod * o - 1 < thresh:
                    continue
                if r.actual == r.open_line:
                    pnl = 0.0
                else:
                    pnl = (o - 1) if (r.actual > r.open_line) == (side == "over") else -1.0
                recs.append({"pnl": pnl, "clv": pcl * o - 1, "date": r.date,
                             "mkt": r.market,
                             "pg": f"{r.event_id}_{r.player}"})
        b = pd.DataFrame(recs)
        if len(b) < 10:
            print(f"EV>{thresh:.0%}: only {len(b)} bets")
            continue

        def tstat(x):
            return x.mean() / (x.std() / np.sqrt(len(x)))

        # cluster by player-game (combo markets on one player overlap heavily)
        bpg = b.groupby("pg").agg(pnl=("pnl", "mean"), clv=("clv", "mean"))
        bdt = b.groupby("date").pnl.mean()
        print(f"EV>{thresh:.0%} at open: {len(b)} bets ({len(bpg)} player-games), "
              f"ROI {b.pnl.mean():+.2%} (pg-clustered t={tstat(bpg.pnl):.1f}), "
              f"CLV {b.clv.mean():+.2%} (pg t={tstat(bpg.clv):.1f}), "
              f"{(bdt > 0).mean():.0%} of {len(bdt)} days positive")
    return ev


def main():
    ms = prepare()
    ms = add_v2_features(ms)
    ev = walk_forward(ms)
    ev.to_pickle(os.path.join(ROOT, "results", "preds_v2.pkl"))
    evaluate(ev)


if __name__ == "__main__":
    main()
