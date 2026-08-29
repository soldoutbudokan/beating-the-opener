"""From-scratch MLB pitcher-prop model (registration K, PROGRESS.md Market 6).

Two mu paths for the registered pitcher family {strikeouts, outs_recorded}:
  incumbent (default): the Market-4/5 Stage-B class — per-start EW blend
      0.6*{k,outs}_ewf + 0.4*{k,outs}_ews x (opp_so_pa_ew / slate-day league
      mean)^0.5, calibration (c, home) fit strictly pre-odds, Poisson (K) /
      Normal (outs). This is the same-data baseline K-G2 compares against.
  --talent: the talent engine (talent_mlb.py --build): mu_k = p_k x bf_hat,
      mu_outs = opb_hat x bf_hat, joined at scoring time by (nname, native_id)
      — no new column enters the modelset. K distribution = the registered
      choice between (a) Binomial(BF, p) mixed over BF ~ discretised
      Normal(bf_hat, sigma_bf) and (b) NegBin with r by threshold likelihood,
      selected on starts before 2021-01-01 with synthetic half-integer lines;
      outs = empirical conditional distribution given mu_outs (0.5-out
      buckets, kernel-smoothed), fit on starts before the eval season.
The consensus line is the scoring threshold only; no market input anywhere.

Gates as code: --holdout refuses to run unless the dev pooled cell gap is
<= 0.000 (the registered Stage-C spend condition).

Usage: python3 src/fp_model_mlb.py [--talent] [--disp] [--holdout]
"""
import argparse
import json
import os
import sys

import numpy as np
import pandas as pd
from scipy import stats as sps

sys.path.insert(0, os.path.dirname(__file__))
from fp_benchmark import ll, clustered_t, load_eval
from fp_model import american_dec, roi_sim
from fp_model_nhl import fit_r_threshold

ROOT = os.path.join(os.path.dirname(__file__), "..")
ODDS_START = "2025-03-18"
DIST_CUTOFF = "2021-01-01"
CELL = ["strikeouts", "outs_recorded"]
MKT = {"strikeouts": ("k", "k"), "outs_recorded": ("outs", "outs")}
W_FAST, FAC_EXP = 0.6, 0.5
BF_SD_GRID = np.arange(-12, 13)          # discretised BF offsets
OUTS_SEASONS = 4      # iteration 2: outs distribution window (seasons before eval)


# ------------------------------------------------------------ incumbent path
def mu_incumbent(df, datecol, st, cal, key):
    per_game = (W_FAST * df[f"{st}_ewf"] + (1 - W_FAST) * df[f"{st}_ews"]
                ).fillna(df[f"{st}_ewf"])
    lg = df.groupby(datecol)["opp_so_pa_ew"].transform("mean")
    fac = ((df["opp_so_pa_ew"] / lg) ** FAC_EXP).fillna(1.0)
    mu = per_game * fac
    if cal is not None:
        mu = mu * cal["c"][key] * cal["home"][key] ** (df.home - 0.5)
    return mu


def fit_cal_incumbent(panel, cutoff):
    cal = {"c": {}, "home": {}, "sigma": {}, "nb_r": {}}
    for mkt, (st, act) in MKT.items():
        d = panel[(panel.role == "pitcher") & (panel.game_date < cutoff)
                  & (panel.gp >= 4)].copy()
        d["home"] = d.home.astype(float)
        mu0 = mu_incumbent(d, "game_date", st, None, mkt)
        ok = mu0.notna() & d[act].notna()
        d, mu0 = d[ok], mu0[ok]
        cal["c"][mkt] = d[act].mean() / mu0.mean()
        mu1 = mu0 * cal["c"][mkt]
        h, a = d.home == 1, d.home == 0
        cal["home"][mkt] = ((d[act][h].mean() / mu1[h].mean())
                            / (d[act][a].mean() / mu1[a].mean()))
        muc = mu1 * cal["home"][mkt] ** (d.home - 0.5)
        resid2 = (d[act] - muc) ** 2
        if mkt == "outs_recorded":
            b, aa = np.polyfit(muc, resid2, 1)
            cal["sigma"][mkt] = (max(aa, 0.1), max(b, 0.2))
        else:
            excess = resid2.mean() - muc.mean()
            r = (muc ** 2).mean() / excess if excess > 0 else None
            cal["nb_r"][mkt] = None if (r is None or r > 200) else float(r)
    return cal


def p_over_incumbent(mkt, mu, line, cal):
    mu = max(mu, 1e-6)
    k = int(np.floor(line)) + 1
    if mkt == "outs_recorded":
        a, b = cal["sigma"][mkt]
        var = max(a + b * mu, 0.25)
        return float(1.0 - sps.norm.cdf(k - 0.5, mu, np.sqrt(var)))
    r = cal["nb_r"].get(mkt)
    if r is None:
        return float(1.0 - sps.poisson.cdf(k - 1, mu))
    return float(1.0 - sps.nbinom.cdf(k - 1, r, r / (r + mu)))


# ------------------------------------------------------------ talent path
def p_over_binmix(p, bf_hat, sd, line):
    """P(K > line): Binomial(BF, p) mixed over BF ~ discretised N(bf_hat, sd)."""
    k = int(np.floor(line)) + 1
    bfs = np.maximum(np.round(bf_hat) + BF_SD_GRID, 1)
    w = sps.norm.pdf(bfs, bf_hat, max(sd, 0.5))
    w = w / w.sum()
    tail = 1.0 - sps.binom.cdf(k - 1, bfs, p)
    return float((w * tail).sum())


def fit_sigma_bf(tal, cutoff):
    d = tal[(tal.date < cutoff) & tal.bf_hat.notna() & (tal.bf >= 5)]
    b, a = np.polyfit(d.bf_hat, (d.bf - d.bf_hat) ** 2, 1)
    return (float(max(a, 1.0)), float(max(b, 0.0)))


def sd_bf(sig, bf_hat):
    a, b = sig
    return float(np.sqrt(max(a + b * bf_hat, 1.0)))


def select_k_dist(tal, cutoff, sig):
    """Registered selection: threshold likelihood on starts < cutoff with
    synthetic half-integer lines floor(mu)+{-1,0,1}+0.5. Returns
    ("binmix", None) or ("nb", r)."""
    d = tal[(tal.date < cutoff) & tal.mu_k.notna() & (tal.bf >= 10)]
    mu = np.maximum(d.mu_k.to_numpy(float), 1e-6)
    act = d.k.to_numpy(float)
    r = fit_r_threshold(mu, act)
    ll_nb = ll_bin = 0.0
    p = d.p_k.to_numpy(float)
    bfh = d.bf_hat.to_numpy(float)
    for off in (-1.0, 0.0, 1.0):
        kf = np.maximum(np.floor(mu) + off, 0.0)
        over = (act > kf).astype(float)
        if r is None:
            pn = 1.0 - sps.poisson.cdf(kf, mu)
        else:
            pn = 1.0 - sps.nbinom.cdf(kf, r, r / (r + mu))
        pb = np.array([p_over_binmix(pi, bi, sd_bf(sig, bi), kf_i + 0.5)
                       for pi, bi, kf_i in zip(p, bfh, kf)])
        ll_nb += float(ll(pn, over).sum())
        ll_bin += float(ll(pb, over).sum())
    n = 3 * len(d)
    print(f"K distribution selection (< {cutoff}, n={len(d)} starts): "
          f"binmix LL={ll_bin/n:.5f}  negbin(r={r}) LL={ll_nb/n:.5f}")
    return ("binmix", None) if ll_bin <= ll_nb else ("nb", r)


class OutsDist:
    """Empirical P(outs > line | mu_outs bucket), kernel-smoothed, fit on
    starts strictly before `cutoff` (iteration 2: within the last
    `seasons` seasons before it, on the linearly recalibrated mu)."""
    def __init__(self, tal, cutoff, width=0.5, lin=None, seasons=None):
        d = tal[(tal.date < cutoff) & tal.mu_outs.notna() & (tal.bf >= 10)]
        if seasons is not None:
            lo = pd.Timestamp(cutoff).year - seasons
            d = d[d.season >= lo]
        mu = d.mu_outs if lin is None else lin[0] + lin[1] * d.mu_outs
        self.width = width
        self.b = np.round(mu / width).astype(int)
        self.outs = d.outs.to_numpy(int)
        self.tab = {}
        for b in np.unique(self.b):
            self.tab[b] = np.bincount(self.outs[self.b == b], minlength=30)
        self.n = len(d)

    def p_over(self, mu, line):
        b0 = int(np.round(mu / self.width))
        acc = np.zeros(30)
        for off, w in ((-1, 0.25), (0, 0.5), (1, 0.25)):
            c = self.tab.get(b0 + off)
            if c is not None:
                acc += w * c
        if acc.sum() < 30:       # sparse bucket: widen
            for off in range(-4, 5):
                c = self.tab.get(b0 + off)
                if c is not None:
                    acc += c
        k = int(np.floor(line)) + 1
        return float(acc[k:].sum() / acc.sum()) if acc.sum() > 0 else np.nan


def fit_cal_talent(tal, cutoff, lin=False):
    cal = {"c": {}, "home": {}, "lin": {}}
    d = tal[(tal.date < cutoff) & (tal.bf >= 10)]
    for mkt, (st, act) in MKT.items():
        mu = d[f"mu_{st}"]
        ok = mu.notna() & d[act].notna()
        dd, mu0 = d[ok], mu[ok]
        if lin:
            # iteration 2 (menu a): engine-aware linear mu recalibration,
            # actual ~ a + b*mu, fit strictly pre-odds
            b_, a_ = np.polyfit(mu0, dd[act], 1)
            cal["lin"][mkt] = (float(a_), float(b_))
            cal["c"][mkt] = 1.0
            mu1 = a_ + b_ * mu0
            print(f"  lin[{mkt}]: mu' = {a_:+.3f} + {b_:.3f}*mu")
        else:
            cal["c"][mkt] = dd[act].mean() / mu0.mean()
            mu1 = mu0 * cal["c"][mkt]
        h, a = dd.home == 1, dd.home == 0
        cal["home"][mkt] = ((dd[act][h].mean() / mu1[h].mean())
                            / (dd[act][a].mean() / mu1[a].mean()))
        print(f"  talent cal[{mkt}]: c={cal['c'][mkt]:.4f} home={cal['home'][mkt]:.4f} "
              f"(n={int(ok.sum())} pre-{cutoff} starts)")
    return cal


def apply_cal(cal, mkt, mu, home):
    if mkt in cal.get("lin", {}):
        a_, b_ = cal["lin"][mkt]
        mu = a_ + b_ * mu
    else:
        mu = mu * cal["c"][mkt]
    return np.maximum(mu * cal["home"][mkt] ** (home - 0.5), 0.05)


# ------------------------------------------------------------ scoring
def price_rows(g, lines_col, args, cal, tal_dist):
    """P(actual > line) per row for the given line column."""
    out = np.full(len(g), np.nan)
    for i, (mkt, mu, li, home) in enumerate(zip(g.market, g.mu_model, g[lines_col], g.home)):
        if not np.isfinite(mu) or not np.isfinite(li):
            continue
        if not args.talent:
            out[i] = p_over_incumbent(mkt, mu, li, cal)
            continue
        if mkt == "strikeouts":
            kind, r, sig = tal_dist["k"]
            if kind == "binmix":
                p = g.p_k.iat[i] * cal["c"][mkt] * cal["home"][mkt] ** (home - 0.5)
                out[i] = p_over_binmix(min(p, 0.95), g.bf_hat.iat[i],
                                       sd_bf(sig, g.bf_hat.iat[i]), li)
            else:
                k = int(np.floor(li)) + 1
                out[i] = (float(1.0 - sps.poisson.cdf(k - 1, mu)) if r is None
                          else float(1.0 - sps.nbinom.cdf(k - 1, r, r / (r + mu))))
        else:
            out[i] = tal_dist["outs"].p_over(mu, li)
    return out


def fd_cell(evx, label):
    """The venue question: rows with a coherent FanDuel quote, priced at
    FD's close line/price; EV tiers 5%/10%; clustered t; placebo = p_fd."""
    d = evx[evx.coh_fd & evx.line_fd.notna() & (evx.actual != evx.line_fd)].copy()
    if not len(d):
        print(f"FD cell {label}: no rows"); return
    d["over_fd"] = (d.actual > d.line_fd).astype(int)
    for pcol, tag in (("p_model_fd", "model"), ("p_fd", "PLACEBO (FD's own devigged p)")):
        p = d[pcol].to_numpy(float)
        dec_o, dec_u = american_dec(d.oc_fd), american_dec(d.uc_fd)
        ev_o, ev_u = p * dec_o - 1, (1 - p) * dec_u - 1
        so = ev_o >= ev_u
        edge = np.where(so, ev_o, ev_u)
        won = np.where(so, d.over_fd == 1, d.over_fd == 0)
        pnl = np.where(won, np.where(so, dec_o, dec_u) - 1, -1.0)
        print(f"FD cell at FD close ({label}, {tag}; n_rows={len(d)}, FD-sourced open "
              f"{(d.open_book == 10).mean():.0%}):")
        for thr in (0.05, 0.10):
            take = np.isfinite(edge) & (edge > thr)
            n = int(take.sum())
            if n == 0:
                print(f"  EV>{thr:.0%}: 0 bets"); continue
            m, t = clustered_t(pnl[take], d.date.values[take])
            print(f"  EV>{thr:.0%}: n={n}  ROI={m*100:+.2f}% (clustered t={t:.1f})  "
                  f"overs={int(so[take].sum())}/{n}  mean claimed EV={edge[take].mean()*100:+.1f}%")


def evaluate(evx, name, tripwire_only=False):
    evx["ll_model"] = ll(evx.p_model, evx.over)
    evx["ll_open"] = ll(evx.p_open, evx.over)
    d, t = clustered_t((evx.ll_model - evx.ll_open).values, evx.date)
    print(f"LL(model)={evx.ll_model.mean():.5f}  LL(open)={evx.ll_open.mean():.5f}  "
          f"model-open={d:+.5f} (clustered t={t:.1f})  n={len(evx)}")
    print(f"calibration: {100*(evx.p_model.mean()-evx.over.mean()):+.2f}pp")
    sl = evx[evx.coh_close & (evx.line_close == evx.open_line)].copy()
    if len(sl):
        sl["ll_close"] = ll(sl.p_close, sl.over)
        d2, t2 = clustered_t((sl.ll_model - sl.ll_close).values, sl.date)
        flag = "  ** TRIPWIRE: investigate leakage **" if (d2 < -0.001 and abs(t2) > 3) else ""
        print(f"vs same-line close (n={len(sl)}): {d2:+.5f} (t={t2:.1f}){flag}")
    for m, g in evx.groupby("market"):
        dm, tm = clustered_t((g.ll_model - g.ll_open).values, g.date)
        print(f"  {m:14s} n={len(g):5d} gap={dm:+.5f} (t={tm:.1f}) "
              f"cal={100*(g.p_model.mean()-g.over.mean()):+.1f}pp")
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--talent", action="store_true")
    ap.add_argument("--holdout", action="store_true")
    ap.add_argument("--markets", nargs="*", default=CELL)
    ap.add_argument("--recal", action="store_true",
                    help="iteration 2 (menu a): linear mu recalibration fit "
                         "pre-odds, outs distribution on the last OUTS_SEASONS "
                         "seasons before the eval season, NegBin r refit on "
                         "the recalibrated mu (requires --talent)")
    args = ap.parse_args()
    if args.recal and not args.talent:
        raise SystemExit("--recal requires --talent")

    panel = pd.read_pickle(os.path.join(ROOT, "data", "panel_mlb.pkl"))
    panel["game_date"] = pd.to_datetime(panel.date).dt.strftime("%Y-%m-%d")
    ev = load_eval("mlb", args.markets)
    ev["home"] = ev.home.astype(float)
    tal_dist, cal = {}, None
    if args.talent:
        tal = pd.read_pickle(os.path.join(ROOT, "data", "talent_mlb.pkl"))
        params = json.load(open(os.path.join(ROOT, "data", "talent_mlb_params.json")))
        print(f"talent params: {params}")
        tcols = ["kr_hat", "opb_hat", "bf_hat", "lineup_br", "park", "p_k", "mu_k", "mu_outs"]
        key = panel[panel.role == "pitcher"][["pid", "native_id", "nname"]].drop_duplicates()
        tmap = tal.merge(key, left_on=["pid", "gamePk"], right_on=["pid", "native_id"])[
            ["nname", "native_id"] + tcols].drop_duplicates(["nname", "native_id"])
        ev = ev.merge(tmap, on=["nname", "native_id"], how="left")
        print(f"talent join coverage: {ev.mu_k.notna().mean():.2%} of {len(ev)} eval rows")
        cal = fit_cal_talent(tal, ODDS_START, lin=args.recal)
        sig = fit_sigma_bf(tal, DIST_CUTOFF)
        kind, r = select_k_dist(tal, DIST_CUTOFF, sig)
        if args.recal:
            kind = "nb"        # the recalibrated mu feeds the count family
        if kind == "nb":
            pre = tal[(tal.date < ODDS_START) & (tal.bf >= 10) & tal.mu_k.notna() & tal.k.notna()]
            mu_pre = apply_cal(cal, "strikeouts", pre.mu_k.to_numpy(float), pre.home.to_numpy(float))
            r = fit_r_threshold(np.maximum(mu_pre, 1e-6), pre.k.to_numpy(float))
            print(f"  negbin r refit pre-odds: {r}")
        tal_dist["k"] = (kind, r, sig)
        print(f"  sigma_bf: var = {sig[0]:.2f} + {sig[1]:.3f}*bf_hat  -> K dist = {kind}")
    else:
        cal = fit_cal_incumbent(panel, ODDS_START)
        print(f"incumbent cal: c={ {k: round(v, 4) for k, v in cal['c'].items()} } "
              f"home={ {k: round(v, 4) for k, v in cal['home'].items()} } nb_r={cal['nb_r']}")

    dev_gap = None
    for name in ["dev"] + (["holdout"] if args.holdout else []):
        if name == "holdout":
            if dev_gap is None or dev_gap > 0.0:
                print("\nHOLDOUT REFUSED: registered spend condition (dev pooled cell gap "
                      f"<= 0.000) not met (dev gap {dev_gap:+.5f}). Holdout stays unspent.")
                break
            print("\n*** HOLDOUT (2026) — scored ONCE per registration K ***")
        sub = ev[ev.split == name].copy()
        if args.talent:
            cutoff = ODDS_START if name == "dev" else "2026-01-01"
            tal_dist["outs"] = OutsDist(tal, cutoff,
                                        lin=cal["lin"].get("outs_recorded") if args.recal else None,
                                        seasons=OUTS_SEASONS if args.recal else None)
            print(f"  outs distribution fit on {tal_dist['outs'].n} starts before {cutoff}")
            sub["mu_model"] = np.where(sub.market == "strikeouts",
                                       apply_cal(cal, "strikeouts", sub.mu_k.to_numpy(float), sub.home.to_numpy(float)),
                                       apply_cal(cal, "outs_recorded", sub.mu_outs.to_numpy(float), sub.home.to_numpy(float)))
        else:
            sub["mu_model"] = np.nan
            for mkt, (st, act) in MKT.items():
                rows = sub.market == mkt
                if rows.any():
                    sub.loc[rows, "mu_model"] = mu_incumbent(sub[rows], "date", st, cal, mkt)
        sub["p_model"] = price_rows(sub, "open_line", args, cal, tal_dist)
        sub["p_model_fd"] = price_rows(sub, "line_fd", args, cal, tal_dist)
        evx = sub[sub.p_model.notna()].copy()
        mode = ("talent + recal (iteration 2)" if args.recal else
                "talent (iteration 1)" if args.talent else "incumbent EW blend")
        print(f"\n== {name} [{mode}]: coverage {len(evx)/len(sub)*100:.1f}% of {len(sub)} ==")
        gap = evaluate(evx, name)
        if name == "dev":
            dev_gap = gap
        tag = name if name == "dev" else "HOLDOUT (scored once)"
        roi_sim(evx, tag)
        roi_sim(evx.assign(p_model=evx.p_open), f"{tag} PLACEBO", "p_model")
        fd_cell(evx, tag)
        nomove = evx.coh_close & (evx.line_close == evx.open_line) & (evx.oc_close == evx.open_over_cost)
        print(f"no-move share (line and price unchanged at close): {nomove.mean():.1%}")


if __name__ == "__main__":
    main()
