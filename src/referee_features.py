"""Point-in-time referee crew tendencies.

Each referee carries running averages of the games he has already worked -- total
points relative to the league norm, fouls called, and home-team margin. A crew's
feature is the mean over its (usually three) officials. All state updates happen
after a game's row is emitted, so a crew's rating never reflects tonight's game.
"""
import json
import os
from collections import defaultdict

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_officials():
    path = os.path.join(ROOT, "data", "raw", "officials.jsonl")
    rows = []
    with open(path) as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            offs = d.get("officials") or []
            rows.append({"game_id": str(d["game_id"]),
                         "officials": [o for o in offs if o],
                         "attendance": d.get("attendance")})
    return pd.DataFrame(rows)


def build(games, alpha=0.03, min_games=20):
    """games needs game_id, date_utc, total_pts, margin, and home/away fouls."""
    offs = load_officials().set_index("game_id")

    tb = pd.read_json(os.path.join(ROOT, "data", "raw", "team_box.jsonl"), lines=True)
    tb["game_id"] = tb.game_id.astype(str)
    fouls = tb.groupby("game_id")["pf"].sum()

    g = games.sort_values("date_utc").reset_index(drop=True).copy()
    g["game_id"] = g.game_id.astype(str)

    ref_tot = {}      # EWMA of (total points - league mean)
    ref_foul = {}     # EWMA of (game fouls - league mean)
    ref_margin = {}   # EWMA of home margin
    ref_n = defaultdict(int)

    # League baselines also tracked point-in-time so the era shift is absorbed.
    lg_tot, lg_foul, lg_marg = None, None, None
    beta = 0.01

    rows = []
    for r in g.itertuples(index=False):
        crew = offs.officials.get(r.game_id, [])
        if not isinstance(crew, list):
            crew = []
        seen = [p for p in crew if ref_n[p] >= min_games]

        def avg(d, keys):
            vals = [d[k] for k in keys if k in d]
            return float(np.mean(vals)) if vals else 0.0

        rows.append({
            "game_id": r.game_id,
            "crew_total_bias": avg(ref_tot, seen),
            "crew_foul_bias": avg(ref_foul, seen),
            "crew_home_bias": avg(ref_margin, seen),
            "crew_known": len(seen),
            "crew_exp": float(np.mean([ref_n[p] for p in crew])) if crew else 0.0,
        })

        # ---- update after emitting ----
        tot = getattr(r, "total_pts", np.nan)
        marg = getattr(r, "margin", np.nan)
        fl = fouls.get(r.game_id, np.nan)
        if np.isfinite(tot):
            lg_tot = tot if lg_tot is None else (1 - beta) * lg_tot + beta * tot
        if np.isfinite(fl):
            lg_foul = fl if lg_foul is None else (1 - beta) * lg_foul + beta * fl
        if np.isfinite(marg):
            lg_marg = marg if lg_marg is None else (1 - beta) * lg_marg + beta * marg

        for p in crew:
            if np.isfinite(tot) and lg_tot is not None:
                d = tot - lg_tot
                ref_tot[p] = d if p not in ref_tot else (1 - alpha) * ref_tot[p] + alpha * d
            if np.isfinite(fl) and lg_foul is not None:
                d = fl - lg_foul
                ref_foul[p] = d if p not in ref_foul else (1 - alpha) * ref_foul[p] + alpha * d
            if np.isfinite(marg) and lg_marg is not None:
                d = marg - lg_marg
                ref_margin[p] = d if p not in ref_margin else (1 - alpha) * ref_margin[p] + alpha * d
            ref_n[p] += 1

    return pd.DataFrame(rows), (ref_tot, ref_foul, ref_margin, ref_n)
