"""Economic evaluation: does the statistical edge survive real vig?

Bets are settled at the actual American closing prices, so the ~3.9% hold is paid
in full. We also measure closing-line value against the opening price, which is
the standard test of whether a model anticipates market movement.
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from market import american_to_decimal, american_to_prob, log_loss_vec  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST = [2024, 2025, 2026]


def simulate(df, pcol, edge_threshold=0.0, kelly_frac=0.0, stake=1.0):
    """Flat stake unless kelly_frac > 0. Returns per-bet ledger."""
    rows = []
    for r in df.itertuples(index=False):
        p = getattr(r, pcol)
        if not np.isfinite(p):
            continue
        for side in ("home", "away"):
            ml = getattr(r, f"{side}_ml_close")
            if ml is None or not np.isfinite(ml):
                continue
            dec = float(american_to_decimal(ml))
            p_win = p if side == "home" else 1.0 - p
            implied = float(american_to_prob(ml))
            edge = p_win - implied
            if edge <= edge_threshold:
                continue
            b = dec - 1.0
            if kelly_frac > 0:
                f = max(0.0, (p_win * b - (1 - p_win)) / b) * kelly_frac
                size = f
            else:
                size = stake
            if size <= 0:
                continue
            won = (r.home_win == 1) if side == "home" else (r.home_win == 0)
            pnl = size * b if won else -size
            rows.append({"season": r.season_year, "side": side, "stake": size,
                         "pnl": pnl, "edge": edge, "dec": dec, "p": p_win,
                         "won": bool(won)})
    return pd.DataFrame(rows)


def report(ledger, label):
    if ledger.empty:
        print(f"  {label:34s} no bets")
        return None
    n = len(ledger)
    staked = ledger.stake.sum()
    pnl = ledger.pnl.sum()
    roi = pnl / staked
    # SE of ROI via per-bet returns
    ret = ledger.pnl / ledger.stake
    se = ret.std(ddof=1) / np.sqrt(n)
    t = roi / se if se > 0 else 0.0
    print(f"  {label:34s} bets={n:5d} staked={staked:9.1f} pnl={pnl:+8.2f} "
          f"ROI={roi:+.4f} t={t:+.2f} winrate={ledger.won.mean():.3f}")
    return {"label": label, "n": n, "roi": roi, "t": t, "pnl": pnl}


def clv_analysis(df, pcol):
    """Did the model's disagreement with the open predict the move to the close?"""
    d = df[df.mkt_open_mult.notna() & df.mkt_mult.notna() & df[pcol].notna()].copy()
    if d.empty:
        return
    move = d.mkt_mult - d.mkt_open_mult          # how the market actually moved
    signal = d[pcol] - d.mkt_open_mult           # model's disagreement with open
    corr = np.corrcoef(signal, move)[0, 1]
    # Fraction of games where the model correctly called the direction of the move
    mask = move.abs() > 0.005
    hit = ((np.sign(signal[mask]) == np.sign(move[mask])).mean()
           if mask.sum() else np.nan)
    print(f"  corr(model-vs-open, open->close move) = {corr:+.4f}")
    print(f"  directional hit rate on moves >0.5pp  = {hit:.4f}  (n={int(mask.sum())})")


def main():
    df = pd.read_csv(os.path.join(ROOT, "data", "raw", "preds_tiers.csv"))
    t = df[df.season_year.isin(TEST) & df.mkt_mult.notna()].copy()
    print(f"held-out games: {len(t)}\n")

    print("=== MONEYLINE BETTING AT CLOSING PRICES (flat stake) ===")
    for pcol, lab in [("mkt_mult", "market vs itself (sanity)"),
                      ("A_stack", "Tier A stack"),
                      ("B_stack", "Tier B stack"),
                      ("A_blend", "Tier A blend"),
                      ("B_blend", "Tier B blend")]:
        for thr in (0.0, 0.02, 0.05):
            led = simulate(t, pcol, edge_threshold=thr)
            report(led, f"{lab} (edge>{thr:.0%})")
        print()

    print("=== KELLY (1/4) SIZING, edge > 2% ===")
    for pcol, lab in [("B_stack", "Tier B stack"), ("B_blend", "Tier B blend")]:
        led = simulate(t, pcol, edge_threshold=0.02, kelly_frac=0.25)
        report(led, lab)

    print("\n=== PER-SEASON ROI (Tier B blend, edge>2%) ===")
    led = simulate(t, "B_blend", edge_threshold=0.02)
    if not led.empty:
        g = led.groupby("season").apply(
            lambda d: pd.Series({"bets": len(d), "roi": d.pnl.sum() / d.stake.sum(),
                                 "pnl": d.pnl.sum()}))
        print(g.round(4).to_string())

    print("\n=== CLOSING LINE VALUE (model vs opening line) ===")
    for pcol, lab in [("A_stack", "Tier A stack"), ("B_stack", "Tier B stack")]:
        print(f" {lab}:")
        clv_analysis(t, pcol)

    print("\n=== WHERE IS THE EDGE? log loss by missing-talent bucket ===")
    t["miss_mag"] = t[["home_missing_talent", "away_missing_talent"]].max(axis=1)
    qs = t.miss_mag.quantile([0, .25, .5, .75, 1.0]).values
    for i in range(4):
        lo, hi = qs[i], qs[i + 1]
        m = (t.miss_mag >= lo) & (t.miss_mag <= hi if i == 3 else t.miss_mag < hi)
        s = t[m]
        if len(s) < 50:
            continue
        mk = log_loss_vec(s.home_win.values, s.mkt_mult.values).mean()
        b = log_loss_vec(s.home_win.values, s.B_stack.values).mean()
        bb = log_loss_vec(s.home_win.values, s.B_blend.values).mean()
        print(f"  missing_talent [{lo:6.1f},{hi:6.1f}) n={len(s):4d} "
              f"market={mk:.5f} TierB={b:.5f} blend={bb:.5f} "
              f"blend_edge={bb - mk:+.5f}")


if __name__ == "__main__":
    main()
