"""Leak-resistant availability features.

The v1 features asked "who appears in tonight's box score", which is contaminated
by garbage time: blowouts empty the bench, so the active list partly encodes the
final margin (corr(|margin|, n_active) = +0.64).

v2 restricts every availability judgement to ESTABLISHED ROTATION PLAYERS -- those
whose EWMA minutes (computed strictly from prior games) clear a threshold. Such a
player takes the floor in blowouts and nail-biters alike, so his presence or
absence reflects health/rest, which is public pre-tip, rather than game flow.
Deep-bench players, whose appearance IS game-flow dependent, are excluded entirely.
"""
import os
from collections import defaultdict, deque

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def build(games, pb, rot_min=14.0, rot_games=8, recent_window=10):
    """Return availability features plus a leak-resistant RAPM lineup rating."""
    pb = pb.copy()
    pb["game_id"] = pb.game_id.astype(str)
    pb["pid"] = pb.pid.astype(str)
    pb["played"] = (~pb.dnp) & pb["min"].notna() & (pb["min"] > 0)

    comp = (pb.pts.fillna(0) + 0.4 * pb.reb.fillna(0) + 0.7 * pb.ast.fillna(0)
            + 1.0 * pb.stl.fillna(0) + 0.7 * pb.blk.fillna(0) - 1.0 * pb.tov.fillna(0))
    mins = pb["min"].fillna(0)
    pb["per36"] = np.where(mins >= 5, 36.0 * comp / mins.replace(0, np.nan), np.nan)

    played = pb[pb.played]
    # pid -> minutes, per game/team
    appear = {(g, t): set(x.pid for x in grp.itertuples())
              for (g, t), grp in played.groupby(["game_id", "team"])}
    minutes = {(r.game_id, r.team, r.pid): r.min for r in played.itertuples(index=False)}
    per36 = {(r.game_id, r.pid): r.per36 for r in played.itertuples(index=False)}

    g = games.sort_values("date_utc").reset_index(drop=True).copy()
    g["game_id"] = g.game_id.astype(str)

    ewma_min = {}
    ewma_val = {}
    n_seen = defaultdict(int)
    # team -> deque of sets of pids who played recently (roster membership)
    recent = defaultdict(lambda: deque(maxlen=recent_window))
    a_m, a_v = 0.20, 0.12

    rows = []
    for r in g.itertuples(index=False):
        gid = r.game_id
        feat = {"game_id": gid}
        for side, team in (("home", r.home_abbr), ("away", r.away_abbr)):
            # Roster = players seen with this team recently (pre-game knowledge).
            pool = set()
            for s in recent[team]:
                pool |= s
            # Rotation pool: established minutes, judged only on PRIOR games.
            rot = [p for p in pool
                   if n_seen[p] >= rot_games and ewma_min.get(p, 0.0) >= rot_min]
            active_tonight = appear.get((gid, team), set())

            def val(p):
                v = ewma_val.get(p, 0.0)
                m = ewma_min.get(p, 0.0)
                return float(v) * float(m) / 36.0

            avail = [p for p in rot if p in active_tonight]
            missing = [p for p in rot if p not in active_tonight]
            av_t = sum(val(p) for p in avail)
            ms_t = sum(val(p) for p in missing)
            feat[f"{side}_rot_avail_talent"] = av_t
            feat[f"{side}_rot_missing_talent"] = ms_t
            feat[f"{side}_rot_missing_star"] = max([val(p) for p in missing], default=0.0)
            feat[f"{side}_rot_n_missing"] = len(missing)
            feat[f"{side}_rot_size"] = len(rot)
            feat[f"{side}_rot_share"] = av_t / (av_t + ms_t) if (av_t + ms_t) > 0 else 1.0
            feat[f"{side}_rot_top_avail"] = max([val(p) for p in avail], default=0.0)
            feat[f"{side}_rot_avail_min"] = sum(ewma_min.get(p, 0.0) for p in avail)
            feat[f"{side}_rot_missing_min"] = sum(ewma_min.get(p, 0.0) for p in missing)

        feat["rot_avail_diff"] = feat["home_rot_avail_talent"] - feat["away_rot_avail_talent"]
        feat["rot_missing_diff"] = feat["away_rot_missing_talent"] - feat["home_rot_missing_talent"]
        feat["rot_star_diff"] = feat["away_rot_missing_star"] - feat["home_rot_missing_star"]
        feat["rot_share_diff"] = feat["home_rot_share"] - feat["away_rot_share"]
        feat["rot_missing_min_diff"] = (feat["away_rot_missing_min"]
                                        - feat["home_rot_missing_min"])
        rows.append(feat)

        # ---- update state AFTER emitting ----
        for team in (r.home_abbr, r.away_abbr):
            act = appear.get((gid, team), set())
            if act:
                recent[team].append(set(act))
            for p in act:
                mn = minutes.get((gid, team, p))
                if mn is not None and np.isfinite(mn):
                    ewma_min[p] = mn if p not in ewma_min else (1 - a_m) * ewma_min[p] + a_m * mn
                v = per36.get((gid, p))
                if v is not None and np.isfinite(v):
                    ewma_val[p] = v if p not in ewma_val else (1 - a_v) * ewma_val[p] + a_v * v
                n_seen[p] += 1

    return pd.DataFrame(rows)


def rapm_lineup(games, pb, hist, pids, rot_min=14.0, rot_games=8, recent_window=10):
    """RAPM team rating over the AVAILABLE ROTATION only (no garbage-time dilution)."""
    pidx = {p: i for i, p in enumerate(pids)}
    cp_dates = np.array([h[0] for h in hist])

    pb = pb.copy()
    pb["game_id"] = pb.game_id.astype(str)
    pb["pid"] = pb.pid.astype(str)
    pb["played"] = (~pb.dnp) & pb["min"].notna() & (pb["min"] > 0)
    played = pb[pb.played]
    appear = {(gg, t): set(x.pid for x in grp.itertuples())
              for (gg, t), grp in played.groupby(["game_id", "team"])}
    minutes = {(r.game_id, r.team, r.pid): r.min for r in played.itertuples(index=False)}

    g = games.sort_values("date_utc").reset_index(drop=True).copy()
    g["game_id"] = g.game_id.astype(str)
    g["gdate"] = pd.to_datetime(g.game_date)

    ewma_min = {}
    n_seen = defaultdict(int)
    recent = defaultdict(lambda: deque(maxlen=recent_window))
    a_m = 0.20

    out = []
    for r in g.itertuples(index=False):
        pos = np.searchsorted(cp_dates, r.gdate, side="right") - 1
        beta = hist[pos][1] if pos >= 0 else np.zeros(len(pids))
        hfa = hist[pos][2] if pos >= 0 else 2.5

        side_val = {}
        for side, team in (("home", r.home_abbr), ("away", r.away_abbr)):
            pool = set()
            for s in recent[team]:
                pool |= s
            rot = [p for p in pool
                   if n_seen[p] >= rot_games and ewma_min.get(p, 0.0) >= rot_min]
            act = appear.get((r.game_id, team), set())
            avail = [p for p in rot if p in act]
            if not avail:
                side_val[side] = 0.0
                continue
            w = np.array([ewma_min.get(p, 0.0) for p in avail], dtype=float)
            if w.sum() <= 0:
                w = np.ones(len(avail))
            w = w / w.sum()
            b = np.array([beta[pidx[p]] if p in pidx else 0.0 for p in avail])
            side_val[side] = float(np.dot(w, b))

        out.append({"game_id": r.game_id,
                    "rapm2_home": side_val["home"], "rapm2_away": side_val["away"],
                    "rapm2_diff": side_val["home"] - side_val["away"],
                    "rapm2_hfa": hfa})

        for team in (r.home_abbr, r.away_abbr):
            act = appear.get((r.game_id, team), set())
            if act:
                recent[team].append(set(act))
            for p in act:
                mn = minutes.get((r.game_id, team, p))
                if mn is not None and np.isfinite(mn):
                    ewma_min[p] = mn if p not in ewma_min else (1 - a_m) * ewma_min[p] + a_m * mn
                n_seen[p] += 1

    res = pd.DataFrame(out)
    res["rapm2_pred_margin"] = res.rapm2_diff + res.rapm2_hfa
    return res
