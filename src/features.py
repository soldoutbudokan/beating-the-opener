"""Build leak-free pre-match features.

Single chronological pass over all matches (2008->). State (Elo, EWMA goals,
form, last played) updates only AFTER a match's features are recorded, so every
feature uses strictly prior information. Odds features use only the early
(Friday/Tuesday-collected) prices, never closing.
"""
import os
from collections import defaultdict, deque

import numpy as np
import pandas as pd

from odds_utils import devig_shin

DATA = os.path.join(os.path.dirname(__file__), "..", "data", "matches.pkl")
OUT = os.path.join(os.path.dirname(__file__), "..", "data", "features.pkl")

# Elo params (standard club-elo-ish values)
K = 20.0
HOME_ADV = 65.0
DIV_BASE = {
    "E0": 1600, "E1": 1450, "E2": 1350, "E3": 1250, "EC": 1150,
    "SC0": 1450, "SC1": 1300, "SC2": 1200, "SC3": 1100,
    "D1": 1600, "D2": 1450, "I1": 1600, "I2": 1450,
    "SP1": 1600, "SP2": 1450, "F1": 1600, "F2": 1450,
    "N1": 1500, "B1": 1500, "P1": 1500, "T1": 1500, "G1": 1500,
}
EW_ALPHA = 0.10  # EWMA weight for goals for/against
DIV_IDX = {d: i for i, d in enumerate(DIV_BASE)}


def elo_expect(diff):
    return 1.0 / (1.0 + 10.0 ** (-diff / 400.0))


def main():
    df = pd.read_pickle(DATA)
    df = df.sort_values(["Date", "Div", "HomeTeam"]).reset_index(drop=True)

    # de-vig early Pinnacle odds where present (features only; NaN elsewhere)
    ps = df[["PSH", "PSD", "PSA"]].to_numpy(float)
    ok = ~np.isnan(ps).any(axis=1)
    p_open = np.full((len(df), 3), np.nan)
    p_open[ok] = devig_shin(ps[ok])

    b365 = df[["B365H", "B365D", "B365A"]].to_numpy(float)
    ok_b = ~np.isnan(b365).any(axis=1)
    p_b365 = np.full((len(df), 3), np.nan)
    p_b365[ok_b] = devig_shin(b365[ok_b])

    elo = {}
    ew_gf = {}   # EW goals for per match
    ew_ga = {}
    ew_stf = {}  # EW shots-on-target for / against
    ew_sta = {}
    form = defaultdict(lambda: deque(maxlen=5))   # recent points
    last_date = {}
    n_played = defaultdict(int)

    rows = []
    dates = df["Date"].to_numpy()
    for i, r in enumerate(df.itertuples(index=False)):
        h, a, div = r.HomeTeam, r.AwayTeam, r.Div
        base = DIV_BASE.get(div, 1400)
        eh = elo.setdefault(h, float(base))
        ea = elo.setdefault(a, float(base))
        gfh = ew_gf.get(h, 1.3); gah = ew_ga.get(h, 1.3)
        gfa = ew_gf.get(a, 1.3); gaa = ew_ga.get(a, 1.3)
        stfh = ew_stf.get(h, 4.4); stah = ew_sta.get(h, 4.4)
        stfa = ew_stf.get(a, 4.4); staa = ew_sta.get(a, 4.4)
        d = pd.Timestamp(dates[i])
        rest_h = min((d - last_date[h]).days, 30) if h in last_date else 30
        rest_a = min((d - last_date[a]).days, 30) if a in last_date else 30
        diff = eh + HOME_ADV - ea
        exp_h = elo_expect(diff)

        rows.append((
            eh, ea, diff, exp_h,
            gfh, gah, gfa, gaa, gfh - gaa, gfa - gah,
            stfh, stah, stfa, staa, stfh - staa, stfa - stah,
            (sum(form[h]) / len(form[h])) if form[h] else 1.1,
            (sum(form[a]) / len(form[a])) if form[a] else 1.1,
            min(n_played[h], 100), min(n_played[a], 100),
            rest_h, rest_a, DIV_IDX.get(div, -1),
        ))

        # ---- state update (after features recorded) ----
        hg, ag = r.FTHG, r.FTAG
        res = 1.0 if hg > ag else (0.5 if hg == ag else 0.0)
        margin = np.log1p(abs(hg - ag)) if hg != ag else 1.0
        delta = K * margin * (res - exp_h)
        elo[h] = eh + delta
        elo[a] = ea - delta
        ew_gf[h] = (1 - EW_ALPHA) * gfh + EW_ALPHA * hg
        ew_ga[h] = (1 - EW_ALPHA) * gah + EW_ALPHA * ag
        ew_gf[a] = (1 - EW_ALPHA) * gfa + EW_ALPHA * ag
        ew_ga[a] = (1 - EW_ALPHA) * gaa + EW_ALPHA * hg
        hst, ast = r.HST, r.AST
        if not (np.isnan(hst) or np.isnan(ast)):
            ew_stf[h] = (1 - EW_ALPHA) * stfh + EW_ALPHA * hst
            ew_sta[h] = (1 - EW_ALPHA) * stah + EW_ALPHA * ast
            ew_stf[a] = (1 - EW_ALPHA) * stfa + EW_ALPHA * ast
            ew_sta[a] = (1 - EW_ALPHA) * staa + EW_ALPHA * hst
        form[h].append(3.0 if res == 1 else (1.0 if res == 0.5 else 0.0))
        form[a].append(3.0 if res == 0 else (1.0 if res == 0.5 else 0.0))
        last_date[h] = d
        last_date[a] = d
        n_played[h] += 1
        n_played[a] += 1

    cols = ["elo_h", "elo_a", "elo_diff", "elo_exp_h",
            "ew_gf_h", "ew_ga_h", "ew_gf_a", "ew_ga_a", "att_edge_h", "att_edge_a",
            "ew_stf_h", "ew_sta_h", "ew_stf_a", "ew_sta_a", "sot_edge_h", "sot_edge_a",
            "form_h", "form_a", "n_played_h", "n_played_a", "rest_h", "rest_a", "div_idx"]
    feat = pd.DataFrame(rows, columns=cols)

    feat["p_open_h"], feat["p_open_d"], feat["p_open_a"] = p_open.T
    # log-odds of early probs (better scale for GBM/logit)
    with np.errstate(divide="ignore", invalid="ignore"):
        feat["lo_open_h"] = np.log(p_open[:, 0] / (1 - p_open[:, 0]))
        feat["lo_open_d"] = np.log(p_open[:, 1] / (1 - p_open[:, 1]))
        feat["lo_open_a"] = np.log(p_open[:, 2] / (1 - p_open[:, 2]))
    feat["overround_ps"] = (1 / ps).sum(axis=1) - 1
    feat["b365_ps_dis"] = p_b365[:, 0] - p_open[:, 0]  # cross-book disagreement, home

    out = pd.concat([df.reset_index(drop=True), feat], axis=1)
    out.to_pickle(OUT)
    print(f"features built: {len(out)} rows, {len(cols) + 8} feature cols")


if __name__ == "__main__":
    main()
