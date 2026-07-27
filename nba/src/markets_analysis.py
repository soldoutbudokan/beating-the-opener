"""Benchmark the model against every market named in the goal.

  1. Moneyline  -- log loss vs the de-vigged closing (and opening) price.
  2. Spread     -- against-the-spread hit rate vs the closing number (break-even
                   at -110 is 52.38%).
  3. Total      -- over/under hit rate vs the closing number.

Spread and total models are fit walk-forward exactly like the win model.
Tier A (strict) is used for opening-line comparisons because its inputs are all
known the day before, when the opening number is posted.
"""
import os
import sys

import numpy as np
import pandas as pd
from scipy import stats

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model as M  # noqa: E402
from final_experiment import SAME_GAME  # noqa: E402
from market import log_loss_vec  # noqa: E402
from run_experiment import PRED_SEASONS, TEST, VAL  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BREAKEVEN = 0.5238  # -110


def wf_reg(df, cols, factory, target, seasons):
    out = np.full(len(df), np.nan)
    for s in seasons:
        tr = df[df.season_year < s]
        te = (df.season_year == s).values
        m = factory()
        m.fit(tr[cols].astype(float).values, tr[target].values.astype(float))
        out[te] = m.predict(df[te][cols].astype(float).values)
    return out


def binom_p(wins, n, p0=BREAKEVEN):
    if n == 0:
        return np.nan
    return float(1 - stats.binom.cdf(wins - 1, n, p0))


def ats_report(pred_margin, spread_home, margin, label, min_edge=0.0):
    """spread_home is the home team's closing number (negative = home favoured)."""
    ok = np.isfinite(pred_margin) & np.isfinite(spread_home) & np.isfinite(margin)
    pm, sp, mg = pred_margin[ok], spread_home[ok], margin[ok]
    # Home covers when margin + spread > 0.
    edge = pm + sp
    sel = np.abs(edge) > min_edge
    pm, sp, mg, edge = pm[sel], sp[sel], mg[sel], edge[sel]
    bet_home = edge > 0
    res = mg + sp
    push = np.abs(res) < 1e-9
    win = np.where(bet_home, res > 0, res < 0)
    n = int((~push).sum())
    w = int(win[~push].sum())
    rate = w / n if n else np.nan
    roi = (w * (100 / 110) - (n - w)) / n if n else np.nan
    print(f"  {label:38s} bets={n:5d} hit={rate:.4f} ROI={roi:+.4f} "
          f"p(vs 52.38%)={binom_p(w, n):.4f}")
    return {"n": n, "rate": rate, "roi": roi}


def main():
    df = pd.read_csv(os.path.join(ROOT, "data", "raw", "preds_final_v4.csv"))
    df = df.reset_index(drop=True)
    all_cols = M.blind_features(df)
    all_cols = [c for c in all_cols if not c.startswith(("A_", "B_"))]
    strict = [c for c in all_cols if c not in SAME_GAME]

    # ---- margin and total regressions ----
    df["pred_margin_A"] = wf_reg(df, strict, M.mk_gbm_reg, "margin", PRED_SEASONS)
    df["pred_margin_B"] = wf_reg(df, all_cols, M.mk_gbm_reg, "margin", PRED_SEASONS)
    df["pred_total_A"] = wf_reg(df, strict, M.mk_gbm_reg, "total_pts", PRED_SEASONS)
    df["pred_total_B"] = wf_reg(df, all_cols, M.mk_gbm_reg, "total_pts", PRED_SEASONS)
    print("fitted margin + total models")

    t = df[df.season_year.isin(TEST)].copy()
    y = t.home_win.values

    # ================= MONEYLINE =================
    print(f"\n=== 1. MONEYLINE: log loss vs CLOSING line (n={len(t)}) ===")
    tt = t[t.mkt_mult.notna() & t.A_stack.notna() & t.B_stack.notna()]
    yy = tt.home_win.values
    for c, lab in [("mkt_mult", "MARKET closing"), ("A_stack", "Tier A stack"),
                   ("B_stack", "Tier B stack"), ("A_blend", "Tier A blend"),
                   ("B_blend", "Tier B blend")]:
        e = M.evaluate(yy, tt[c].values, lab)
        print(f"  {lab:22s} logloss={e['logloss']:.5f} brier={e['brier']:.5f} acc={e['acc']:.4f}")

    print("\n=== 1b. MONEYLINE vs OPENING line (fair for Tier A: known day-before) ===")
    o = t[t.mkt_open_mult.notna() & t.A_stack.notna() & t.mkt_mult.notna()]
    yo = o.home_win.values
    for c, lab in [("mkt_open_mult", "MARKET opening"), ("mkt_mult", "MARKET closing"),
                   ("A_stack", "Tier A stack"), ("B_stack", "Tier B stack")]:
        e = M.evaluate(yo, o[c].values, lab)
        print(f"  {lab:22s} logloss={e['logloss']:.5f} brier={e['brier']:.5f} acc={e['acc']:.4f}")
    for c, lab in [("A_stack", "Tier A stack"), ("B_stack", "Tier B stack")]:
        s = M.paired_test(yo, o[c].values, o.mkt_open_mult.values)
        flag = "BEATS OPEN" if s["ci_hi"] < 0 else ("loses" if s["ci_lo"] > 0 else "tied")
        print(f"    {lab:20s} vs OPEN dLL={s['mean_diff']:+.5f} "
              f"CI[{s['ci_lo']:+.5f},{s['ci_hi']:+.5f}] p={s['p_two_sided']:.4f} [{flag}]")

    # ================= SPREAD =================
    print("\n=== 2. SPREAD: against-the-spread vs CLOSING number ===")
    sp = t[t.market_spread.notna()].copy()
    if len(sp):
        # ESPN stores the home team's handicap; verify sign against outcomes.
        chk = np.corrcoef(sp.market_spread.values, sp.margin.values)[0, 1]
        print(f"  (sanity) corr(home spread, margin) = {chk:+.3f} "
              f"-> {'home number is negative when favoured' if chk < 0 else 'sign flipped'}")
        sh = sp.market_spread.values
        for c, lab in [("pred_margin_A", "Tier A margin model"),
                       ("pred_margin_B", "Tier B margin model")]:
            for me in (0.0, 1.0, 2.0, 3.0):
                ats_report(sp[c].values, sh, sp.margin.values,
                           f"{lab} (edge>{me:.0f})", min_edge=me)

    # ================= TOTAL =================
    print("\n=== 3. TOTAL: over/under vs CLOSING number ===")
    tot = t[t.market_total.notna()].copy()
    if len(tot):
        mt = tot.market_total.values
        actual = tot.total_pts.values
        for c, lab in [("pred_total_A", "Tier A total model"),
                       ("pred_total_B", "Tier B total model")]:
            pred = tot[c].values
            for me in (0.0, 2.0, 4.0, 6.0):
                edge = pred - mt
                sel = np.abs(edge) > me
                res = actual[sel] - mt[sel]
                push = np.abs(res) < 1e-9
                bet_over = edge[sel] > 0
                win = np.where(bet_over, res > 0, res < 0)
                n = int((~push).sum())
                w = int(win[~push].sum())
                rate = w / n if n else np.nan
                roi = (w * (100 / 110) - (n - w)) / n if n else np.nan
                print(f"  {lab} (edge>{me:.0f}){'':<12} bets={n:5d} hit={rate:.4f} "
                      f"ROI={roi:+.4f} p(vs 52.38%)={binom_p(w, n):.4f}")
        # How good is the market's own total?
        mae_m = np.mean(np.abs(actual - mt))
        for c, lab in [("pred_total_A", "Tier A"), ("pred_total_B", "Tier B")]:
            print(f"  MAE total: market={mae_m:.3f} {lab}={np.mean(np.abs(actual - tot[c].values)):.3f}")

    df.to_csv(os.path.join(ROOT, "data", "raw", "preds_markets.csv"), index=False)


if __name__ == "__main__":
    main()
