"""Walk-forward move model: predict the market's own open->close move.

Port of wnba/src/train_eval_v2.py (the winning architecture - the outcome
model lost to the close; the standardized move is ~6x less noisy). One
pooled HistGradientBoosting model per role group (MLB: pitcher-markets and
batter-markets separately; NBA: one), mkt_i as a feature, expanding window,
trained on strictly earlier dates only.

Safeguards carried from AUDIT:
  N1  expanding per-market shade on strictly-past graded rows; EV must come
      from the move model's direction, never from the shade alone
  H3  bet sim restricted to the FD tradeable cell (FD-sourced opener + FD
      close quoted); EV priced at FD's OPENING price
  H4  zero-skill placebo (shaded opener as the probability) run in the same
      cell, reported as its own block
  C1/H2/N4  modelset rows are coherent-open only; LL/CLV use coherent closes

Dev/holdout: dev = dates <= EVAL_END (env; default max_date - 56d), printed
by default. --holdout prints the held-out rows instead - run it EXACTLY ONCE,
after the PLAN.md Phase 2/3 gates are frozen.

Usage: python3 src/train_eval.py --sport MLB [--holdout]
"""
import argparse
import os

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.ensemble import HistGradientBoostingRegressor

import dist_utils
from build_modelset import MARKETS, PANEL_FEATS
from dist_utils import p_over
from grade_props import STAT_COLS
from odds_utils import amer_to_dec, apply_shade, fit_shade, ll_binary

ROOT = os.path.join(os.path.dirname(__file__), "..")

RETRAIN_DAYS = 7
BURN_IN_DAYS = 21
MIN_TRAIN = {"MLB": 5000, "NBA": 1500}  # density-scaled (wnba: 400)
MIN_SHADE_N = 500  # per-market obs before trusting its own shade fit
DEV_TAIL_DAYS = 56
V2_EXTRA = ["move_mom", "move_mom_all", "gap_ew"]
EV_THRESH = (0.02, 0.03, 0.06)


def prepare(sport):
    ms = pd.read_pickle(os.path.join(ROOT, "data",
                                     f"modelset_{sport.lower()}.pkl"))
    dist_utils.load_params(sport)  # sigma/NegBin r fitted by build_modelset
    ms = ms[ms.mu_open.notna() & ms.actual.notna()
            & ~ms.void.fillna(False).astype(bool)].copy()
    ms["open_juice"] = ms.p_open - 0.5
    return ms.sort_values("date").reset_index(drop=True)


def add_momentum(ms):
    """Leak-free move momentum (train_eval_v2): shift(1) -> strictly earlier
    props of the same player before the EW."""
    ms = ms.sort_values("date").reset_index(drop=True)
    ms["move_mom"] = (
        ms.groupby(["player", "market"], sort=False)["y"]
        .transform(lambda s: s.shift(1).ewm(alpha=0.25, min_periods=1).mean()))
    ms["move_mom_all"] = (
        ms.groupby("player", sort=False)["y"]
        .transform(lambda s: s.shift(1).ewm(alpha=0.15, min_periods=1).mean()))
    return ms


def features(ms, sport, role):
    X = ms[PANEL_FEATS[sport][role] + V2_EXTRA].copy()
    X["mkt_i"] = ms.mkt_i
    X["mu_open"] = ms.mu_open
    X["open_line"] = ms.open_line
    X["open_juice"] = ms.open_juice
    # open_book deliberately excluded: the model must not learn per-book
    # mispricing it can't trade (AUDIT "conditioning on unknowable info")
    return X.to_numpy(float)


def fit_shades(ms, tr, mkts):
    """Per-market over-shade (AUDIT N1) from strictly-past graded rows."""
    y_all = (ms.actual > ms.open_line).to_numpy(float)
    ok = tr & (ms.actual != ms.open_line).to_numpy()  # drop pushes
    pooled = fit_shade(ms.p_open[ok], y_all[ok]) if ok.sum() >= MIN_SHADE_N \
        else 0.0
    out = {}
    for mkt in mkts:
        m = ok & (ms.market == mkt).to_numpy()
        out[mkt] = fit_shade(ms.p_open[m], y_all[m]) if m.sum() >= MIN_SHADE_N \
            else pooled
    return out


def walk_forward(ms, sport):
    pred = np.full(len(ms), np.nan)
    shade = np.full(len(ms), np.nan)
    roles = sorted({STAT_COLS[sport][m][0] for m in MARKETS[sport]})
    for role in roles:  # MLB: separate pitcher- and batter-market models
        ridx = np.flatnonzero((ms.role == role).to_numpy())
        if not len(ridx):
            continue
        sub = ms.iloc[ridx]
        X_all = features(sub, sport, role)
        yv = sub.y.to_numpy()
        date_ts = pd.to_datetime(sub.date)
        mkts = [m for m in MARKETS[sport] if STAT_COLS[sport][m][0] == role]
        dates = sorted(sub.date.unique())
        start = pd.Timestamp(dates[0]) + pd.Timedelta(days=BURN_IN_DAYS)
        blocks = [d for d in dates if pd.Timestamp(d) >= start][::RETRAIN_DAYS]
        for i, d0 in enumerate(blocks):
            d1 = blocks[i + 1] if i + 1 < len(blocks) else "9999"
            tr = ((date_ts < pd.Timestamp(d0)).to_numpy()
                  & sub.y.notna().to_numpy())  # strictly earlier, has a move
            te = ((sub.date >= d0) & (sub.date < d1)).to_numpy()
            if tr.sum() < MIN_TRAIN[sport] or te.sum() == 0:
                continue
            model = HistGradientBoostingRegressor(
                max_iter=300, learning_rate=0.05, max_leaf_nodes=15,
                min_samples_leaf=60, l2_regularization=1.0, random_state=0)
            model.fit(X_all[tr], yv[tr])
            pred[ridx[te]] = model.predict(X_all[te])
            # shade: expanding, all strictly-past graded rows (no close needed)
            sh = fit_shades(sub, (date_ts < pd.Timestamp(d0)).to_numpy(), mkts)
            for mkt, dsh in sh.items():
                sel = te & (sub.market == mkt).to_numpy()
                shade[ridx[sel]] = dsh
    ms["pred_move"] = pred
    ms["shade"] = shade
    ms["mu_model"] = ms.mu_open + ms.pred_move * ms.scale
    return ms[ms.pred_move.notna()].copy()


def tstat(x):
    return x.mean() / (x.std() / np.sqrt(len(x)))


def evaluate(ev, sport, label):
    if not len(ev):
        print(f"\n=== {label}: 0 evaluated props (archive too short for "
              f"burn-in {BURN_IN_DAYS}d + MIN_TRAIN {MIN_TRAIN[sport]}?) ===")
        return
    ev = ev.copy()
    print(f"\n=== {label}: {len(ev)} props ({ev.date.min()} .. {ev.date.max()}) ===")
    hm = ev.y.notna()
    if hm.any():
        print(f"move: corr(pred, move) = {ev.pred_move[hm].corr(ev.y[hm]):.3f}, "
              f"sd(pred)={ev.pred_move.std():.3f} vs sd(move)={ev.y[hm].std():.3f}")

    # -- calibration acceptance (AUDIT N1): fed the market's OWN prices,
    # shade-corrected P(over) must match the realized over rate
    acc = ev[ev.actual != ev.open_line]
    po_cal = apply_shade(acc.p_open, acc.shade)
    yo = (acc.actual > acc.open_line).astype(float).to_numpy()
    print("\ncalibration (market-fed, walk-forward shades):")
    print(f"  overall: raw {acc.p_open.mean():.4f}  cal {po_cal.mean():.4f}  "
          f"realized {yo.mean():.4f}  |bias| "
          f"{abs(po_cal.mean() - yo.mean()) * 100:.2f}pp "
          f"(PLAN gate < 0.75pp)")
    for mkt in MARKETS[sport]:
        m = (acc.market == mkt).to_numpy()
        if m.sum() < 50:
            continue
        cal = float(apply_shade(acc.p_open[m], acc.shade[m]).mean())
        print(f"  {mkt:<14} n={m.sum():<6} raw {acc.p_open[m].mean():.4f}  "
              f"cal {cal:.4f}  realized {yo[m].mean():.4f}  "
              f"|bias| {abs(cal - yo[m].mean()) * 100:.2f}pp")

    # -- log loss at the close line, coherent-close rows only; open and model
    # shade-calibrated (paired diff isolates move skill), close raw benchmark
    cc = ev.mu_close.notna() & ev.line_close.notna()
    ev["pm"] = np.nan
    ev["po"] = np.nan
    for mkt in MARKETS[sport]:
        m = ((ev.market == mkt) & cc).to_numpy()
        if not m.any():
            continue
        ev.loc[m, "pm"] = p_over(sport, mkt, ev.loc[m, "mu_model"],
                                 ev.loc[m, "line_close"])
        ev.loc[m, "po"] = p_over(sport, mkt, ev.loc[m, "mu_open"],
                                 ev.loc[m, "line_close"])
    ev["pm"] = apply_shade(ev.pm, ev.shade)
    ev["po"] = apply_shade(ev.po, ev.shade)
    e = ev[cc & ev.p_close.notna() & (ev.actual != ev.line_close)].copy()
    if len(e):
        yb = (e.actual > e.line_close).astype(float)
        ll_o, ll_m, ll_c = (ll_binary(e.po, yb), ll_binary(e.pm, yb),
                            ll_binary(e.p_close, yb))
        print(f"\nLL at the close line (coh_close, n={len(e)}):")
        print("  per-market: n  LL(open)  LL(model)  LL(close)  model-vs-open")
        for mkt in MARKETS[sport]:
            m = (e.market == mkt).to_numpy()
            if m.sum() < 50:
                continue
            print(f"    {mkt:<14} {m.sum():<6} {ll_o[m].mean():.5f}  "
                  f"{ll_m[m].mean():.5f}   {ll_c[m].mean():.5f}   "
                  f"{(ll_o[m] - ll_m[m]).mean():+.5f}")
        for name, ll in [("open ", ll_o), ("model", ll_m), ("close", ll_c)]:
            print(f"  LL {name}: {ll.mean():.5f}")
        e["dmo"] = ll_o - ll_m  # >0 model better than open
        e["dmc"] = ll_c - ll_m  # >0 model better than close (tripwire!)
        for name, col in [("model vs open ", "dmo"), ("model vs close", "dmc")]:
            bd = e.groupby("date")[col].mean()
            t, p = stats.ttest_1samp(bd, 0)
            print(f"  {name}: {e[col].mean():+.5f} "
                  f"(date-clustered t={t:.2f}, p={p:.2g}, {len(bd)} dates)")
        wedge = (ll_o - ll_c).mean()
        if wedge > 0:
            print(f"  capture: {e.dmo.mean() / wedge:.0%} of the open->close "
                  f"wedge (PLAN gate >= 25%)")

    # -- bet sim at FD's OPENING price, FD tradeable cell (AUDIT H3):
    # FD-sourced opener + FD close quoted; side must agree with the predicted
    # move so EV never comes from the shade alone (AUDIT N1)
    ev["pm_ol"] = np.nan
    ev["pc_ol"] = np.nan
    for mkt in MARKETS[sport]:
        m = (ev.market == mkt).to_numpy()
        if not m.any():
            continue
        ev.loc[m, "pm_ol"] = p_over(sport, mkt, ev.loc[m, "mu_model"],
                                    ev.loc[m, "open_line"])
        c = m & ev.mu_close.notna().to_numpy()
        if c.any():
            ev.loc[c, "pc_ol"] = p_over(sport, mkt, ev.loc[c, "mu_close"],
                                        ev.loc[c, "open_line"])
    ev["pm_olc"] = apply_shade(ev.pm_ol, ev.shade)
    ev["pc_olc"] = apply_shade(ev.pc_ol, ev.shade)
    ev["po_olc"] = apply_shade(ev.p_open, ev.shade)  # placebo probability
    cell = ev[(ev.open_book == 10) & ev.p_fd.notna()
              & ev.pm_ol.notna() & ev.pc_ol.notna()]

    def sim(rr, thresh, tag, placebo=False):
        recs = []
        for r in rr.itertuples():
            p_mod = r.po_olc if placebo else r.pm_olc
            for side, pmod, pcl_raw, pcl_cal, cost in [
                    ("over", p_mod, r.pc_ol, r.pc_olc, r.open_over_cost),
                    ("under", 1 - p_mod, 1 - r.pc_ol, 1 - r.pc_olc,
                     r.open_under_cost)]:
                if pd.isna(cost):
                    continue
                # live rule: the MOVE model must point toward the bet side
                # (the placebo has no move signal - that is its point)
                if not placebo and (side == "over") != (r.mu_model > r.mu_open):
                    continue
                o = float(amer_to_dec(cost))
                if pmod * o - 1 < thresh:
                    continue
                if r.actual == r.open_line:
                    pnl = 0.0
                else:
                    pnl = (o - 1) if (r.actual > r.open_line) == (side == "over") \
                        else -1.0
                recs.append({"pnl": pnl, "clv_mkt": pcl_raw * o - 1,
                             "clv_cal": pcl_cal * o - 1,
                             "over": side == "over",
                             "pg": f"{r.event_id}_{r.player}"})
        b = pd.DataFrame(recs)
        if len(b) < 10:
            print(f"  EV>{thresh:.0%} {tag}: only {len(b)} bets")
            return
        bpg = b.groupby("pg").agg(pnl=("pnl", "mean"), cm=("clv_mkt", "mean"),
                                  cc=("clv_cal", "mean"))
        print(f"  EV>{thresh:.0%} {tag}: {len(b)} bets ({len(bpg)} pg, "
              f"{b.over.mean():.0%} overs), ROI {b.pnl.mean():+.2%} "
              f"(pg-t {tstat(bpg.pnl):.1f}), CLV-mkt {b.clv_mkt.mean():+.2%} "
              f"(pg-t {tstat(bpg.cm):.1f}), CLV-cal {b.clv_cal.mean():+.2%} "
              f"(pg-t {tstat(bpg.cc):.1f})")

    print(f"\nbet sim - FD tradeable cell (open_book==10, FD close quoted; "
          f"{len(cell)} props):")
    for thresh in EV_THRESH:
        sim(cell, thresh, "model  ")
    print("placebo - zero skill, shaded opener as probability (AUDIT H4):")
    for thresh in EV_THRESH:
        sim(cell, thresh, "placebo", placebo=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", required=True, choices=sorted(STAT_COLS))
    ap.add_argument("--holdout", action="store_true",
                    help="print holdout-only metrics - run EXACTLY ONCE")
    args = ap.parse_args()
    sport = args.sport
    if sport not in MARKETS:
        raise NotImplementedError(
            f"{sport}: not modeled yet - port dist_utils/features/"
            f"build_modelset for it first (MLB/NBA only for now)")

    ms = prepare(sport)
    ms = add_momentum(ms)
    ev = walk_forward(ms, sport)
    ev.to_pickle(os.path.join(ROOT, "data", f"preds_{sport.lower()}.pkl"))
    if not len(ev):
        evaluate(ev, sport, "no eval rows")
        return
    dev_end = os.environ.get("EVAL_END") or str(
        (pd.to_datetime(ms.date).max() - pd.Timedelta(days=DEV_TAIL_DAYS)).date())
    if args.holdout:
        evaluate(ev[ev.date > dev_end], sport,
                 f"HOLDOUT (date > {dev_end}) - one-shot, do not iterate")
    else:
        evaluate(ev[ev.date <= dev_end], sport, f"dev (date <= {dev_end})")


if __name__ == "__main__":
    main()
