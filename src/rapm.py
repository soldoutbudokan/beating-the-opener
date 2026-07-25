"""Minutes-weighted APM (RAPM-style) player impact ratings, walk-forward.

For each checkpoint date we fit, on games that already finished:

    margin_per100_g = sum_p beta_p * minshare_{p,g,home}
                    - sum_p beta_p * minshare_{p,g,away} + hfa

with ridge shrinkage toward zero (replacement level) and exponential time decay.
Prediction for a future game uses each available player's EWMA minutes share
(renormalised over players who are actually dressed), so a missing star's minutes
flow to his replacements exactly as they would in reality.
"""
import os
from collections import defaultdict

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_player_minutes():
    pb = pd.read_json(os.path.join(ROOT, "data", "raw", "player_box.jsonl"), lines=True)
    pb["game_id"] = pb.game_id.astype(str)
    pb["pid"] = pb.pid.astype(str)
    pb["played"] = (~pb.dnp) & pb["min"].notna() & (pb["min"] > 0)
    return pb


def build(games, pb, cadence_days=14, halflife_days=200.0, lam=180.0,
          window_days=560, min_games=15):
    g = games.sort_values("date_utc").reset_index(drop=True).copy()
    g["game_id"] = g.game_id.astype(str)
    g["gdate"] = pd.to_datetime(g.game_date)

    played = pb[pb.played].copy()
    # Minutes share within a team-game (~240 team minutes).
    tot = played.groupby(["game_id", "team"])["min"].transform("sum")
    played["share"] = played["min"] / tot.replace(0, np.nan)
    played = played[played.share.notna()]

    pids = sorted(played.pid.unique())
    pidx = {p: i for i, p in enumerate(pids)}
    n_p = len(pids)

    gmeta = g.set_index("game_id")[["home_abbr", "away_abbr", "margin", "gdate"]]
    # Normalise margin to per-100 possessions where available.
    poss = None
    eff_path = os.path.join(ROOT, "data", "raw", "team_box.jsonl")
    if os.path.exists(eff_path):
        tb = pd.read_json(eff_path, lines=True)
        tb["game_id"] = tb.game_id.astype(str)
        h = tb[tb.home_away == "home"].set_index("game_id")
        a = tb[tb.home_away == "away"].set_index("game_id")
        p = 0.5 * ((h.fga + 0.44 * h.fta - h.oreb + h.tov)
                   + (a.fga + 0.44 * a.fta - a.oreb + a.tov))
        poss = p

    # Rows of the design matrix, one per (game, player) with signed share.
    played = played.join(gmeta, on="game_id")
    played = played[played.home_abbr.notna()]
    played["sign"] = np.where(played.team == played.home_abbr, 1.0, -1.0)
    played["pcol"] = played.pid.map(pidx)

    gids = played.game_id.unique()
    gid_row = {gid: i for i, gid in enumerate(gids)}
    played["grow"] = played.game_id.map(gid_row)

    gm = gmeta.reindex(gids)
    y_margin = gm.margin.values.astype(float)
    if poss is not None:
        pv = poss.reindex(gids).values.astype(float)
        pv = np.where(np.isfinite(pv) & (pv > 50), pv, 100.0)
        y = 100.0 * y_margin / pv
    else:
        y = y_margin
    gdates = gm.gdate.values

    rows = played.grow.values
    cols = played.pcol.values
    vals = played.sign.values * played.share.values

    checkpoints = sorted(pd.to_datetime(pd.Series(g.gdate.unique())))[::cadence_days]
    hist = []
    for cp in checkpoints:
        cp = pd.Timestamp(cp)
        mask = (gdates < np.datetime64(cp)) & (
            gdates >= np.datetime64(cp - pd.Timedelta(days=window_days)))
        if mask.sum() < 300:
            continue
        sel_rows = np.flatnonzero(mask)
        rowmap = -np.ones(len(gids), dtype=int)
        rowmap[sel_rows] = np.arange(len(sel_rows))
        keep = rowmap[rows] >= 0
        r_i = rowmap[rows[keep]]
        c_i = cols[keep]
        v_i = vals[keep]

        m = len(sel_rows)
        age = (np.datetime64(cp) - gdates[sel_rows]).astype("timedelta64[D]").astype(float)
        w = 0.5 ** (age / halflife_days)

        X = np.zeros((m, n_p + 1))
        np.add.at(X, (r_i, c_i), v_i)
        X[:, n_p] = 1.0
        yy = y[sel_rows]

        Xw = X * w[:, None]
        A = X.T @ Xw
        reg = np.eye(n_p + 1) * lam
        reg[n_p, n_p] = 1e-6
        b = Xw.T @ yy
        try:
            sol = np.linalg.solve(A + reg, b)
        except np.linalg.LinAlgError:
            continue
        hist.append((cp, sol[:n_p].copy(), float(sol[n_p])))

    if not hist:
        raise RuntimeError("no RAPM checkpoints fitted")

    cp_dates = np.array([h[0] for h in hist])

    # ---- sequential prediction pass ----
    by_game_team = defaultdict(dict)
    for r in played.itertuples(index=False):
        by_game_team[(r.game_id, r.team)][r.pid] = r.share
    roster_all = pb.groupby(["game_id", "team"])["pid"].apply(list).to_dict()

    ewma_share = {}
    pid_n = defaultdict(int)
    alpha = 0.20
    out = []
    for r in g.itertuples(index=False):
        pos = np.searchsorted(cp_dates, r.gdate, side="right") - 1
        beta = hist[pos][1] if pos >= 0 else np.zeros(n_p)
        hfa = hist[pos][2] if pos >= 0 else 2.5

        vals_side = {}
        for side, team in (("home", r.home_abbr), ("away", r.away_abbr)):
            active = list(by_game_team.get((r.game_id, team), {}).keys())
            if not active:
                vals_side[side] = 0.0
                continue
            shares, betas = [], []
            for p in active:
                s = ewma_share.get(p)
                if s is None or pid_n[p] < 3:
                    s = 1.0 / max(len(active), 1)
                shares.append(s)
                i = pidx.get(p)
                betas.append(beta[i] if i is not None else 0.0)
            shares = np.array(shares, dtype=float)
            if shares.sum() <= 0:
                shares = np.ones(len(active)) / len(active)
            shares = shares / shares.sum()  # redistribute missing players' minutes
            vals_side[side] = float(np.dot(shares, np.array(betas)))

        out.append({
            "game_id": r.game_id,
            "rapm_home": vals_side["home"],
            "rapm_away": vals_side["away"],
            "rapm_diff": vals_side["home"] - vals_side["away"],
            "rapm_hfa": hfa,
        })

        for p, s in by_game_team.get((r.game_id, r.home_abbr), {}).items():
            ewma_share[p] = s if p not in ewma_share else (1 - alpha) * ewma_share[p] + alpha * s
            pid_n[p] += 1
        for p, s in by_game_team.get((r.game_id, r.away_abbr), {}).items():
            ewma_share[p] = s if p not in ewma_share else (1 - alpha) * ewma_share[p] + alpha * s
            pid_n[p] += 1

    res = pd.DataFrame(out)
    res["rapm_pred_margin"] = res.rapm_diff + res.rapm_hfa
    return res, (pids, hist)
