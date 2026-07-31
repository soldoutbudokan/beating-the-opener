"""WNBA game-line model from player projections (Market 1b, PROGRESS.md).

Team expected points = sum over the STRICTLY-PRIOR expected rotation
(previous game's lineup, weighted by their prior EW minutes, normalized to
200 team-minutes) of per-minute scoring talent (Kalman states — which carry
no team/opponent adjustment). Pace and defense factors are then applied
EXACTLY ONCE, at the game level (the circularity guard). Margin = adjusted
points difference + home advantage; P(home) = Phi(margin / sigma).
home_adv and sigma are fit on games <= 2024 only, walk-forward. No odds
column is ever an input.

Usage: python3 src/fp_games.py         # fit <=2024, dev = played 2025-26
"""
import os
import sys
from collections import defaultdict

import numpy as np
import pandas as pd
from scipy.stats import norm as spnorm

sys.path.insert(0, os.path.dirname(__file__))
from fp_benchmark import ll, clustered_t

ROOT = os.path.join(os.path.dirname(__file__), "..")
DEF_EXP = 0.5
TEAM_MIN = 200.0


def team_game_table():
    """Per (team, game): pre-game expected points from the prior game's
    rotation, plus team context and result."""
    panel = pd.read_pickle(os.path.join(ROOT, "data", "panel.pkl"))
    tal = pd.read_pickle(os.path.join(ROOT, "data", "talent.pkl"))
    p = panel.merge(tal, on=["athlete_id", "game_id"], how="left")
    p = p[p.minutes.notna() & (p.minutes > 0)].copy()
    p = p.sort_values(["team_id", "game_date", "game_id"])

    # state per player: latest pre-game talent + EW minutes (post-game
    # states update AFTER we emit the current game's projection)
    talent_now = {}
    minutes_now = {}
    app_w = defaultdict(dict)   # team -> {player: appearance EW}
    APP_ALPHA = 0.35
    rows = []
    for (team, gid), grp in p.groupby(["team_id", "game_id"], sort=False):
        g0 = grp.iloc[0]
        # iteration 2: appearance-EW expected rotation (top 10 by
        # appearance weight x prior EW minutes), not just last game's XI
        ros = app_w[team]
        cand = sorted(ros.items(),
                      key=lambda kv: -(kv[1] * minutes_now.get(kv[0], 0.0))
                      )[:10]
        wsum = sum(aw * minutes_now.get(a, 0.0) for a, aw in cand)
        if cand and wsum > 50:
            pts = 0.0
            for a, aw in cand:
                w = aw * minutes_now.get(a, 0.0) * TEAM_MIN / wsum
                pts += min(w, 36.0) * talent_now.get(a, 0.30)
        else:
            pts = np.nan
        rows.append({"team_id": team, "team_name": g0.team_name,
                     "game_id": gid, "game_date": g0.game_date,
                     "home": int(g0.home), "won": int(bool(g0.team_winner)),
                     "score": g0.team_score,
                     "tm_pace_ew": g0.tm_pace_ew,
                     "opp_pace_ew": g0.opp_pace_ew,
                     "opp_pts_against_ew": g0.opp_pts_against_ew,
                     "exp_pts_raw": pts})
        for r in grp.itertuples():
            if pd.notna(r.talent_poi):
                talent_now[r.athlete_id] = r.talent_poi
            mn = r.min_ewf if pd.notna(r.min_ewf) else r.minutes
            minutes_now[r.athlete_id] = mn
        played = set(grp.athlete_id)
        for a in list(app_w[team]):
            app_w[team][a] *= (1 - APP_ALPHA)
        for a in played:
            app_w[team][a] = app_w[team].get(a, 0.0) + APP_ALPHA
    return pd.DataFrame(rows)


def game_frame(tg):
    """One row per game: home vs away expected points, single game-level
    pace/defense adjustment, margin features."""
    lg_allow = tg.groupby("game_date").opp_pts_against_ew.transform("mean")
    lg_pace = tg.groupby("game_date").tm_pace_ew.transform("mean")
    pace_f = ((tg.tm_pace_ew + tg.opp_pace_ew)
              / (tg.tm_pace_ew + lg_pace)).fillna(1.0)
    def_f = ((tg.opp_pts_against_ew / lg_allow) ** DEF_EXP).fillna(1.0)
    tg = tg.assign(exp_pts=tg.exp_pts_raw * pace_f * def_f)
    h = tg[tg.home == 1].set_index("game_id")
    a = tg[tg.home == 0].set_index("game_id")
    idx = h.index.intersection(a.index)
    g = pd.DataFrame({
        "game_date": h.loc[idx, "game_date"],
        "home_team": h.loc[idx, "team_name"],
        "away_team": a.loc[idx, "team_name"],
        "exp_margin_raw": (h.loc[idx, "exp_pts"].to_numpy()
                           - a.loc[idx, "exp_pts"].to_numpy()),
        "home_win": h.loc[idx, "won"],
        "margin": (h.loc[idx, "score"].to_numpy()
                   - a.loc[idx, "score"].to_numpy()),
    }).reset_index()
    return g[g.exp_margin_raw.notna()].copy()


def fit_params(g):
    """home_adv + scale correction + sigma on <=2024 games (walk-forward
    inputs by construction)."""
    tr = g[g.game_date < "2025-01-01"].copy()
    # regress actual margin on raw expected margin -> slope calibrates the
    # aggregation's scale; intercept is home advantage
    x = tr.exp_margin_raw.to_numpy(float)
    y = tr.margin.to_numpy(float)
    ok = np.isfinite(x) & np.isfinite(y)
    slope, intercept = np.polyfit(x[ok], y[ok], 1)
    resid = y[ok] - (slope * x[ok] + intercept)
    return {"slope": float(slope), "home_adv": float(intercept),
            "sigma": float(resid.std(ddof=1))}


def abbr_to_mascot():
    import gzip
    import json
    out = {}
    for season in (2025, 2026):
        path = os.path.join(ROOT, "data", "raw", "bp",
                            f"events_{season}.json.gz")
        for e in json.load(gzip.open(path)):
            for p_ in e.get("participants", []):
                out[p_["id"]] = p_["name"]
    return out


def market_frame():
    """Consensus book-0 moneyline open/close, devigged, with results from
    events.pkl (closed games)."""
    games = pd.read_pickle(os.path.join(ROOT, "data", "games.pkl"))
    ev = pd.read_pickle(os.path.join(ROOT, "data", "events.pkl"))
    ev = ev[(ev.status == "closed") & ev.home_score.notna()].set_index(
        "event_id")
    mas = abbr_to_mascot()
    ml = games[(games.market == "moneyline") & (games.book == 0)].copy()

    def amer_p(c):
        if pd.isna(c):
            return np.nan
        return 100 / (c + 100) if c > 0 else -c / (-c + 100)

    rows = {}
    for r in ml.itertuples():
        if r.event_id not in ev.index:
            continue
        d = rows.setdefault(r.event_id, {
            "event_id": r.event_id, "date": r.date,
            "home_team": mas.get(r.home, r.home),
            "home_win": int(ev.loc[r.event_id, "home_score"]
                            > ev.loc[r.event_id, "visitor_score"])})
        side = "h" if r.participant == r.home else "a"
        d[f"open_{side}"] = amer_p(r.open_cost)
        d[f"close_{side}"] = amer_p(r.cost)
    m = pd.DataFrame(rows.values()).dropna(subset=["open_h", "open_a"])
    m["p_open"] = m.open_h / (m.open_h + m.open_a)
    m["p_close"] = m.close_h / (m.close_h + m.close_a)
    return m


def main():
    tg = team_game_table()
    g = game_frame(tg)
    cal = fit_params(g)
    print(f"fit <=2024: slope={cal['slope']:.3f} "
          f"home_adv={cal['home_adv']:.2f} sigma={cal['sigma']:.2f}")
    g["p_model"] = spnorm.cdf((cal["slope"] * g.exp_margin_raw
                               + cal["home_adv"]) / cal["sigma"])

    m = market_frame()
    g["dstr"] = pd.to_datetime(g.game_date).dt.strftime("%Y-%m-%d")
    m["dstr"] = m.date
    j = g.merge(m[["dstr", "home_team", "p_open", "p_close", "home_win"]],
                on=["dstr", "home_team"], how="inner",
                suffixes=("_panel", ""))
    dev = j[(j.game_date >= "2025-01-01")].copy()
    print(f"dev (played 2025-26, joined to consensus ML): n={len(dev)}")
    lm = ll(dev.p_model, dev.home_win)
    lo = ll(dev.p_open, dev.home_win)
    lc = ll(dev.p_close.fillna(dev.p_open), dev.home_win)
    d, t = clustered_t((lm - lo).values, dev.dstr)
    print(f"LL(model)={lm.mean():.5f}  LL(open)={lo.mean():.5f}  "
          f"LL(close)={lc.mean():.5f}")
    print(f"GG1 model-open = {d:+.5f} (clustered t={t:.1f})  [<= +0.010]")
    cal2 = dev.p_model.mean() - dev.home_win.mean()
    print(f"GG2 calibration: {100*cal2:+.2f}pp  [<= 2pp]")
    dc, tc = clustered_t((lm - lc).values, dev.dstr)
    print(f"vs close: {dc:+.5f} (t={tc:.1f})  [tripwire < -0.005 at t>2]")


if __name__ == "__main__":
    main()
