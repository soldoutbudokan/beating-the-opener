"""Stage B from-scratch baseline (fp programme, PROGRESS.md Market 1).

Prices a prop generatively: predicted minutes x per-minute rate, opponent
pace/defense adjusted, per-market distribution -> P(over) at the quoted line.
The line is the evaluation threshold only; no market-derived quantity is an
input (no mu_open, no prices, no book columns). Panel features are strictly
prior-game (shift-then-ewm). Absence features are EXCLUDED in Stage B:
absent_ew_min / absent_prior_ew_min condition on tonight's participation --
the nba/ leakage trap; a strictly-prior version is Stage C work.

sigma(mu) is refit on the pre-2025 panel only (walk-forward; the shipped
SIGMA_AB in dist_utils was fit through 2026).

Fixed constants throughout -- no dev-set tuning beyond the two feature
iterations the gates allow.

Usage: python3 src/fp_model.py            # dev season (2025) vs gates
       python3 src/fp_model.py --holdout  # score 2026 once (Stage C only)
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import dist_utils
from fp_benchmark import ll, clustered_t

ROOT = os.path.join(os.path.dirname(__file__), "..")

# stat components per market; mu(combo) = sum of component mus
PARTS = {
    "points": ["poi"], "rebounds": ["reb"], "assists": ["ass"],
    "threes": ["tpm"], "steals": ["ste"], "blocks": ["blo"],
    "turnovers": ["tur"],
    "pra": ["poi", "reb", "ass"], "pts_reb": ["poi", "reb"],
    "pts_ast": ["poi", "ass"], "reb_ast": ["reb", "ass"],
    "stl_blk": ["ste", "blo"],
}
SCORING = {"poi", "tpm"}          # stats also scaled by opponent defense
W_FAST = 0.6                      # fast-vs-slow EW blend for minutes/pergame
W_RATE = 0.5                      # rate-x-minutes vs per-game blend
DEF_EXP = 0.5                     # defense factor exponent (efficiency share)


def stat_mu(ms, stat):
    """Baseline mean for one stat: blend(per-game EW, rate x minutes)."""
    per_game = ms[f"{stat}_ewf"]
    slow = f"{stat}_ews"
    if slow in ms.columns:
        per_game = W_FAST * ms[f"{stat}_ewf"] + (1 - W_FAST) * ms[slow]
        per_game = per_game.fillna(ms[f"{stat}_ewf"])
    minutes = (W_FAST * ms.min_ewf + (1 - W_FAST) * ms.min_ews).fillna(ms.min_ewf)
    rate = ms[f"{stat}_rate_ewf"] * minutes
    return W_RATE * per_game.fillna(rate) + (1 - W_RATE) * rate.fillna(per_game)


def predict(ms):
    """mu per row of the modelset, market-blind."""
    # league context per date from shifted EWs (leak-free even same-date)
    lg_pace = ms.groupby("date").tm_pace_ew.transform("mean")
    lg_allow = ms.groupby("date").opp_pts_against_ew.transform("mean")
    pace_f = (ms.tm_pace_ew + ms.opp_pace_ew) / (ms.tm_pace_ew + lg_pace)
    def_f = (ms.opp_pts_against_ew / lg_allow) ** DEF_EXP

    mu = pd.Series(np.nan, index=ms.index)
    for mkt, parts in PARTS.items():
        rows = ms.market == mkt
        if not rows.any():
            continue
        base = sum(stat_mu(ms.loc[rows], p) for p in parts)
        adj = pace_f[rows].fillna(1.0)
        scoring_share = np.mean([p in SCORING for p in parts])
        if scoring_share:
            adj = adj * def_f[rows].fillna(1.0) ** scoring_share
        mu[rows] = base * adj
    return mu


def evaluate(ev, label):
    print(f"\n== {label}: n={len(ev)} "
          f"({ev.date.min()} .. {ev.date.max()}) ==")
    print(f"LL(model) = {ev.ll_model.mean():.5f}")
    print(f"LL(open)  = {ev.ll_open.mean():.5f}")
    d, t = clustered_t((ev.ll_model - ev.ll_open).values, ev.date)
    print(f"model - open = {d:+.5f} (clustered t={t:.1f})  "
          f"[G1 needs <= +0.010]")
    cal = ev.p_model.mean() - ev.over.mean()
    print(f"calibration: mean P(over) {ev.p_model.mean():.4f} vs realized "
          f"{ev.over.mean():.4f} -> {cal*100:+.2f}pp  [G2 needs |.| <= 2.5]")
    sl = ev[ev.coh_close & (ev.line_close == ev.open_line)].copy()
    if len(sl):
        sl["ll_close"] = ll(sl.p_close, sl.over)
        d2, t2 = clustered_t((sl.ll_model - sl.ll_close).values, sl.date)
        print(f"vs same-line close (n={len(sl)}): model - close = {d2:+.5f} "
              f"(clustered t={t2:.1f})  [G3 tripwire: < -0.001 at |t|>3]")
    print("per market (model-open LL gap):")
    for m, sub in ev.groupby("market"):
        if len(sub) < 50:
            continue
        print(f"  {m:10s} n={len(sub):5d}  gap={sub.ll_model.mean()-sub.ll_open.mean():+.5f}"
              f"  cal={100*(sub.p_model.mean()-sub.over.mean()):+.1f}pp")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout", action="store_true",
                    help="score the 2026 held-out season (Stage C, once)")
    args = ap.parse_args()

    panel = pd.read_pickle(os.path.join(ROOT, "data", "panel.pkl"))
    pre = panel[panel.game_date < "2025-01-01"]
    dist_utils.SIGMA_AB = dist_utils.fit_sigma(pre)
    print("sigma(mu) refit on pre-2025 panel "
          f"({len(pre)} player-games): {dist_utils.SIGMA_AB}")

    ms = pd.read_pickle(os.path.join(ROOT, "data", "modelset.pkl"))
    ms = ms[ms.matched & ~ms.void & ms.open_coherent
            & (ms.actual != ms.open_line)].copy()
    ms["over"] = (ms.actual > ms.open_line).astype(int)
    ms["mu_model"] = predict(ms)

    cover = ms.mu_model.notna()
    print(f"coverage: {cover.mean()*100:.1f}% of {len(ms)} eval props "
          f"({(~cover).sum()} dropped, no prior-game features)")
    ev = ms[cover].copy()
    ev["p_model"] = [
        float(dist_utils.p_over(m, mu, li))
        for m, mu, li in zip(ev.market, ev.mu_model, ev.open_line)]
    ev["ll_model"] = ll(ev.p_model, ev.over)
    ev["ll_open"] = ll(ev.p_open, ev.over)

    evaluate(ev[ev.season == 2025], "dev season 2025")
    if args.holdout:
        evaluate(ev[ev.season == 2026], "HELD-OUT season 2026 (scored once)")


if __name__ == "__main__":
    main()
