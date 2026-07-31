"""From-scratch NBA prop model (fp programme, PROGRESS.md Market 5).

Port of wnba/src/fp_model.py: per-stat means from blended per-game EWs with
opponent pace/defense factors, per-market distributions -> P(over) at the
quoted line. The line is the scoring threshold only; no market-derived
quantity is an input. The props panel carries no per-minute rate columns,
so mu comes from the per-game EW blend alone (fast/slow), which already
embeds minutes.

Calibration (per-stat bias, home factor, sigma vs the model's own mu) is
fit on the PRE-ODDS panel only (seasons 2023-24 and 2024-25; the odds
archive starts 2025-10-21). Distributions: Poisson for threes/steals/
blocks; 0.5/0.5 Normal+NegBin mixture for the rest (the WNBA selection,
carried over as a fixed prior choice, not re-tuned).

--expanding refits the calibration weekly on an expanding window (counts
as the one allowed iteration switch per the registered gates).

Usage: python3 src/fp_model.py [--expanding]     # dev vs gates
       python3 src/fp_model.py [--expanding] --holdout  # + holdout ONCE
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd
from scipy import stats as sps

sys.path.insert(0, os.path.dirname(__file__))
from fp_benchmark import ll, clustered_t, load_eval, DEV_END

ROOT = os.path.join(os.path.dirname(__file__), "..")

RAW = {"pts": "points", "reb": "rebounds", "ast": "assists",
       "tpm": "three_point_field_goals_made", "stl": "steals",
       "blk": "blocks"}
PARTS = {
    "points": ["pts"], "rebounds": ["reb"], "assists": ["ast"],
    "threes": ["tpm"], "steals": ["stl"], "blocks": ["blk"],
    "pra": ["pts", "reb", "ast"], "pts_reb": ["pts", "reb"],
    "pts_ast": ["pts", "ast"], "reb_ast": ["reb", "ast"],
}
POISSON = {"threes", "steals", "blocks"}
SCORING = {"pts", "tpm"}
W_FAST = 0.6
DEF_EXP = 0.5
ODDS_START = "2025-10-21"


def mu_stats(df, datecol, cal=None):
    lg_pace = df.groupby(datecol).tm_pace_ew.transform("mean")
    lg_allow = df.groupby(datecol).opp_pts_against_ew.transform("mean")
    pace_f = ((df.tm_pace_ew + df.opp_pace_ew)
              / (df.tm_pace_ew + lg_pace)).fillna(1.0)
    def_f = ((df.opp_pts_against_ew / lg_allow) ** DEF_EXP).fillna(1.0)
    out = pd.DataFrame(index=df.index)
    for st in RAW:
        per_game = df[f"{st}_ewf"]
        if f"{st}_ews" in df.columns:
            per_game = (W_FAST * df[f"{st}_ewf"]
                        + (1 - W_FAST) * df[f"{st}_ews"]).fillna(df[f"{st}_ewf"])
        mu = per_game * pace_f * (def_f if st in SCORING else 1.0)
        if cal is not None:
            mu = mu * cal["c"][st] * cal["home"][st] ** (df.home - 0.5)
        out[st] = mu
    return out


def fit_play_cal(panel, cutoff):
    d = panel[(panel.game_date < cutoff) & (panel.gp >= 4)
              & panel.minutes.notna()].copy()
    d["home"] = d["home"].astype(float)
    cal = {"c": {}, "home": {}, "sigma": {}}
    mus = mu_stats(d, "game_date", None)
    for st, col in RAW.items():
        ok = mus[st].notna() & d[col].notna()
        cal["c"][st] = d[col][ok].mean() / mus[st][ok].mean()
        h, a = ok & (d.home == 1), ok & (d.home == 0)
        cal["home"][st] = ((d[col][h].mean() / mus[st][h].mean())
                           / (d[col][a].mean() / mus[st][a].mean()))
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
    dec_o = american_dec(ev.open_over_cost)
    dec_u = american_dec(ev.open_under_cost)
    p = ev[p_col].values
    ev_over, ev_under = p * dec_o - 1, (1 - p) * dec_u - 1
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
        print(f"  EV>{thr:.0%}: n={n}  ROI={d*100:+.2f}% (clustered t={t:.1f})"
              f"  overs={int(side_over[take].sum())}/{n}")


def evaluate(ev, label):
    print(f"\n== {label}: n={len(ev)} ({ev.date.min()} .. {ev.date.max()}) ==")
    print(f"LL(model)={ev.ll_model.mean():.5f}  LL(open)={ev.ll_open.mean():.5f}")
    d, t = clustered_t((ev.ll_model - ev.ll_open).values, ev.date)
    print(f"model - open = {d:+.5f} (clustered t={t:.1f})")
    print(f"calibration: {100*(ev.p_model.mean()-ev.over.mean()):+.2f}pp")
    sl = ev[ev.coh_close & (ev.line_close == ev.open_line)].copy()
    if len(sl):
        sl["ll_close"] = ll(sl.p_close, sl.over)
        d2, t2 = clustered_t((sl.ll_model - sl.ll_close).values, sl.date)
        print(f"vs same-line close (n={len(sl)}): {d2:+.5f} (t={t2:.1f})  "
              f"[tripwire: < -0.001 at |t|>3]")
    for m, sub in ev.groupby("market"):
        print(f"  {m:10s} n={len(sub):5d}  "
              f"gap={sub.ll_model.mean()-sub.ll_open.mean():+.5f}"
              f"  cal={100*(sub.p_model.mean()-sub.over.mean()):+.1f}pp")


def score(sub, panel, expanding):
    if expanding:
        parts = []
        for wk, grp in sub.groupby(pd.to_datetime(sub.date)
                                   .dt.to_period("W").dt.start_time):
            cal = fit_play_cal(panel, wk.strftime("%Y-%m-%d"))
            g = grp.copy()
            g["mu_model"] = predict(g, cal)
            g = g[g.mu_model.notna()].copy()
            g["p_model"] = [p_over(m, mu, li, cal) for m, mu, li in
                            zip(g.market, g.mu_model, g.open_line)]
            parts.append(g)
        return pd.concat(parts)
    cal = fit_play_cal(panel, ODDS_START)
    g = sub.copy()
    g["mu_model"] = predict(g, cal)
    g = g[g.mu_model.notna()].copy()
    g["p_model"] = [p_over(m, mu, li, cal) for m, mu, li in
                    zip(g.market, g.mu_model, g.open_line)]
    return g


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout", action="store_true")
    ap.add_argument("--expanding", action="store_true")
    args = ap.parse_args()

    panel = pd.read_pickle(os.path.join(ROOT, "data", "panel_nba.pkl"))
    ev = load_eval("nba")
    mode = "expanding-weekly" if args.expanding else "frozen pre-odds"
    for name in ["dev"] + (["holdout"] if args.holdout else []):
        sub = ev[ev.split == name].copy()
        evx = score(sub, panel, args.expanding)
        print(f"\n{name} [{mode}]: coverage {len(evx)/len(sub)*100:.1f}%")
        evx["ll_model"] = ll(evx.p_model, evx.over)
        evx["ll_open"] = ll(evx.p_open, evx.over)
        tag = ("dev (through " + DEV_END + ")" if name == "dev"
               else "HOLDOUT (scored once)")
        evaluate(evx, tag)
        roi_sim(evx, tag)
        roi_sim(evx.assign(p_model=evx.p_open), f"{tag} PLACEBO", "p_model")
        if name == "holdout" and "post" in evx.columns:
            for flag, g in evx.groupby(evx.post.fillna(0) > 0):
                d, t = clustered_t((g.ll_model - g.ll_open).values, g.date)
                print(f"  {'playoffs' if flag else 'regular season'}: "
                      f"n={len(g)} gap={d:+.5f} (t={t:.1f})")


if __name__ == "__main__":
    main()
