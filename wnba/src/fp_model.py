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

fp-v2 (2026-07-31 revisit, PROGRESS.md "Market 1 revisit"): adds
- --expanding: in-season WEEKLY expanding recalibration — for each eval
  week, c/home/sigma/lg_rate refit on all panel games before that week
  (strictly walk-forward; addresses the 2026 calibration drift)
- presumed-absent availability: teammates who missed the team's previous
  game (computable from prior games only — unlike absent_ew_min, which
  reads tonight's box score) boost the remaining players' minutes via a
  factor fit on the pre-eval panel
- threes get an opponent-3PA-allowed defense factor

Usage: python3 src/fp_model.py                 # v1, dev season vs gates
       python3 src/fp_model.py --expanding     # v2, dev season
       python3 src/fp_model.py --holdout       # + 2026 (Stage C was spent
           2026-07-31: any 2026 rerun is POST-HOC diagnostic, and prints so)
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


def presumed_absent(panel):
    """Strictly-prior availability: for each played panel row, the EW
    minutes of teammates who played the team's game before last but missed
    the most recent one (>= 10 EW min), excluding the row's own player.
    Uses prior games only — never tonight's box score."""
    d = panel[panel.minutes.notna()][
        ["team_id", "game_id", "game_date", "athlete_id", "min_ewf"]].copy()
    tg = (d[["team_id", "game_id", "game_date"]].drop_duplicates()
          .sort_values(["team_id", "game_date"]))
    played = d.groupby(["team_id", "game_id"]).athlete_id.agg(set).to_dict()
    minew = d.set_index(["athlete_id", "game_id"]).min_ewf.to_dict()
    team_tot, own = {}, {}
    for team, games in tg.groupby("team_id"):
        gids = games.game_id.tolist()
        for i in range(2, len(gids)):
            outs = (played.get((team, gids[i - 2]), set())
                    - played.get((team, gids[i - 1]), set()))
            contrib = {p: minew.get((p, gids[i - 2]), 0.0) or 0.0
                       for p in outs}
            contrib = {p: w for p, w in contrib.items() if w >= 10}
            team_tot[(team, gids[i])] = sum(contrib.values())
            for p, w in contrib.items():
                own[(p, gids[i])] = w
    tt = d.apply(lambda r: team_tot.get((r.team_id, r.game_id), 0.0), axis=1)
    ow = d.apply(lambda r: own.get((r.athlete_id, r.game_id), 0.0), axis=1)
    d["presumed_share"] = np.clip((tt - ow), 0, None) / 160.0
    return d[["athlete_id", "game_id", "presumed_share"]]


def mu_stats(df, datecol, cal=None):
    """Per-stat adjusted means, one column per RAW key. Market-blind."""
    lg_pace = df.groupby(datecol).tm_pace_ew.transform("mean")
    lg_allow = df.groupby(datecol).opp_pts_against_ew.transform("mean")
    pace_f = ((df.tm_pace_ew + df.opp_pace_ew)
              / (df.tm_pace_ew + lg_pace)).fillna(1.0)
    def_f = ((df.opp_pts_against_ew / lg_allow) ** DEF_EXP).fillna(1.0)
    if (cal is not None and cal.get("use_def3")
            and "opp_tpa_for_ew" in df.columns):
        lg_tpa = df.groupby(datecol).opp_tpa_for_ew.transform("mean")
        def3_f = ((df.opp_tpa_for_ew / lg_tpa) ** DEF_EXP).fillna(1.0)
    else:
        def3_f = pd.Series(1.0, index=df.index)
    minutes = (W_FAST * df.min_ewf
               + (1 - W_FAST) * df.min_ews).fillna(df.min_ewf)

    out = pd.DataFrame(index=df.index)
    for st in RAW:
        per_game = df[f"{st}_ewf"]
        if f"{st}_ews" in df.columns:
            per_game = (W_FAST * df[f"{st}_ewf"]
                        + (1 - W_FAST) * df[f"{st}_ews"]).fillna(df[f"{st}_ewf"])
        if f"talent_{st}" in df.columns:
            # v3 T1: Kalman talent state (already regressed to an informed
            # prior; no extra shrinkage)
            rate = df[f"talent_{st}"].fillna(df[f"{st}_rate_ewf"])
        else:
            rate = df[f"{st}_rate_ewf"]
            if cal is not None:  # shrink unstable early-career rates
                rate = ((df.gp * rate + SHRINK_K * cal["lg_rate"][st])
                        / (df.gp + SHRINK_K))
        rate_mu = rate * minutes
        mu = (W_RATE * per_game.fillna(rate_mu)
              + (1 - W_RATE) * rate_mu.fillna(per_game))
        dfac = def_f if st in SCORING else 1.0
        if st == "tpm" and cal is not None and cal.get("use_def3"):
            dfac = def3_f
        mu = mu * pace_f * dfac
        if cal is not None:
            mu = mu * cal["c"][st] * cal["home"][st] ** (df.home - 0.5)
            if cal.get("gamma") and "presumed_share" in df.columns:
                mu = mu * (1 + cal["gamma"] * df.presumed_share.fillna(0.0))
        out[st] = mu
    return out


MIN_GRID_Z = np.array([-1.65, -0.7, 0.0, 0.7, 1.65])
MIN_GRID_W = np.array([0.10, 0.25, 0.30, 0.25, 0.10])


def _pace_def(df, datecol):
    lg_pace = df.groupby(datecol).tm_pace_ew.transform("mean")
    lg_allow = df.groupby(datecol).opp_pts_against_ew.transform("mean")
    pace_f = ((df.tm_pace_ew + df.opp_pace_ew)
              / (df.tm_pace_ew + lg_pace)).fillna(1.0)
    def_f = ((df.opp_pts_against_ew / lg_allow) ** DEF_EXP).fillna(1.0)
    return pace_f, def_f


def _mu_rate(df, st, minutes, cal2, pace_f, def_f):
    """Pure rate-x-minutes mean for one stat at the given minutes vector,
    with rate-vs-minutes curvature around the player's usual level."""
    usual = df.min_ews.fillna(df.min_ewf)
    rate = (df[f"talent_{st}"].fillna(df[f"{st}_rate_ewf"])
            + cal2["beta"][st] * (minutes - usual))
    mu = rate.clip(lower=0.0) * minutes * pace_f
    if st in SCORING:
        mu = mu * def_f
    return mu * cal2["c"][st] * cal2["home"][st] ** (df.home - 0.5)


def fit_t2_cal(panel, cutoff):
    """T2 calibration, all strictly pre-cutoff: rate-vs-minutes slopes,
    minutes variance by (starter, level) bucket, per-stat bias/home on the
    rate-x-minutes path, conditional sigma given ACTUAL minutes."""
    d = panel[(panel.game_date >= FIT_FROM) & (panel.game_date < cutoff)
              & (panel.gp >= 8) & panel.minutes.notna()
              & (panel.minutes > 0)].copy()
    d["home"] = d["home"].astype(float)
    cal2 = {"beta": {}, "c": {}, "home": {}, "sigma": {}, "mvar": {},
            "gamma": 0.0}
    # rate-vs-minutes curvature, within player
    for st in RAW:
        y = d[RAW[st]] / d.minutes
        dy = y - y.groupby(d.athlete_id).transform("mean")
        dm = d.minutes - d.minutes.groupby(d.athlete_id).transform("mean")
        ok = dy.notna() & dm.notna()
        cal2["beta"][st] = float((dy[ok] * dm[ok]).sum()
                                 / max((dm[ok] ** 2).sum(), 1e-9))
    # minutes spread by (starter, predicted-level) bucket
    mhat = (W_FAST * d.min_ewf + (1 - W_FAST) * d.min_ews).fillna(d.min_ewf)
    starter = (d.started_ewf.fillna(0) > 0.5)
    level = pd.cut(mhat, [0, 12, 20, 28, 45], labels=False)
    resid2 = (d.minutes - mhat) ** 2
    for s in (0, 1):
        for lv in range(4):
            m = (starter == bool(s)) & (level == lv)
            cal2["mvar"][(s, lv)] = float(resid2[m].mean()) if m.sum() > 50 \
                else float(resid2.mean())
    if "presumed_share" in d.columns:
        ok = mhat.gt(8) & d.presumed_share.notna()
        x = d.presumed_share[ok].values
        yy = (d.minutes[ok] / mhat[ok] - 1).clip(-1, 1).values
        if (x > 0).sum() > 200:
            cal2["gamma"] = float(np.clip((x * yy).sum() / (x * x).sum(),
                                          0, 2))
    # bias/home fit END-TO-END against the DEPLOYED estimator: the grid
    # expectation at predicted minutes (iteration 2 — fitting at actual
    # minutes left a -2pp skew because the rate-vs-minutes coupling shifts
    # the grid mean below mu(E[minutes]))
    pace_f, def_f = _pace_def(d, "game_date")
    fit_starter = (d.started_ewf.fillna(0) > 0.5).astype(int)
    fit_level = pd.cut(mhat, [0, 12, 20, 28, 45], labels=False).fillna(1)
    fit_msd = np.sqrt([cal2["mvar"][(s, int(lv))]
                       for s, lv in zip(fit_starter, fit_level)])
    for st in RAW:
        cal2["c"][st] = 1.0
        cal2["home"][st] = 1.0
        emu = sum(w * _mu_rate(d, st,
                               (mhat + z * fit_msd).clip(lower=2.0,
                                                         upper=42.0),
                               cal2, pace_f, def_f)
                  for z, w in zip(MIN_GRID_Z, MIN_GRID_W))
        ok = emu.notna() & d[RAW[st]].notna()
        cal2["c"][st] = d[RAW[st]][ok].mean() / emu[ok].mean()
        h = ok & (d.home == 1)
        a = ok & (d.home == 0)
        cal2["home"][st] = ((d[RAW[st]][h].mean() / emu[h].mean())
                            / (d[RAW[st]][a].mean() / emu[a].mean()))
    for mkt, parts in PARTS.items():
        if mkt in POISSON:
            continue
        mu = sum(_mu_rate(d, p, d.minutes, cal2, pace_f, def_f)
                 for p in parts)
        act = sum(d[RAW[p]] for p in parts)
        ok = mu.notna() & act.notna()
        b, a = np.polyfit(mu[ok], (act[ok] - mu[ok]) ** 2, 1)
        cal2["sigma"][mkt] = (max(a, 0.1), max(b, 0.2))
    return cal2


def predict_t2(ms, cal2):
    """P(over) integrated over the minutes distribution (5-point grid)."""
    pace_f, def_f = _pace_def(ms, "date")
    mhat = (W_FAST * ms.min_ewf + (1 - W_FAST) * ms.min_ews).fillna(ms.min_ewf)
    if cal2["gamma"] and "presumed_share" in ms.columns:
        mhat = mhat * (1 + cal2["gamma"] * ms.presumed_share.fillna(0.0))
    starter = (ms.started_ewf.fillna(0) > 0.5).astype(int)
    level = pd.cut(mhat, [0, 12, 20, 28, 45], labels=False).fillna(1)
    msd = np.sqrt([cal2["mvar"][(s, int(lv))]
                   for s, lv in zip(starter, level)])
    p_out = np.full(len(ms), np.nan)
    mu_out = np.full(len(ms), np.nan)
    for k, (z, w) in enumerate(zip(MIN_GRID_Z, MIN_GRID_W)):
        mins_k = (mhat + z * msd).clip(lower=2.0, upper=42.0)
        mus = pd.DataFrame(
            {st: _mu_rate(ms, st, mins_k, cal2, pace_f, def_f)
             for st in RAW}, index=ms.index)
        for mkt, parts in PARTS.items():
            rows = (ms.market == mkt).to_numpy()
            if not rows.any():
                continue
            mu_k = sum(mus.loc[rows, p] for p in parts).to_numpy(float)
            lines = ms.open_line.to_numpy(float)[rows]
            pk = np.array([p_over(mkt, m, li, cal2) if np.isfinite(m)
                           else np.nan
                           for m, li in zip(mu_k, lines)])
            base_p = np.nan_to_num(p_out[rows], nan=0.0)
            base_mu = np.nan_to_num(mu_out[rows], nan=0.0)
            p_out[rows] = base_p + w * pk
            mu_out[rows] = base_mu + w * mu_k
    return mu_out, p_out


def fit_play_cal(panel, cutoff):
    """Bias / home / shrinkage-ballast / sigma / availability-gamma from
    play data before cutoff."""
    d = panel[(panel.game_date >= FIT_FROM) & (panel.game_date < cutoff)
              & (panel.gp >= 4) & panel.minutes.notna()].copy()
    d["home"] = d["home"].astype(float)
    cal = {"c": {}, "home": {}, "lg_rate": {}, "sigma": {}, "gamma": 0.0}
    if "presumed_share" in d.columns:
        mhat = (W_FAST * d.min_ewf + (1 - W_FAST) * d.min_ews).fillna(d.min_ewf)
        ok = mhat.gt(8) & d.minutes.notna() & d.presumed_share.notna()
        x = d.presumed_share[ok].values
        y = (d.minutes[ok] / mhat[ok] - 1).clip(-1, 1).values
        if (x > 0).sum() > 200:
            cal["gamma"] = float(np.clip((x * y).sum() / (x * x).sum(), 0, 2))
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
                    help="also score 2026 (POST-HOC: registered holdout was "
                         "spent 2026-07-31; printed results are diagnostic)")
    ap.add_argument("--expanding", action="store_true",
                    help="fp-v2: weekly in-season expanding recalibration + "
                         "presumed-absent availability + threes defense")
    ap.add_argument("--talent", action="store_true",
                    help="v3 T1: rates from the Kalman talent engine "
                         "(run src/talent.py --build first)")
    ap.add_argument("--minutes", action="store_true",
                    help="v3 T2: distributional minutes integration "
                         "(implies --talent)")
    ap.add_argument("--mineng", action="store_true",
                    help="v3 M: minutes from the walk-forward share "
                         "engine (src/minutes_engine.py --build first), "
                         "delivered via the override path (implies "
                         "--talent)")
    args = ap.parse_args()
    if args.minutes:
        args.talent = True
    if args.mineng:
        args.talent = True

    panel = pd.read_pickle(os.path.join(ROOT, "data", "panel.pkl"))
    ms = pd.read_pickle(os.path.join(ROOT, "data", "modelset.pkl"))
    ms = ms[ms.matched & ~ms.void & ms.open_coherent
            & (ms.actual != ms.open_line)].copy()
    ms["over"] = (ms.actual > ms.open_line).astype(int)

    if args.talent:
        from build_modelset import norm
        tal = pd.read_pickle(os.path.join(ROOT, "data", "talent.pkl"))
        panel = panel.merge(tal, on=["athlete_id", "game_id"], how="left")
        tmap = panel.assign(
            nname=panel.athlete_display_name.map(norm),
            dstr=pd.to_datetime(panel.game_date).dt.strftime("%Y-%m-%d"))
        tcols = [c for c in tal.columns if c.startswith("talent_")]
        tmap = tmap.groupby(["nname", "dstr"])[tcols].max().reset_index()
        ms["dstr"] = pd.to_datetime(ms.date).dt.strftime("%Y-%m-%d")
        ms = ms.merge(tmap, on=["nname", "dstr"], how="left")

    if args.mineng:
        from build_modelset import norm

        def apply_mineng(df):
            # override delivery path (fp_live.py mechanism): per-game
            # EWs scaled by the minutes ratio, minutes estimate replaced
            ok = df.min_pred.notna() & (df.min_pred > 0)
            usual = (W_FAST * df.min_ewf
                     + (1 - W_FAST) * df.min_ews).fillna(df.min_ewf)
            ratio = (df.min_pred / usual.clip(lower=1.0)).where(ok, 1.0)
            for st in RAW:
                for tag in ("_ewf", "_ews"):
                    c = f"{st}{tag}"
                    if c in df.columns:
                        df[c] = df[c] * ratio
            df.loc[ok, "min_ewf"] = df.min_pred[ok]
            df.loc[ok, "min_ews"] = df.min_pred[ok]
            return df

        me = pd.read_pickle(os.path.join(ROOT, "data", "minutes_eng.pkl"))
        pmap = (panel.merge(me, on=["athlete_id", "game_id"], how="left")
                .assign(nname=lambda d: d.athlete_display_name.map(norm),
                        dstr=lambda d: pd.to_datetime(d.game_date)
                        .dt.strftime("%Y-%m-%d"))
                .groupby(["nname", "dstr"]).min_pred.max().reset_index())
        ms = ms.merge(pmap, on=["nname", "dstr"], how="left")
        ms = apply_mineng(ms)
        # engine-aware recalibration (M-G2 iteration 2): the play-data
        # calibration sees the SAME transformation, so c/home/sigma are
        # fit against engine minutes (walk-forward predictions — strictly
        # prior, no leakage into the pre-season fit window)
        panel = panel.merge(me[["athlete_id", "game_id", "min_pred"]],
                            on=["athlete_id", "game_id"], how="left")
        panel = apply_mineng(panel)

    if args.minutes:
        from build_modelset import norm
        pa = presumed_absent(panel)
        panel = panel.merge(pa, on=["athlete_id", "game_id"], how="left")
        pmap = panel.assign(
            nname=panel.athlete_display_name.map(norm),
            dstr=pd.to_datetime(panel.game_date).dt.strftime("%Y-%m-%d"))
        pmap = (pmap.groupby(["nname", "dstr"]).presumed_share.max()
                .reset_index())
        ms = ms.merge(pmap, on=["nname", "dstr"], how="left")

    if args.expanding:
        from build_modelset import norm
        pa = presumed_absent(panel)
        panel = panel.merge(pa, on=["athlete_id", "game_id"], how="left")
        pmap = panel.assign(
            nname=panel.athlete_display_name.map(norm),
            dstr=pd.to_datetime(panel.game_date).dt.strftime("%Y-%m-%d"))
        pmap = (pmap.groupby(["nname", "dstr"]).presumed_share.max()
                .reset_index())
        ms["dstr"] = pd.to_datetime(ms.date).dt.strftime("%Y-%m-%d")
        ms = ms.merge(pmap, on=["nname", "dstr"], how="left")

    seasons = [2025] + ([2026] if args.holdout else [])
    for season in seasons:
        sub = ms[ms.season == season].copy()
        if args.expanding:
            sub["week"] = (pd.to_datetime(sub.date)
                           .dt.to_period("W").dt.start_time)
            parts = []
            for wk, grp in sub.groupby("week"):
                cal = fit_play_cal(panel, wk.strftime("%Y-%m-%d"))
                cal["use_def3"] = True
                g = grp.copy()
                g["mu_model"] = predict(g, cal)
                gc = g[g.mu_model.notna()].copy()
                gc["p_model"] = [p_over(m, mu, li, cal) for m, mu, li in
                                 zip(gc.market, gc.mu_model, gc.open_line)]
                parts.append(gc)
            evx = pd.concat(parts)
            mode = "v2 expanding-weekly"
        elif args.minutes:
            cal2 = fit_t2_cal(panel, f"{season}-01-01")
            mu_m, p_m = predict_t2(sub, cal2)
            sub["mu_model"], sub["p_model"] = mu_m, p_m
            evx = sub[sub.p_model.notna()].copy()
            mode = "v3 T2 minutes-integrated"
        else:
            cal = fit_play_cal(panel, f"{season}-01-01")
            sub["mu_model"] = predict(sub, cal)
            evx = sub[sub.mu_model.notna()].copy()
            evx["p_model"] = [p_over(m, mu, li, cal) for m, mu, li in
                              zip(evx.market, evx.mu_model, evx.open_line)]
            mode = ("v3 M minutes-engine" if args.mineng else "v1 frozen")
        print(f"\nseason {season} [{mode}]: coverage "
              f"{len(evx)/len(sub)*100:.1f}% ({len(sub)-len(evx)} dropped)")
        evx["ll_model"] = ll(evx.p_model, evx.over)
        evx["ll_open"] = ll(evx.p_open, evx.over)
        tag = ("dev season 2025" if season == 2025 else
               "2026 POST-HOC DIAGNOSTIC (registered holdout spent "
               "2026-07-31; not a result)")
        evaluate(evx, tag)
        roi_sim(evx, tag)
        # zero-skill placebo: the opener's own devigged probability
        roi_sim(evx.assign(p_model=evx.p_open), f"{tag} PLACEBO", "p_model")


if __name__ == "__main__":
    main()
