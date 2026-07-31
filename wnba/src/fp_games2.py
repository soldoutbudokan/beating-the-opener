"""WNBA game model v2 — possession-based, usage-constrained (Market 1b-v2).

The owner's diagnosis of v1: you cannot sum per-minute scoring rates —
there is one ball. Here team points = plays x points-per-play where a
"play" is a possession use (FGA + 0.44 FTA + TOV):

  1. Game possessions predicted from both teams' strictly-prior EW pace;
     mapped to team plays by a scale fit on <=2024.
  2. Each rotation player demands plays = projected minutes x usage
     talent (Kalman state). Demands are renormalized so the team total
     equals the predicted play count (usage conservation).
  3. The usage-efficiency tradeoff beta (points-per-play lost per unit of
     relative forced over-usage s-1) is fit on <=2024 player-games
     against the model's own pre-game s — not realized usage, which is
     confounded by in-game shot selection. Set to 0 if |t| < 2.
  4. Defense applied EXACTLY ONCE, as opponent points-allowed-per-
     possession vs league (exponent fit <=2024); talent states carry no
     team/opponent context (circularity guard unchanged from v1).
  5. Margin head: scale/home-adv/sigma fit <=2024; margin distribution
     Normal vs Student-t chosen on <=2024 likelihood. Two market heads:
     P(margin>0) vs the open moneyline, P(margin>-line) vs the open
     spread. No odds inputs anywhere.

Gates GV2-1..4 + tripwire: PROGRESS.md "Market 1b-v2".

Usage: python3 src/fp_games2.py     # fit <=2024, dev = played 2025-26
"""
import os
import sys
from collections import defaultdict

import numpy as np
import pandas as pd
from scipy.stats import norm as spnorm
from scipy.stats import t as spt

sys.path.insert(0, os.path.dirname(__file__))
from fp_benchmark import ll, clustered_t
from fp_games import abbr_to_mascot, market_frame

ROOT = os.path.join(os.path.dirname(__file__), "..")
TEAM_MIN = 200.0
APP_ALPHA = 0.35
USG_DEFAULT = 0.43   # league per-minute usage, fallback for unseen players
EFF_DEFAULT = 0.85   # league points per play, same fallback
GRID_DEF_EXP = (0.0, 0.25, 0.5, 0.75, 1.0)
GRID_T_DF = (3, 4, 5, 6, 8, 10, 15, 20, 30)


def load_merged():
    panel = pd.read_pickle(os.path.join(ROOT, "data", "panel.pkl"))
    tal = pd.read_pickle(os.path.join(ROOT, "data", "talent.pkl"))
    p = panel.merge(tal, on=["athlete_id", "game_id"], how="left")
    p = p[p.minutes.notna() & (p.minutes > 0)].copy()
    p["plays"] = (p.field_goals_attempted + 0.44 * p.free_throws_attempted
                  + p.turnovers)
    return p.sort_values(["team_id", "game_date", "game_id"])


def fit_plays_scale(p):
    """<=2024: actual team plays regressed on predicted game possessions
    (mean of both teams' strictly-prior EW pace)."""
    t = (p[p.game_date < "2025-01-01"]
         .groupby(["team_id", "game_id"])
         .agg(plays=("plays", "sum"), tm=("tm_pace_ew", "first"),
              op=("opp_pace_ew", "first")).dropna())
    x = ((t.tm + t.op) / 2).to_numpy(float)
    y = t.plays.to_numpy(float)
    b, a = np.polyfit(x, y, 1)
    r = y - (a + b * x)
    print(f"plays scale (<=2024, n={len(t)}): plays = {a:.1f} + {b:.3f} x "
          f"poss_hat   (resid sd {r.std():.2f}, plays mean {y.mean():.1f})")
    return a, b


def team_game_table(p, pa, pb):
    """Per (team, game): pre-game usage-constrained expected points parts,
    plus <=2024 player rows for the beta (usage-efficiency) fit.

    Emits exp_pts as pts_raw + beta-correction hooks: exp_pts_raw (beta=0)
    and s (uniform over-usage factor), so exp_pts = exp_pts_raw
    + beta*(s-1)*plays_hat can be formed after beta is fit."""
    talent_usg = {}
    talent_eff = {}
    minutes_now = {}
    app_w = defaultdict(dict)
    rows = []
    prows = []
    for (team, gid), grp in p.groupby(["team_id", "game_id"], sort=False):
        g0 = grp.iloc[0]
        ros = app_w[team]
        cand = sorted(ros.items(),
                      key=lambda kv: -(kv[1] * minutes_now.get(kv[0], 0.0))
                      )[:10]
        wsum = sum(aw * minutes_now.get(a, 0.0) for a, aw in cand)
        exp_raw, s, plays_hat = np.nan, np.nan, np.nan
        if cand and wsum > 50 and pd.notna(g0.tm_pace_ew) \
                and pd.notna(g0.opp_pace_ew):
            poss_hat = (g0.tm_pace_ew + g0.opp_pace_ew) / 2
            plays_hat = pa + pb * poss_hat
            mins, usgs, effs, aids = [], [], [], []
            for a, aw in cand:
                w = min(aw * minutes_now.get(a, 0.0) * TEAM_MIN / wsum, 36.0)
                mins.append(w)
                usgs.append(talent_usg.get(a, USG_DEFAULT))
                effs.append(talent_eff.get(a, EFF_DEFAULT))
                aids.append(a)
            demand = np.array(mins) * np.array(usgs)
            D = demand.sum()
            if D > 0:
                s = plays_hat / D          # one ball: conserve usage
                alloc = demand * s
                exp_raw = float((alloc * np.array(effs)).sum())
                if g0.game_date < pd.Timestamp("2025-01-01"):
                    played = grp.set_index("athlete_id")
                    for a, pl, ef in zip(aids, alloc, effs):
                        if a in played.index:
                            r_ = played.loc[a]
                            if r_.plays > 0 and pd.notna(r_.talent_eff):
                                prows.append((s - 1.0,
                                              r_.points / r_.plays - ef,
                                              r_.plays))
        opp_ppp = (g0.opp_pts_against_ew / g0.opp_pace_ew
                   if pd.notna(g0.opp_pace_ew) and g0.opp_pace_ew > 0
                   else np.nan)
        rows.append({"team_id": team, "team_name": g0.team_name,
                     "game_id": gid, "game_date": g0.game_date,
                     "home": int(g0.home), "score": g0.team_score,
                     "opp_ppp": opp_ppp, "exp_pts_raw": exp_raw,
                     "s": s, "plays_hat": plays_hat})
        for r_ in grp.itertuples():
            if pd.notna(r_.talent_usg):
                talent_usg[r_.athlete_id] = r_.talent_usg
            if pd.notna(r_.talent_eff):
                talent_eff[r_.athlete_id] = r_.talent_eff
            mn = r_.min_ewf if pd.notna(r_.min_ewf) else r_.minutes
            minutes_now[r_.athlete_id] = mn
        played_ids = set(grp.athlete_id)
        for a in list(app_w[team]):
            app_w[team][a] *= (1 - APP_ALPHA)
        for a in played_ids:
            app_w[team][a] = app_w[team].get(a, 0.0) + APP_ALPHA
    return pd.DataFrame(rows), pd.DataFrame(
        prows, columns=["s1", "deff", "w"])


def fit_beta(prow):
    """Usage-efficiency tradeoff on <=2024, against the model's own
    pre-game over-usage factor (weighted least squares, plays weights)."""
    x = prow.s1.to_numpy(float)
    y = prow.deff.to_numpy(float)
    w = prow.w.to_numpy(float)
    xm = (w * x).sum() / w.sum()
    ym = (w * y).sum() / w.sum()
    sxx = (w * (x - xm) ** 2).sum()
    beta = (w * (x - xm) * (y - ym)).sum() / sxx
    resid = y - ym - beta * (x - xm)
    se = np.sqrt((w * resid ** 2).sum() / w.sum() / sxx * w.mean())
    t = beta / se
    print(f"beta (usage-efficiency tradeoff, <=2024, n={len(prow)}): "
          f"{beta:+.4f} pts/play per unit s-1  (t={t:.1f})")
    if abs(t) < 2:
        print("  -> |t| < 2: noise per registration, beta := 0")
        return 0.0
    if beta > 0:
        print("  -> wrong sign (efficiency GAIN under forced over-usage is "
              "not credible; selection artifact), beta := 0")
        return 0.0
    return float(beta)


def game_frame(tg, beta, def_exp):
    tg = tg.copy()
    tg["exp_pts_adj"] = (tg.exp_pts_raw
                         + beta * (tg.s - 1.0) * tg.plays_hat)
    lg_ppp = tg.groupby("game_date").opp_ppp.transform("mean")
    def_f = ((tg.opp_ppp / lg_ppp) ** def_exp).fillna(1.0)
    tg["exp_pts"] = tg.exp_pts_adj * def_f
    h = tg[tg.home == 1].set_index("game_id")
    a = tg[tg.home == 0].set_index("game_id")
    idx = h.index.intersection(a.index)
    g = pd.DataFrame({
        "game_date": h.loc[idx, "game_date"],
        "home_team": h.loc[idx, "team_name"],
        "exp_margin_raw": (h.loc[idx, "exp_pts"].to_numpy()
                           - a.loc[idx, "exp_pts"].to_numpy()),
        "margin": (h.loc[idx, "score"].to_numpy()
                   - a.loc[idx, "score"].to_numpy()),
    }).reset_index()
    g["home_win"] = (g.margin > 0).astype(int)
    return g[g.exp_margin_raw.notna()].copy()


def fit_margin_head(g):
    """Iteration 2: scale + home adv + sigma fit on 2020-2024 only — the
    <=2024 era table shows home advantage collapsed from ~3.3 pts
    (pre-2015) to ~1.1-1.5 pts (2021-24), and the all-history fit's 2.8
    baked a stale home edge into every prediction (the GV2-3 miss).
    Distribution: Normal vs Student-t on the residuals by likelihood."""
    tr = g[(g.game_date >= "2020-01-01") & (g.game_date < "2025-01-01")]
    x = tr.exp_margin_raw.to_numpy(float)
    y = tr.margin.to_numpy(float)
    ok = np.isfinite(x) & np.isfinite(y)
    slope, intercept = np.polyfit(x[ok], y[ok], 1)
    resid = y[ok] - (slope * x[ok] + intercept)
    sig = resid.std(ddof=1)
    ll_n = spnorm.logpdf(resid, scale=sig).sum()
    best = ("normal", None, sig, ll_n)
    for df in GRID_T_DF:
        sc = spt.fit(resid, df, floc=0)[2]
        ll_t = spt.logpdf(resid, df, scale=sc).sum()
        if ll_t > best[3]:
            best = ("t", df, sc, ll_t)
    kind, df, scale, _ = best
    print(f"margin head (<=2024, n={ok.sum()}): slope={slope:.3f} "
          f"home_adv={intercept:.2f} resid_sd={sig:.2f} "
          f"dist={kind}{'' if df is None else f'(df={df})'} scale={scale:.2f}")
    return {"slope": slope, "home_adv": intercept, "kind": kind,
            "df": df, "scale": scale}


def p_margin_gt(mu, x, head):
    """P(actual margin > x) under the fitted margin distribution."""
    z = (np.asarray(x, float) - np.asarray(mu, float)) / head["scale"]
    if head["kind"] == "t":
        return spt.sf(z, head["df"])
    return spnorm.sf(z)


def fit_def_exp(tg, beta):
    """Defense exponent by <=2024 margin residual sd (train-only choice)."""
    best = (np.inf, None)
    for e in GRID_DEF_EXP:
        g = game_frame(tg, beta, e)
        tr = g[g.game_date < "2025-01-01"]
        x, y = tr.exp_margin_raw.to_numpy(float), tr.margin.to_numpy(float)
        ok = np.isfinite(x) & np.isfinite(y)
        sl, ic = np.polyfit(x[ok], y[ok], 1)
        sd = (y[ok] - sl * x[ok] - ic).std(ddof=1)
        if sd < best[0]:
            best = (sd, e)
    print(f"defense exponent (<=2024): {best[1]} (resid sd {best[0]:.2f})")
    return best[1]


def spread_frame():
    """Book-0 spread: home line + devigged cover prices, open and close,
    with results. Line convention: `line` is the handicap ADDED to the
    participant's score (home covers when margin + home_line > 0)."""
    games = pd.read_pickle(os.path.join(ROOT, "data", "games.pkl"))
    ev = pd.read_pickle(os.path.join(ROOT, "data", "events.pkl"))
    ev = ev[(ev.status == "closed") & ev.home_score.notna()].set_index(
        "event_id")
    mas = abbr_to_mascot()
    sp = games[(games.market == "spread") & (games.book == 0)].copy()

    def amer_p(c):
        if pd.isna(c):
            return np.nan
        return 100 / (c + 100) if c > 0 else -c / (-c + 100)

    rows = {}
    for r in sp.itertuples():
        if r.event_id not in ev.index:
            continue
        d = rows.setdefault(r.event_id, {
            "event_id": r.event_id, "dstr": r.date,
            "home_team": mas.get(r.home, r.home),
            "margin": float(ev.loc[r.event_id, "home_score"]
                            - ev.loc[r.event_id, "visitor_score"])})
        side = "h" if r.participant == r.home else "a"
        d[f"line_{side}"] = r.line
        d[f"cost_{side}"] = amer_p(r.cost)
        d[f"oline_{side}"] = r.open_line
        d[f"ocost_{side}"] = amer_p(r.open_cost)
    m = pd.DataFrame(rows.values()).dropna(
        subset=["oline_h", "ocost_h", "ocost_a"])
    # sanity: two-way lines must mirror
    bad = m[(m.oline_h + m.oline_a).abs() > 0.01]
    if len(bad):
        print(f"spread_frame: dropping {len(bad)} mirror-violating events")
        m = m.drop(bad.index)
    m["p_open"] = m.ocost_h / (m.ocost_h + m.ocost_a)
    m["p_close"] = m.cost_h / (m.cost_h + m.cost_a)
    return m


def main():
    p = load_merged()
    pa, pb = fit_plays_scale(p)
    tg, prow = team_game_table(p, pa, pb)
    beta = fit_beta(prow)
    def_exp = fit_def_exp(tg, beta)
    g = game_frame(tg, beta, def_exp)
    head = fit_margin_head(g)
    g["mu"] = head["slope"] * g.exp_margin_raw + head["home_adv"]

    tr = g[g.game_date < "2025-01-01"]
    print(f"sanity: <=2024 team pts — projected "
          f"{tr.exp_margin_raw.abs().mean():.1f} mean |raw margin|; "
          f"actual mean score {tg[tg.game_date < '2025-01-01'].score.mean():.1f} "
          f"vs projected {tg[tg.game_date < '2025-01-01'].exp_pts_raw.mean():.1f}")

    g["dstr"] = pd.to_datetime(g.game_date).dt.strftime("%Y-%m-%d")

    # ---- moneyline head (GV2-1, GV2-3) ----
    m = market_frame()
    m["dstr"] = m.date
    j = g.merge(m[["dstr", "home_team", "p_open", "p_close", "home_win"]],
                on=["dstr", "home_team"], how="inner",
                suffixes=("_panel", ""))
    dev = j[j.game_date >= "2025-01-01"].copy()
    dev["p_model"] = p_margin_gt(dev.mu, 0.0, head)
    lm = ll(dev.p_model, dev.home_win)
    lo = ll(dev.p_open, dev.home_win)
    lc = ll(dev.p_close.fillna(dev.p_open), dev.home_win)
    d1, t1 = clustered_t((lm - lo).values, dev.dstr)
    print(f"\nML dev n={len(dev)}: LL(model)={lm.mean():.5f} "
          f"LL(open)={lo.mean():.5f} LL(close)={lc.mean():.5f}")
    print(f"GV2-1 model-open = {d1:+.5f} (clustered t={t1:.1f})  [<= +0.010]")
    cal = dev.p_model.mean() - dev.home_win.mean()
    print(f"GV2-3 calibration = {100*cal:+.2f}pp  [<= 2pp]")
    dc, tc = clustered_t((lm - lc).values, dev.dstr)
    print(f"ML tripwire vs close: {dc:+.5f} (t={tc:.1f})  [< -0.005 at t>2]")

    # ---- spread head (GV2-2, GV2-4) ----
    sp = spread_frame()
    js = g.merge(sp, on=["dstr", "home_team"], how="inner",
                 suffixes=("_panel", ""))
    devs = js[js.game_date >= "2025-01-01"].copy()
    devs["cover"] = np.sign(devs.margin + devs.oline_h)
    devs = devs[devs.cover != 0].copy()          # drop pushes
    devs["cover"] = (devs.cover > 0).astype(int)
    devs["p_model"] = p_margin_gt(devs.mu, -devs.oline_h, head)
    lm = ll(devs.p_model, devs.cover)
    lo = ll(devs.p_open, devs.cover)
    d2, t2 = clustered_t((lm - lo).values, devs.dstr)
    print(f"\nspread dev n={len(devs)}: LL(model)={lm.mean():.5f} "
          f"LL(open)={lo.mean():.5f}")
    print(f"GV2-2 model-open = {d2:+.5f} (clustered t={t2:.1f})  [<= +0.010]")
    devc = devs[devs.line_h.notna() & devs.p_close.notna()].copy()
    devc["cover_c"] = np.sign(devc.margin + devc.line_h)
    devc = devc[devc.cover_c != 0]
    devc["cover_c"] = (devc.cover_c > 0).astype(int)
    lmc = ll(p_margin_gt(devc.mu, -devc.line_h, head), devc.cover_c)
    lcc = ll(devc.p_close, devc.cover_c)
    dct, tct = clustered_t((lmc - lcc).values, devc.dstr)
    print(f"spread tripwire vs close (close line): {dct:+.5f} (t={tct:.1f})")
    mae = (devc.mu - (-devc.line_h)).abs().mean()
    print(f"GV2-4 MAE(predicted margin, close spread) = {mae:.2f} pts "
          f"(report only; open-line MAE "
          f"{(devs.mu - (-devs.oline_h)).abs().mean():.2f})")


if __name__ == "__main__":
    main()
