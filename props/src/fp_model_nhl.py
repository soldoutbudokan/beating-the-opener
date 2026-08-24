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
# registration N (PROGRESS.md Market 4 revisit): the markets whose mu path
# switches to the talent engine under --talent = the registered cell (the
# subset of {shots, blocked_shots} that passed N-G1). All other markets
# keep the incumbent path unconditionally.
TALENT_MKTS = {"shots": "sog", "blocked_shots": "blk"}


def mu_one(df, datecol, st, fac_col, cal, key, wr=None):
    per_game = (W_FAST * df[f"{st}_ewf"]
                + (1 - W_FAST) * df[f"{st}_ews"]).fillna(df[f"{st}_ewf"])
    if wr is not None and key in wr and f"talent_{st}" in df.columns:
        toi_b = (W_FAST * df.toi_min_ewf
                 + (1 - W_FAST) * df.toi_min_ews).fillna(df.toi_min_ewf)
        rate_mu = df[f"talent_{st}"] * toi_b
        w = wr[key]
        core = (w * per_game.fillna(rate_mu)
                + (1 - w) * rate_mu.fillna(per_game))
    else:
        core = per_game
    lg = df.groupby(datecol)[fac_col].transform("mean")
    fac = ((df[fac_col] / lg) ** FAC_EXP).fillna(1.0)
    mu = core * fac
    if cal is not None:
        if key in cal.get("lin", {}):
            a_, b_ = cal["lin"][key]
            mu = np.maximum(a_ + b_ * mu, 0.05)
        else:
            mu = mu * cal["c"][key]
        mu = mu * cal["home"][key] ** (df.home - 0.5)
    return mu


def fit_w_rate(panel, cutoff):
    """Registration N: per-market blend weight between the incumbent
    per-game EW and talent_rate x toi_blend, fit STRICTLY pre-odds
    (rows < cutoff, the fit_play_cal filter)."""
    wr = {}
    for mkt, (role, st, act, fac) in MKT.items():
        if mkt not in TALENT_MKTS:
            continue
        d = panel[(panel.role == role) & (panel.game_date < cutoff)
                  & (panel.gp >= 4)]
        per_game = (W_FAST * d[f"{st}_ewf"]
                    + (1 - W_FAST) * d[f"{st}_ews"]).fillna(d[f"{st}_ewf"])
        toi_b = (W_FAST * d.toi_min_ewf
                 + (1 - W_FAST) * d.toi_min_ews).fillna(d.toi_min_ewf)
        rate_mu = d[f"talent_{st}"] * toi_b
        ok = per_game.notna() & rate_mu.notna() & d[act].notna()
        act_v = d[act][ok]
        best = min((float(((w * per_game[ok] + (1 - w) * rate_mu[ok]
                            - act_v) ** 2).mean()), round(w, 2))
                   for w in np.arange(0.0, 1.0001, 0.05))
        wr[mkt] = best[1]
        print(f"  w_rate[{mkt}] = {best[1]:.2f} "
              f"(pre-odds mse {best[0]:.4f}, n={int(ok.sum())})")
    return wr


def fit_play_cal(panel, cutoff, wr=None, lin=False):
    cal = {"c": {}, "home": {}, "sigma": {}, "nb_r": {}, "lin": {}}
    for mkt, (role, st, act, fac) in MKT.items():
        d = panel[(panel.role == role) & (panel.game_date < cutoff)
                  & (panel.gp >= 4)].copy()
        d["home"] = d.home.astype(float)
        mu0 = mu_one(d, "game_date", st, fac, None, mkt, wr)
        ok = mu0.notna() & d[act].notna()
        d, mu0 = d[ok], mu0[ok]
        if lin and wr is not None and mkt in TALENT_MKTS:
            # registration N iteration 2: engine-aware linear mu
            # recalibration (actual ~ a + b*mu), fit strictly pre-odds
            b_, a_ = np.polyfit(mu0, d[act], 1)
            cal["lin"][mkt] = (float(a_), float(b_))
            cal["c"][mkt] = 1.0
            print(f"  lin[{mkt}]: mu' = {a_:+.3f} {b_:+.3f}*mu")
            mu1 = np.maximum(a_ + b_ * mu0, 0.05)
        else:
            cal["c"][mkt] = d[act].mean() / mu0.mean()
            mu1 = mu0 * cal["c"][mkt]
        h, a = d.home == 1, d.home == 0
        cal["home"][mkt] = ((d[act][h].mean() / mu1[h].mean())
                            / (d[act][a].mean() / mu1[a].mean()))
        muc = mu1 * cal["home"][mkt] ** (d.home - 0.5)
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


def score(sub, panel, expanding, wr=None, lin=False):
    def one(grp, cutoff):
        cal = fit_play_cal(panel, cutoff, wr, lin)
        g = grp.copy()
        g["mu_model"] = np.nan
        for mkt, (role, st, act, fac) in MKT.items():
            rows = g.market == mkt
            if rows.any():
                g.loc[rows, "mu_model"] = mu_one(
                    g[rows], "date", st, fac, cal, mkt, wr)
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
    ap.add_argument("--talent", action="store_true",
                    help="registration N: talent-engine mu path for the "
                         "registered cell (TALENT_MKTS)")
    ap.add_argument("--lin", action="store_true",
                    help="registration N iteration 2: engine-aware linear "
                         "mu recalibration, fit pre-odds (requires --talent)")
    args = ap.parse_args()
    if args.lin and not args.talent:
        raise SystemExit("--lin requires --talent")
    if args.talent and args.expanding:
        raise SystemExit("registration N: talent-path constants are fit "
                         "strictly pre-odds; --expanding is not allowed "
                         "with --talent")

    panel = pd.read_pickle(os.path.join(ROOT, "data", "panel_nhl.pkl"))
    panel["game_date"] = pd.to_datetime(panel.date).dt.strftime("%Y-%m-%d")
    ev = load_eval("nhl")
    wr = None
    if args.talent:
        tal = pd.read_pickle(os.path.join(ROOT, "data", "talent_nhl.pkl"))
        tal_cols = [c for c in tal.columns if c.startswith("talent_")]
        panel = panel.merge(tal, on=["pid", "game_id"], how="left")
        tmap = (panel[panel.role == "skater"]
                .groupby(["nname", "native_id"])[tal_cols].max()
                .reset_index())
        ev = ev.merge(tmap, on=["nname", "native_id"], how="left")
        cov = ev[ev.market.isin(TALENT_MKTS)]["talent_sog"].notna().mean()
        print(f"talent join coverage on cell markets: {cov:.2%}")
        wr = fit_w_rate(panel, ODDS_START)
    mode = ("talent " if args.talent else "") + (
        "lin-recal " if args.lin else "") + (
        "expanding-weekly" if args.expanding else "frozen pre-odds")
    for name in ["dev"] + (["holdout"] if args.holdout else []):
        sub = ev[ev.split == name].copy()
        evx = score(sub, panel, args.expanding, wr, args.lin)
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
