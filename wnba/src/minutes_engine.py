"""v3 minutes engine (PROGRESS.md "Minutes-engine gates").

Walk-forward Kalman filter on each player's SHARE of team minutes —
minutes are compositional (~200 conserved per team-game), so the state
is a share and predictions renormalize over the presumed-available set
(players who appeared in the team's previous game — strictly prior
participation, never tonight's box score; the row's own player is added
to the set, mirroring the props population which conditions on the
subject playing).

Observation model: a player's raw share inflates when teammates are
missing, so the Kalman update targets the availability-normalized
pseudo-observation z_i = (minutes_i / team_total) * sum(states of
players who actually played). Season boundaries inflate state variance
(offseason role churn). Structural factors the EW blend cannot see —
returnee ramp by absence length, back-to-back rest — are fit on <=2014
residuals only and applied multiplicatively.

All hyperparameters tuned on pre-2015 data only (T1 convention).

Usage: python3 src/minutes_engine.py --tune    # grid search, pre-2015
       python3 src/minutes_engine.py --build   # walk-forward all years
                                               #  -> data/minutes_eng.pkl
                                               #  + M-G1 report (2015-24)
"""
import argparse
import os
from collections import defaultdict

import numpy as np
import pandas as pd

ROOT = os.path.join(os.path.dirname(__file__), "..")
W_FAST = 0.6                     # the incumbent blend (fp_model.W_FAST)
P0 = 0.010                       # initial state variance (share^2)

# tuned pre-2015 by --tune (grid in tune(), plus an extended-r pass to
# confirm the boundary — interior optimum); committed after the run.
# t_hat is only the INITIAL team-total level: the engine tracks the
# league team-minute total walk-forward (T_ALPHA EW) because totals
# drifted ~195 (pre-2015) -> ~201 (2015+), which a frozen constant
# turns into a systematic low bias. w_blend combines the engine with
# the incumbent EW blend (precision blend, tuned pre-2015): the engine
# carries the availability/returnee structure, the blend carries the
# stable-veteran signal.
PARAMS = {"q": 0.0002, "r": 0.002, "q_season": 0.005, "p0": 0.04,
          "t_hat": 195.4, "w_blend": None}
T_ALPHA = 0.01


def team_games(panel):
    """GLOBALLY date-ordered team-game structure (players change teams,
    so per-player Kalman states must see games in true chronological
    order — iterating team-by-team would leak a traded player's later
    games into their earlier-team predictions), plus per
    (team_id, game_id) the played rows."""
    d = panel.sort_values("game_date")
    order = (d[["team_id", "game_id", "game_date"]].drop_duplicates()
             .sort_values(["game_date", "team_id"]))
    rows = {k: g for k, g in d.groupby(["team_id", "game_id"], sort=False)}
    return order, rows


def run(panel, q, r, q_season, p0, t_hat, ramp=None):
    """One walk-forward pass. Returns DataFrame athlete_id, game_id,
    min_pred (pre-update prediction for every played row)."""
    order, rows = team_games(panel)
    m = {}                       # athlete_id -> share state
    P = {}                       # athlete_id -> state variance
    seen_season = {}             # athlete_id -> last season seen
    last_date = {}               # athlete_id -> last game date
    prev_players = {}            # team_id -> set of athlete_ids last game
    out_aid, out_gid, out_pred = [], [], []

    t_ew = t_hat                 # walk-forward league team-total level
    for team, gid, gdate in order.itertuples(index=False):
        g = rows[(team, gid)]
        season = int(g.season.iloc[0])
        aids = g.athlete_id.to_numpy()
        mins = g.minutes.to_numpy(float)
        total = mins.sum()
        avail = prev_players.get(team, set())

        # lazy init + season-boundary inflation (once per player-season)
        for a in aids:
            if a not in m:
                m[a], P[a] = p0, P0
                seen_season[a] = season
            elif seen_season[a] != season:
                P[a] += q_season
                seen_season[a] = season

        # predictions BEFORE tonight's update
        S_avail = sum(m.get(j, 0.0) for j in avail)
        for a, mn in zip(aids, mins):
            S = S_avail + (0.0 if a in avail else m[a])
            pred = m[a] / max(S, 1e-6) * t_ew
            if ramp is not None:
                ld = last_date.get(a)
                gap = (gdate - ld).days if ld is not None else None
                pred *= ramp_factor(ramp, a in avail, gap)
            out_aid.append(a)
            out_gid.append(gid)
            out_pred.append(min(max(pred, 0.0), 44.0))

        # Kalman updates (availability-normalized pseudo-observation)
        S_played = sum(m[a] for a in aids)
        for a, mn in zip(aids, mins):
            z = (mn / max(total, 1.0)) * S_played
            Pa = P[a] + q
            K = Pa / (Pa + r)
            m[a] = m[a] + K * (z - m[a])
            P[a] = Pa * (1 - K)
            last_date[a] = gdate

        prev_players[team] = set(aids)
        t_ew = (1 - T_ALPHA) * t_ew + T_ALPHA * total

    return pd.DataFrame({"athlete_id": out_aid, "game_id": out_gid,
                         "min_pred": out_pred})


def ramp_key(in_avail, gap_days):
    """Structural bucket: fresh player, normal, or returning from an
    absence (missed the team's previous game), by own layoff length."""
    if gap_days is None:
        return "debut"
    if in_avail:
        return "b2b" if gap_days <= 1 else "normal"
    if gap_days >= 21:
        return "return_21+"
    if gap_days >= 10:
        return "return_10_20"
    return "return_short"


def ramp_factor(ramp, in_avail, gap_days):
    return ramp.get(ramp_key(in_avail, gap_days), 1.0)


def fit_ramp(panel, pred):
    """Multiplicative factors actual/predicted per structural bucket,
    fit on <=2014 rows only."""
    d = panel.merge(pred, on=["athlete_id", "game_id"])
    d = d[d.game_date < "2015-01-01"]
    last, avail_prev, keys = {}, {}, {}
    order, rows = team_games(d)
    for team, gid, gdate in order.itertuples(index=False):
        g = rows[(team, gid)]
        avail = avail_prev.get(team, set())
        for ix, a in zip(g.index, g.athlete_id):
            ld = last.get(a)
            keys[ix] = ramp_key(a in avail,
                                (gdate - ld).days if ld else None)
            last[a] = gdate
        avail_prev[team] = set(g.athlete_id)
    dk = pd.Series(keys).reindex(d.index)
    ramp = {}
    for k, grp in d.groupby(dk):
        if k in ("normal", "debut") or len(grp) < 200:
            continue
        ok = grp.min_pred > 4
        ramp[k] = float((grp.minutes[ok] / grp.min_pred[ok])
                        .clip(0.1, 3.0).mean())
    return ramp


def evaluate(panel, pred, lo, hi, label):
    d = panel.merge(pred, on=["athlete_id", "game_id"])
    d = d[(d.game_date >= lo) & (d.game_date < hi)
          & d.min_ewf.notna()].copy()
    blend = (W_FAST * d.min_ewf + (1 - W_FAST) * d.min_ews).fillna(d.min_ewf)
    e_eng = (d.minutes - d.min_pred).abs()
    e_bld = (d.minutes - blend).abs()
    print(f"{label}: n={len(d)}  engine MAE={e_eng.mean():.3f}  "
          f"blend MAE={e_bld.mean():.3f}  "
          f"delta={e_eng.mean()-e_bld.mean():+.3f}")
    return e_eng.mean(), e_bld.mean()


def tune(panel):
    train = panel[panel.game_date < "2015-01-01"]
    grid_q = [2e-5, 5e-5, 2e-4, 1e-3]
    grid_r = [0.002, 0.005, 0.01, 0.02]
    grid_qs = [0.0, 0.001, 0.005, 0.02]
    grid_p0 = [0.04, 0.06]
    t_hat = float(train.groupby(["team_id", "game_id"]).minutes.sum().mean())
    best = None
    for q in grid_q:
        for r in grid_r:
            for qs in grid_qs:
                for p0 in grid_p0:
                    pred = run(train, q, r, qs, p0, t_hat)
                    # tune on 2008+ (states warmed up), still pre-2015
                    d = train.merge(pred, on=["athlete_id", "game_id"])
                    d = d[d.game_date >= "2008-01-01"]
                    mae = float((d.minutes - d.min_pred).abs().mean())
                    if best is None or mae < best[0]:
                        best = (mae, q, r, qs, p0)
                        print(f"  new best MAE={mae:.3f}  q={q} r={r} "
                              f"q_season={qs} p0={p0}")
    mae, q, r, qs, p0 = best
    print(f"\ntuned pre-2015: q={q} r={r} q_season={qs} p0={p0} "
          f"t_hat={t_hat:.1f}  (train MAE {mae:.3f})")
    print("-> paste into PARAMS and run --build")


W_BUCKETS = [0.0, 12.0, 20.0, 28.0, 45.0]   # by EW-blend minutes level


def tune_w(panel, pred):
    """Per-bucket blend weight for the engine-vs-EW combination, tuned
    on pre-2015 rows only (2008+ so states are warmed up). The engine
    wins on mid-rotation players (availability/returnee structure); the
    EW blend wins on locked-in high-minute starters — one global w
    leaves both edges on the table."""
    d = panel.merge(pred, on=["athlete_id", "game_id"])
    d = d[(d.game_date >= "2008-01-01") & (d.game_date < "2015-01-01")
          & d.min_ewf.notna()].copy()
    blend = (W_FAST * d.min_ewf + (1 - W_FAST) * d.min_ews).fillna(d.min_ewf)
    bucket = pd.cut(blend, W_BUCKETS, labels=False)
    ws = {}
    for b, grp in d.groupby(bucket):
        bl = blend[grp.index]
        best = min((float((grp.minutes
                           - (w * grp.min_pred + (1 - w) * bl))
                          .abs().mean()), float(w))
                   for w in np.arange(0.0, 1.01, 0.1))
        ws[int(b)] = best[1]
    print(f"w_blend per bucket, tuned pre-2015: {ws}")
    return ws


def build(panel):
    pr = PARAMS
    base = run(panel, pr["q"], pr["r"], pr["q_season"], pr["p0"],
               pr["t_hat"])
    ramp = fit_ramp(panel, base)
    print("ramp factors (<=2014 fit):",
          {k: round(v, 3) for k, v in sorted(ramp.items())})
    pred = run(panel, pr["q"], pr["r"], pr["q_season"], pr["p0"],
               pr["t_hat"], ramp=ramp)
    ws = pr["w_blend"]
    if ws is None:
        ws = tune_w(panel, pred)
    d = panel[["athlete_id", "game_id", "min_ewf", "min_ews"]].merge(
        pred, on=["athlete_id", "game_id"])
    blend = (W_FAST * d.min_ewf + (1 - W_FAST) * d.min_ews).fillna(d.min_ewf)
    w = pd.cut(blend, W_BUCKETS, labels=False).map(ws).fillna(1.0)
    d["min_eng_raw"] = d.min_pred
    d["min_pred"] = (w * d.min_pred + (1 - w) * blend).fillna(d.min_pred)
    pred = d[["athlete_id", "game_id", "min_pred", "min_eng_raw"]]
    path = os.path.join(ROOT, "data", "minutes_eng.pkl")
    pred.to_pickle(path)
    print(f"{path}: {len(pred)} rows (w_blend={ws})")
    print("\n== M-G1 (registered): 2015-2024 played rows, blend defined ==")
    evaluate(panel, pred, "2015-01-01", "2025-01-01", "M-G1")
    evaluate(panel, pred, "2025-01-01", "2026-01-01", "dev 2025 (report)")
    evaluate(panel, pred, "2026-01-01", "2027-01-01",
             "2026 (report, post-hoc)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tune", action="store_true")
    ap.add_argument("--build", action="store_true")
    args = ap.parse_args()
    panel = pd.read_pickle(os.path.join(ROOT, "data", "panel.pkl"))
    panel = panel[panel.minutes.notna()].copy()
    if args.tune:
        tune(panel)
    if args.build:
        build(panel)


if __name__ == "__main__":
    main()
