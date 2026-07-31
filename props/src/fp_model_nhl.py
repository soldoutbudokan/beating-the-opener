"""From-scratch NHL prop model (fp programme, PROGRESS.md Market 4).

Skater markets (shots/points/assists/goals/blocked) and goalie saves.
mu = blended per-game EW x opponent factor:
  goals/points/assists <- opponent EW goals allowed (opp_ga_ew)
  shots                <- opponent EW shots allowed (opp_sa_ew)
  blocked              <- opponent EW shots generated (opp_sf_ew)
  saves                <- opponent EW shots generated (opp_sf_ew)
all as ratios vs the slate-day league mean, exponent 0.5. The line is the
scoring threshold only; no market-derived inputs anywhere.

Calibration (per-stat bias, home factor, sigma or NegBin r vs the model's
own mu) fit on the PRE-ODDS panel only (the 2024-25 season; odds start
2025-10-07). Distributions: NegBin (Poisson fallback) for counts, Normal
for saves — same mixture-free discrete handling as the registered NHL
families.

Usage: python3 src/fp_model_nhl.py [--expanding] [--holdout]
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd
from scipy import stats as sps

sys.path.insert(0, os.path.dirname(__file__))
from fp_benchmark import ll, clustered_t, load_eval, DEV_END
from fp_model import american_dec, roi_sim

ROOT = os.path.join(os.path.dirname(__file__), "..")

# market -> (role, ew stat, actual col, opponent factor col)
MKT = {
    "goals": ("skater", "g", "g", "opp_ga_ew"),
    "assists": ("skater", "a", "a", "opp_ga_ew"),
    "points": ("skater", "p", "p", "opp_ga_ew"),
    "shots": ("skater", "sog", "sog", "opp_sa_ew"),
    "blocked_shots": ("skater", "blk", "blk", "opp_sf_ew"),
    "saves": ("goalie", "sv", "sv", "opp_sf_ew"),
}
NORMAL = {"saves"}
W_FAST = 0.6
FAC_EXP = 0.5
ODDS_START = "2025-10-07"


def mu_one(df, datecol, st, fac_col, cal, key):
    per_game = (W_FAST * df[f"{st}_ewf"]
                + (1 - W_FAST) * df[f"{st}_ews"]).fillna(df[f"{st}_ewf"])
    lg = df.groupby(datecol)[fac_col].transform("mean")
    fac = ((df[fac_col] / lg) ** FAC_EXP).fillna(1.0)
    mu = per_game * fac
    if cal is not None:
        mu = mu * cal["c"][key] * cal["home"][key] ** (df.home - 0.5)
    return mu


def fit_play_cal(panel, cutoff):
    cal = {"c": {}, "home": {}, "sigma": {}, "nb_r": {}}
    for mkt, (role, st, act, fac) in MKT.items():
        d = panel[(panel.role == role) & (panel.game_date < cutoff)
                  & (panel.gp >= 4)].copy()
        d["home"] = d.home.astype(float)
        mu0 = mu_one(d, "game_date", st, fac, None, mkt)
        ok = mu0.notna() & d[act].notna()
        d, mu0 = d[ok], mu0[ok]
        cal["c"][mkt] = d[act].mean() / mu0.mean()
        h, a = d.home == 1, d.home == 0
        cal["home"][mkt] = ((d[act][h].mean() / mu0[h].mean())
                            / (d[act][a].mean() / mu0[a].mean()))
        muc = mu0 * cal["c"][mkt] * cal["home"][mkt] ** (d.home - 0.5)
        resid2 = (d[act] - muc) ** 2
        if mkt in NORMAL:
            b, aa = np.polyfit(muc, resid2, 1)
            cal["sigma"][mkt] = (max(aa, 0.1), max(b, 0.2))
        else:
            # method of moments: var = mu + mu^2/r
            excess = resid2.mean() - muc.mean()
            r = (muc**2).mean() / excess if excess > 0 else None
            cal["nb_r"][mkt] = None if (r is None or r > 200) else float(r)
    return cal


def p_over(mkt, mu, line, cal):
    mu = max(mu, 1e-6)
    k = int(np.floor(line)) + 1
    if mkt in NORMAL:
        a, b = cal["sigma"][mkt]
        var = max(a + b * mu, 0.25)
        pn = 1.0 - sps.norm.cdf(k - 0.5, mu, np.sqrt(var))
        if var <= mu * 1.001:
            pb = 1.0 - sps.poisson.cdf(k - 1, mu)
        else:
            r = mu * mu / (var - mu)
            pb = 1.0 - sps.nbinom.cdf(k - 1, r, r / (r + mu))
        return float(0.5 * pn + 0.5 * pb)
    r = cal["nb_r"].get(mkt)
    if r is None:
        return float(1.0 - sps.poisson.cdf(k - 1, mu))
    return float(1.0 - sps.nbinom.cdf(k - 1, r, r / (r + mu)))


def score(sub, panel, expanding):
    def one(grp, cutoff):
        cal = fit_play_cal(panel, cutoff)
        g = grp.copy()
        g["mu_model"] = np.nan
        for mkt, (role, st, act, fac) in MKT.items():
            rows = g.market == mkt
            if rows.any():
                g.loc[rows, "mu_model"] = mu_one(
                    g[rows], "date", st, fac, cal, mkt)
        g = g[g.mu_model.notna()].copy()
        g["p_model"] = [p_over(m, mu, li, cal) for m, mu, li in
                        zip(g.market, g.mu_model, g.open_line)]
        return g
    if not expanding:
        return one(sub, ODDS_START)
    parts = []
    for wk, grp in sub.groupby(pd.to_datetime(sub.date)
                               .dt.to_period("W").dt.start_time):
        parts.append(one(grp, wk.strftime("%Y-%m-%d")))
    return pd.concat(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout", action="store_true")
    ap.add_argument("--expanding", action="store_true")
    args = ap.parse_args()

    panel = pd.read_pickle(os.path.join(ROOT, "data", "panel_nhl.pkl"))
    panel["game_date"] = pd.to_datetime(panel.date).dt.strftime("%Y-%m-%d")
    ev = load_eval("nhl")
    mode = "expanding-weekly" if args.expanding else "frozen pre-odds"
    for name in ["dev"] + (["holdout"] if args.holdout else []):
        sub = ev[ev.split == name].copy()
        evx = score(sub, panel, args.expanding)
        print(f"\n{name} [{mode}]: coverage {len(evx)/len(sub)*100:.1f}%")
        evx["ll_model"] = ll(evx.p_model, evx.over)
        evx["ll_open"] = ll(evx.p_open, evx.over)
        d, t = clustered_t((evx.ll_model - evx.ll_open).values, evx.date)
        print(f"LL(model)={evx.ll_model.mean():.5f}  "
              f"LL(open)={evx.ll_open.mean():.5f}  "
              f"model-open={d:+.5f} (clustered t={t:.1f})")
        print(f"calibration: {100*(evx.p_model.mean()-evx.over.mean()):+.2f}pp")
        sl = evx[evx.coh_close & (evx.line_close == evx.open_line)].copy()
        if len(sl):
            sl["ll_close"] = ll(sl.p_close, sl.over)
            d2, t2 = clustered_t((sl.ll_model - sl.ll_close).values, sl.date)
            print(f"vs same-line close (n={len(sl)}): {d2:+.5f} (t={t2:.1f})")
        for m, g in evx.groupby("market"):
            print(f"  {m:14s} n={len(g):5d} "
                  f"gap={g.ll_model.mean()-g.ll_open.mean():+.5f} "
                  f"cal={100*(g.p_model.mean()-g.over.mean()):+.1f}pp")
        tag = name if name == "dev" else "HOLDOUT (scored once)"
        roi_sim(evx, tag)
        roi_sim(evx.assign(p_model=evx.p_open), f"{tag} PLACEBO", "p_model")


if __name__ == "__main__":
    main()
