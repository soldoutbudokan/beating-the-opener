"""Efficiency, opponent-adjusted rating, and player-availability features.

Three additions over the base feature set:

1. Possession-based offensive/defensive efficiency (EWMA).
2. Opponent-adjusted team ratings from a ridge fit on past margins, recomputed
   on a weekly cadence using only games that already finished.
3. Player availability -- the load-management signal. A player's value and
   expected minutes come strictly from games BEFORE the current one; only the
   binary "is this player in tonight's box score" is taken from the game itself.
   That is public ~30 minutes pre-tip and is priced into the closing line, so it
   is a fair input for a closing-line comparison. Actual minutes played are never
   used, as those are an in-game outcome.
"""
import os
from collections import defaultdict, deque

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEAGUE_ORTG = 110.0


# --------------------------------------------------------------------------
# Possession / efficiency
# --------------------------------------------------------------------------
def team_efficiency(games):
    """Merge team box scores onto games and derive per-possession ratings."""
    tb = pd.read_json(os.path.join(ROOT, "data", "raw", "team_box.jsonl"), lines=True)
    tb["game_id"] = tb.game_id.astype(str)
    games = games.copy()
    games["game_id"] = games.game_id.astype(str)

    home = tb[tb.home_away == "home"].set_index("game_id")
    away = tb[tb.home_away == "away"].set_index("game_id")
    idx = games.game_id

    def col(frame, c):
        return frame[c].reindex(idx).values

    hp = 0.5 * ((col(home, "fga") + 0.44 * col(home, "fta") - col(home, "oreb")
                 + col(home, "tov"))
                + (col(away, "fga") + 0.44 * col(away, "fta") - col(away, "oreb")
                   + col(away, "tov")))
    out = pd.DataFrame({"game_id": idx.values, "poss": hp})
    out["home_ortg"] = 100.0 * games.home_score.values / hp
    out["away_ortg"] = 100.0 * games.away_score.values / hp
    return out


def build_efficiency_features(games, eff, alpha=0.06):
    """Sequential EWMA of offensive / defensive rating and pace."""
    g = games.merge(eff, on="game_id", how="left").sort_values("date_utc")
    ortg, drtg, pace = {}, {}, {}
    seen = defaultdict(int)
    season_of = {}
    rows = []
    for r in g.itertuples(index=False):
        h, a = r.home_abbr, r.away_abbr
        for t in (h, a):
            # Regress toward league mean at each new season.
            if season_of.get(t) != r.season_year:
                season_of[t] = r.season_year
                if t in ortg:
                    ortg[t] = 0.6 * ortg[t] + 0.4 * LEAGUE_ORTG
                    drtg[t] = 0.6 * drtg[t] + 0.4 * LEAGUE_ORTG
        rows.append({
            "game_id": r.game_id,
            "ortg_home": ortg.get(h, LEAGUE_ORTG), "drtg_home": drtg.get(h, LEAGUE_ORTG),
            "ortg_away": ortg.get(a, LEAGUE_ORTG), "drtg_away": drtg.get(a, LEAGUE_ORTG),
            "net_home": ortg.get(h, LEAGUE_ORTG) - drtg.get(h, LEAGUE_ORTG),
            "net_away": ortg.get(a, LEAGUE_ORTG) - drtg.get(a, LEAGUE_ORTG),
            "pace_home": pace.get(h, 100.0), "pace_away": pace.get(a, 100.0),
            "eff_n_home": seen[h], "eff_n_away": seen[a],
        })
        if np.isfinite(getattr(r, "home_ortg", np.nan)):
            for t, o, d in ((h, r.home_ortg, r.away_ortg), (a, r.away_ortg, r.home_ortg)):
                ortg[t] = o if t not in ortg else (1 - alpha) * ortg[t] + alpha * o
                drtg[t] = d if t not in drtg else (1 - alpha) * drtg[t] + alpha * d
                pace[t] = r.poss if t not in pace else (1 - alpha) * pace[t] + alpha * r.poss
                seen[t] += 1
    out = pd.DataFrame(rows)
    out["net_diff"] = out.net_home - out.net_away
    out["pace_sum"] = out.pace_home + out.pace_away
    return out


# --------------------------------------------------------------------------
# Opponent-adjusted ridge ratings (weekly walk-forward)
# --------------------------------------------------------------------------
def ridge_ratings(games, halflife_days=120.0, lam=12.0, cadence_days=7):
    """Solve margin = r_home - r_away + hfa on past games only, weekly."""
    g = games.sort_values("date_utc").reset_index(drop=True)
    teams = sorted(set(g.home_abbr) | set(g.away_abbr))
    tidx = {t: i for i, t in enumerate(teams)}
    n_t = len(teams)

    dates = pd.to_datetime(g.game_date)
    g = g.assign(gdate_ts=dates)
    uniq = sorted(dates.unique())
    checkpoints = uniq[::cadence_days]

    ratings_hist = []  # (date, ratings vector, hfa)
    for cp in checkpoints:
        past = g[g.gdate_ts < cp]
        # Two seasons of history is plenty; older games get negligible weight.
        past = past[past.gdate_ts >= cp - pd.Timedelta(days=560)]
        if len(past) < 200:
            continue
        age = (cp - past.gdate_ts).dt.days.values.astype(float)
        w = 0.5 ** (age / halflife_days)
        m = len(past)
        X = np.zeros((m, n_t + 1))
        X[np.arange(m), [tidx[t] for t in past.home_abbr]] = 1.0
        X[np.arange(m), [tidx[t] for t in past.away_abbr]] = -1.0
        X[:, n_t] = 1.0  # home-court advantage
        y = past.margin.values.astype(float)
        W = w[:, None]
        A = X.T @ (W * X)
        reg = np.eye(n_t + 1) * lam
        reg[n_t, n_t] = 1e-6  # don't shrink HFA
        b = X.T @ (w * y)
        try:
            sol = np.linalg.solve(A + reg, b)
        except np.linalg.LinAlgError:
            continue
        r = sol[:n_t] - sol[:n_t].mean()
        ratings_hist.append((cp, r, sol[n_t]))

    if not ratings_hist:
        return pd.DataFrame({"game_id": g.game_id, "srs_diff": 0.0})

    cp_dates = np.array([x[0] for x in ratings_hist])
    rows = []
    for r in g.itertuples(index=False):
        pos = np.searchsorted(cp_dates, r.gdate_ts, side="right") - 1
        if pos < 0:
            rows.append({"game_id": r.game_id, "srs_home": 0.0, "srs_away": 0.0,
                         "srs_hfa": 2.5})
            continue
        _, vec, hfa = ratings_hist[pos]
        rows.append({
            "game_id": r.game_id,
            "srs_home": float(vec[tidx[r.home_abbr]]),
            "srs_away": float(vec[tidx[r.away_abbr]]),
            "srs_hfa": float(hfa),
        })
    out = pd.DataFrame(rows)
    out["srs_diff"] = out.srs_home - out.srs_away
    out["srs_pred_margin"] = out.srs_diff + out.srs_hfa
    return out


# --------------------------------------------------------------------------
# Player availability
# --------------------------------------------------------------------------
def build_availability(games):
    """Talent-on-floor and talent-missing features from player box scores."""
    pb = pd.read_json(os.path.join(ROOT, "data", "raw", "player_box.jsonl"), lines=True)
    pb["game_id"] = pb.game_id.astype(str)
    pb["played"] = (~pb.dnp) & pb["min"].notna() & (pb["min"] > 0)
    pb["pid"] = pb.pid.astype(str)

    # Per-36 box composite; a rough but stable proxy for on-court value.
    comp = (pb.pts.fillna(0) + 0.4 * pb.reb.fillna(0) + 0.7 * pb.ast.fillna(0)
            + 1.0 * pb.stl.fillna(0) + 0.7 * pb.blk.fillna(0) - 1.0 * pb.tov.fillna(0))
    pb["comp"] = comp
    mins = pb["min"].fillna(0)
    pb["per36"] = np.where(mins >= 5, 36.0 * comp / mins.replace(0, np.nan), np.nan)

    played = pb[pb.played].copy()
    by_game = {gid: grp for gid, grp in played.groupby("game_id")}
    roster_by_game = {gid: grp for gid, grp in pb.groupby("game_id")}

    g = games.sort_values("date_utc").reset_index(drop=True)
    g["game_id"] = g.game_id.astype(str)

    ewma_min = {}      # pid -> expected minutes
    ewma_val = {}      # pid -> per-36 value
    pid_games = defaultdict(int)
    recent_team_players = defaultdict(lambda: deque(maxlen=8))  # team -> sets of pids

    alpha_m, alpha_v = 0.20, 0.12
    rows = []
    for r in g.itertuples(index=False):
        gid = r.game_id
        grp = by_game.get(gid)
        roster_grp = roster_by_game.get(gid)
        feat = {"game_id": gid}

        for side, team in (("home", r.home_abbr), ("away", r.away_abbr)):
            # Roster pool = players seen in this team's recent games.
            pool = set()
            for s in recent_team_players[team]:
                pool |= s
            active = set()
            if grp is not None:
                active = set(grp[grp.team == team].pid.tolist())
            # Anyone dressed but listed DNP is also known pre-game.
            if roster_grp is not None:
                pool |= set(roster_grp[roster_grp.team == team].pid.tolist())

            def val(p):
                v = ewma_val.get(p)
                m = ewma_min.get(p)
                if v is None or m is None or pid_games[p] < 5:
                    return 0.0
                return float(v) * float(m) / 36.0

            avail_talent = sum(val(p) for p in active)
            missing = pool - active
            missing_talent = sum(val(p) for p in missing)
            missing_star = max([val(p) for p in missing], default=0.0)
            top_avail = sorted((val(p) for p in active), reverse=True)[:3]

            feat[f"{side}_avail_talent"] = avail_talent
            feat[f"{side}_missing_talent"] = missing_talent
            feat[f"{side}_missing_star"] = missing_star
            feat[f"{side}_n_missing"] = len(missing)
            feat[f"{side}_n_active"] = len(active)
            feat[f"{side}_top3_talent"] = float(sum(top_avail))
            denom = avail_talent + missing_talent
            feat[f"{side}_talent_share"] = avail_talent / denom if denom > 0 else 1.0

        feat["avail_talent_diff"] = feat["home_avail_talent"] - feat["away_avail_talent"]
        feat["missing_talent_diff"] = (feat["away_missing_talent"]
                                       - feat["home_missing_talent"])
        feat["missing_star_diff"] = feat["away_missing_star"] - feat["home_missing_star"]
        feat["talent_share_diff"] = (feat["home_talent_share"]
                                     - feat["away_talent_share"])
        feat["top3_talent_diff"] = feat["home_top3_talent"] - feat["away_top3_talent"]
        rows.append(feat)

        # ---- update player state AFTER emitting ----
        if grp is not None:
            for p in grp.itertuples(index=False):
                pid, mn, v = p.pid, p.min, p.per36
                if mn is not None and np.isfinite(mn):
                    ewma_min[pid] = (mn if pid not in ewma_min
                                     else (1 - alpha_m) * ewma_min[pid] + alpha_m * mn)
                if v is not None and np.isfinite(v):
                    ewma_val[pid] = (v if pid not in ewma_val
                                     else (1 - alpha_v) * ewma_val[pid] + alpha_v * v)
                pid_games[pid] += 1
            for team in (r.home_abbr, r.away_abbr):
                recent_team_players[team].append(
                    set(grp[grp.team == team].pid.tolist()))
    return pd.DataFrame(rows)
