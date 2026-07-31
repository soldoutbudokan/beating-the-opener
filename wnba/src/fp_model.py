"""From-scratch WNBA prop model (fp programme, PROGRESS.md Market 1).

Prices a prop generatively: predicted minutes x per-minute rate, opponent
pace/defense adjusted, per-market distribution -> P(over) at the quoted line.
The line is the evaluation threshold only; no market-derived quantity is an
input (no mu_open, no prices, no book columns). Panel features are strictly
prior-game (shift-then-ewm). Absence features are excluded: absent_ew_min /
absent_prior_ew_min condition on tonight's participation (the nba/ leakage
trap).

Stage C over the Stage B baseline (baseline preserved in git history;
numbers in PROGRESS.md):
- correct Normal continuity threshold floor(L)+0.5 (dist_utils uses L+0.5,
  half a count high on half-lines -- fine for market round-trips, a real
  p_over bias for a physical mu; this was Stage B's negative calibration)
- per-stat multiplicative bias, home factor, low-gp shrinkage and sigma(mu)
  all fit on the pre-eval-season panel ONLY (play data, no props):
  season S is scored with parameters fit on games before Jan 1 of S.

Usage: python3 src/fp_model.py            # dev season (2025) vs gates
       python3 src/fp_model.py --holdout  # score 2026 once (Stage C) + ROI
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd
from scipy import stats as sps

sys.path.insert(0, os.path.dirname(__file__))
from fp_benchmark import ll, clustered_t

ROOT = os.path.join(os.path.dirname(__file__), "..")

RAW = {"poi": "points", "reb": "rebounds", "ass": "assists",
       "tpm": "three_point_field_goals_made", "ste": "steals",
       "blo": "blocks", "tur": "turnovers"}
PARTS = {
    "points": ["poi"], "rebounds": ["reb"], "assists": ["ass"],
    "threes": ["tpm"], "steals": ["ste"], "blocks": ["blo"],
    "turnovers": ["tur"],
    "pra": ["poi", "reb", "ass"], "pts_reb": ["poi", "reb"],
    "pts_ast": ["poi", "ass"], "reb_ast": ["reb", "ass"],
    "stl_blk": ["ste", "blo"],
}
POISSON = {"threes", "steals", "blocks", "turnovers", "stl_blk"}
SCORING = {"poi", "tpm"}     # components also scaled by opponent defense
W_FAST = 0.6                 # fast-vs-slow EW blend (minutes, per-game)
W_RATE = 0.5                 # rate-x-minutes vs per-game blend
DEF_EXP = 0.5                # defense factor exponent
SHRINK_K = 3.0               # games of league-rate ballast on the rate path
FIT_FROM = "2010-01-01"      # calibration fit window start


def mu_stats(df, datecol, cal=None):
    """Per-stat adjusted means, one column per RAW key. Market-blind."""
    lg_pace = df.groupby(datecol).tm_pace_ew.transform("mean")
    lg_allow = df.groupby(datecol).opp_pts_against_ew.transform("mean")
    pace_f = ((df.tm_pace_ew + df.opp_pace_ew)
              / (df.tm_pace_ew + lg_pace)).fillna(1.0)
    def_f = ((df.opp_pts_against_ew / lg_allow) ** DEF_EXP).fillna(1.0)
    minutes = (W_FAST * df.min_ewf
               + (1 - W_FAST) * df.min_ews).fillna(df.min_ewf)

    out = pd.DataFrame(index=df.index)
    for st in RAW:
        per_game = df[f"{st}_ewf"]
        if f"{st}_ews" in df.columns:
            per_game = (W_FAST * df[f"{st}_ewf"]
                        + (1 - W_FAST) * df[f"{st}_ews"]).fillna(df[f"{st}_ewf"])
        rate = df[f"{st}_rate_ewf"]
        if cal is not None:  # shrink unstable early-career rates
            rate = ((df.gp * rate + SHRINK_K * cal["lg_rate"][st])
                    / (df.gp + SHRINK_K))
        rate_mu = rate * minutes
        mu = (W_RATE * per_game.fillna(rate_mu)
              + (1 - W_RATE) * rate_mu.fillna(per_game))
        mu = mu * pace_f * (def_f if st in SCORING else 1.0)
        if cal is not None:
            mu = mu * cal["c"][st] * cal["home"][st] ** (df.home - 0.5)
        out[st] = mu
    return out


def fit_play_cal(panel, cutoff):
    """Bias / home / shrinkage-ballast / sigma from play data before cutoff."""
    d = panel[(panel.game_date >= FIT_FROM) & (panel.game_date < cutoff)
              & (panel.gp >= 4) & panel.minutes.notna()].copy()
    d["home"] = d["home"].astype(float)
    cal = {"c": {}, "home": {}, "lg_rate": {}, "sigma": {}}
    for st, col in RAW.items():
        cal["lg_rate"][st] = d[col].sum() / d.minutes.sum()
    mus = mu_stats(d, "game_date", None)
    for st, col in RAW.items():
        ok = mus[st].notna() & d[col].notna()
        cal["c"][st] = d[col][ok].mean() / mus[st][ok].mean()
        h = ok & (d.home == 1)
        a = ok & (d.home == 0)
        ratio_h = d[col][h].mean() / mus[st][h].mean()
        ratio_a = d[col][a].mean() / mus[st][a].mean()
        cal["home"][st] = ratio_h / ratio_a
    # sigma^2 = a + b*mu per Normal market, against the CALIBRATED mu
    musc = mu_stats(d, "game_date", cal)
    for mkt, parts in PARTS.items():
        if mkt in POISSON:
            continue
        mu = sum(musc[p] for p in parts)
        act = sum(d[RAW[p]] for p in parts)
        ok = mu.notna() & act.notna()
        b, a = np.polyfit(mu[ok], (act[ok] - mu[ok]) ** 2, 1)
        cal["sigma"][mkt] = (max(a, 0.1), max(b, 0.2))
    return cal


def p_over(market, mu, line, cal):
    """P(actual > line). Over a line L means >= floor(L)+1 successes.

    Overdispersed markets use a 0.5/0.5 mixture of Normal (continuity
    threshold floor(L)+0.5) and Negative Binomial (mean mu, variance from
    the sigma fit): the Normal alone overstates P(over) on right-skewed
    counts, the NegBin alone understates it. The mixture was selected on
    pre-2025 play data with synthetic market-free lines (best LL on 3/5
    markets, |calibration| < 1.2pp everywhere) -- never on the dev season.
    """
    if market in POISSON:
        k = int(np.floor(line)) + 1
        return float(1.0 - sps.poisson.cdf(k - 1, max(mu, 1e-6)))
    a, b = cal["sigma"][market]
    var = max(a + b * max(mu, 0.0), 0.25)
    mu = max(mu, 1e-6)
    k = int(np.floor(line)) + 1
    pn = 1.0 - sps.norm.cdf(k - 0.5, mu, np.sqrt(var))
    if var <= mu * 1.001:
        pb = 1.0 - sps.poisson.cdf(k - 1, mu)
    else:
        r = mu * mu / (var - mu)
        pb = 1.0 - sps.nbinom.cdf(k - 1, r, r / (r + mu))
    return float(0.5 * pn + 0.5 * pb)


def predict(ms, cal):
    mus = mu_stats(ms, "date", cal)
    mu = pd.Series(np.nan, index=ms.index)
    for mkt, parts in PARTS.items():
        rows = ms.market == mkt
        if rows.any():
            mu[rows] = sum(mus.loc[rows, p] for p in parts)
    return mu


def american_dec(cost):
    c = np.asarray(cost, float)
    return np.where(c > 0, 1 + c / 100, 1 + 100 / np.maximum(-c, 1e-9))


def roi_sim(ev, label, p_col="p_model"):
    """Flat $1 at consensus opening prices where model EV exceeds threshold."""
    dec_o = american_dec(ev.open_over_cost)
    dec_u = american_dec(ev.open_under_cost)
    p = ev[p_col].values
    ev_over = p * dec_o - 1
    ev_under = (1 - p) * dec_u - 1
    side_over = ev_over >= ev_under
    edge = np.where(side_over, ev_over, ev_under)
    won = np.where(side_over, ev.over == 1, ev.over == 0)
    pnl = np.where(won, np.where(side_over, dec_o, dec_u) - 1, -1.0)
    print(f"ROI at consensus open, {label}:")
    for thr in (0.02, 0.05):
        take = edge > thr
        n = int(take.sum())
        if n == 0:
            print(f"  EV>{thr:.0%}: 0 bets")
            continue
        d, t = clustered_t(pnl[take], ev.date.values[take])
        n_over = int(side_over[take].sum())
        print(f"  EV>{thr:.0%}: n={n}  ROI={d*100:+.2f}% (clustered t={t:.1f})"
              f"  overs={n_over}/{n}")


def evaluate(ev, label):
    print(f"\n== {label}: n={len(ev)} "
          f"({ev.date.min()} .. {ev.date.max()}) ==")
    print(f"LL(model) = {ev.ll_model.mean():.5f}")
    print(f"LL(open)  = {ev.ll_open.mean():.5f}")
    d, t = clustered_t((ev.ll_model - ev.ll_open).values, ev.date)
    print(f"model - open = {d:+.5f} (clustered t={t:.1f})")
    cal = ev.p_model.mean() - ev.over.mean()
    print(f"calibration: mean P(over) {ev.p_model.mean():.4f} vs realized "
          f"{ev.over.mean():.4f} -> {cal*100:+.2f}pp")
    sl = ev[ev.coh_close & (ev.line_close == ev.open_line)].copy()
    if len(sl):
        sl["ll_close"] = ll(sl.p_close, sl.over)
        d2, t2 = clustered_t((sl.ll_model - sl.ll_close).values, sl.date)
        print(f"vs same-line close (n={len(sl)}): model - close = {d2:+.5f} "
              f"(clustered t={t2:.1f})  [tripwire: < -0.001 at |t|>3]")
    print("per market (model-open LL gap):")
    for m, sub in ev.groupby("market"):
        if len(sub) < 50:
            continue
        print(f"  {m:10s} n={len(sub):5d}  "
              f"gap={sub.ll_model.mean()-sub.ll_open.mean():+.5f}"
              f"  cal={100*(sub.p_model.mean()-sub.over.mean()):+.1f}pp")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout", action="store_true",
                    help="score the 2026 held-out season (Stage C, once)")
    args = ap.parse_args()

    panel = pd.read_pickle(os.path.join(ROOT, "data", "panel.pkl"))
    ms = pd.read_pickle(os.path.join(ROOT, "data", "modelset.pkl"))
    ms = ms[ms.matched & ~ms.void & ms.open_coherent
            & (ms.actual != ms.open_line)].copy()
    ms["over"] = (ms.actual > ms.open_line).astype(int)

    seasons = [2025] + ([2026] if args.holdout else [])
    for season in seasons:
        cal = fit_play_cal(panel, f"{season}-01-01")
        sub = ms[ms.season == season].copy()
        sub["mu_model"] = predict(sub, cal)
        cover = sub.mu_model.notna()
        print(f"\nseason {season}: params fit on pre-{season} play data; "
              f"coverage {cover.mean()*100:.1f}% "
              f"({(~cover).sum()} of {len(sub)} dropped)")
        evx = sub[cover].copy()
        evx["p_model"] = [p_over(m, mu, li, cal) for m, mu, li in
                          zip(evx.market, evx.mu_model, evx.open_line)]
        evx["ll_model"] = ll(evx.p_model, evx.over)
        evx["ll_open"] = ll(evx.p_open, evx.over)
        tag = ("dev season 2025" if season == 2025
               else "HELD-OUT season 2026 (scored once)")
        evaluate(evx, tag)
        roi_sim(evx, tag)
        # zero-skill placebo: the opener's own devigged probability
        roi_sim(evx.assign(p_model=evx.p_open), f"{tag} PLACEBO", "p_model")


if __name__ == "__main__":
    main()
