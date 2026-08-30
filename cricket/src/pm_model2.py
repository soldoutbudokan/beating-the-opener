"""Cricket v2 (registration Q): team Elo backbone + phase/wicket-aware
player-composition values + per-segment logit blend.

Everything is fit/tuned on matches dated < 2024-06-01 (train era). The
train-era walk-forward log loss (2018 -> train end, teams with history) is
the market-free iteration criterion; dev (the registration-P population) is
scored only via --dev and every touch is logged in PROGRESS.md.

Usage: python3 src/pm_model2.py            # tune + train-era report
       python3 src/pm_model2.py --dev      # + score the dev benchmark
"""
import argparse
import itertools
import os
from collections import defaultdict

import numpy as np
import pandas as pd

from pm_benchmark import ll, clustered_t
from pm_model import home_flag, roi

ROOT = os.path.join(os.path.dirname(__file__), "..")
TRAIN_END = "2024-06-01"
EVAL_LO = "2018-01-01"
MIN_PRIOR = 10                 # both teams need this many prior matches
ELO_GRID = {"K": (16, 24, 32, 48, 64, 96), "home": (0, 40, 80),
            "regress": (0.0, 0.2, 0.4), "scale": (300.0, 400.0),
            "mov": (0.0, 0.5, 1.0, 2.0)}   # mov: K multiplier 1 + mov*log1p(|margin runs|/20)
XFMT_GRID = (0.0, 0.25, 0.5)   # weight of cross-format international results
                               # (ODI/ODM/IT20) as extra Elo observations
VAL_ALPHA = 0.002              # per-ball EW decay (v1's tuned choice)
WICKET_GRID = (4.0, 6.0, 8.0)
SHRINK_GRID = (150.0, 300.0)
SIGMA_GRID = (25.0, 40.0)
W_GRID = (0.0, 0.25, 0.5, 0.75, 1.0)
TEMP_GRID = (0.8, 1.0, 1.25, 1.5)
APP_ALPHA = 0.35
EPS = 1e-6


def logit(p):
    p = np.clip(p, EPS, 1 - EPS)
    return np.log(p / (1 - p))


def inv(z):
    return 1.0 / (1.0 + np.exp(-z))


def load():
    m = pd.read_parquet(os.path.join(ROOT, "data", "matches_cs.parquet"))
    m = m[(m.result == "normal") & m.winner.notna()].copy()
    m = m.sort_values(["date", "match_id"]).reset_index(drop=True)
    m["home1"] = [home_flag(t, c) for t, c in zip(m.team1, m.city)]
    m["y1"] = (m.winner == m.team1).astype(float)
    m["seg"] = np.where(m.comp == "t20s",
                        np.where(m.gender == "female", "intl_f", "intl_m"), "fr")
    st = m.get("stage", pd.Series(None, index=m.index)).fillna("").str.lower()
    m["ko"] = st.str.contains("final|qualifier|eliminator|play-off|playoff|semi").astype(int)
    m = add_dead_rubber(m)
    x = pd.read_parquet(os.path.join(ROOT, "data", "matches_extra.parquet"))
    x = x[(x.result == "normal") & x.winner.notna()].copy()
    x["home1"] = [home_flag(t, c) for t, c in zip(x.team1, x.city)]
    x["y1"] = (x.winner == x.team1).astype(float)
    x["seg"] = np.where(x.gender == "female", "intl_f", "intl_m")
    x["margin_runs"] = 20.0
    x["extra"] = 1
    m["extra"] = 0
    d = pd.read_parquet(os.path.join(ROOT, "data", "deliveries_cs.parquet"))
    d["phase"] = np.where(d.over <= 5, 0, np.where(d.over <= 14, 1, 2))
    # chase-aware runs-equivalent margin for MOV-Elo: a defended total counts
    # the run gap; a successful chase counts wickets-in-hand and balls-to-
    # spare as runs at the chase's own scoring rate (10-wicket-with-40-balls
    # is dominance, not a 1-run squeak)
    g2 = d.groupby(["match_id", "innings"]).agg(runs=("runs_total", "sum"),
                                                balls=("runs_total", "size"),
                                                wkts=("wicket", "sum"))
    tot = g2.runs.unstack(); balls = g2.balls.unstack(); wk = g2.wkts.unstack()
    if 2 in tot:
        gap = tot[1] - tot[2]
        rate2 = (tot[2] / balls[2]).clip(0.5, 3.0)
        chase_eq = (120 - balls[2]).clip(lower=0) * rate2 * 0.5 \
            + (10 - wk[2]).clip(lower=0) * 4.0
        margin = np.where(gap > 0, gap, chase_eq)
        m = m.merge(pd.Series(margin, index=tot.index, name="margin_runs"),
                    left_on="match_id", right_index=True, how="left")
    else:
        m["margin_runs"] = np.nan
    m["margin_runs"] = m.margin_runs.fillna(20.0)
    # config-independent eval mask: min prior REAL matches of the two teams
    # (within the gender pool) — never affected by Elo/extra settings
    cnt = defaultdict(int)
    nreal = np.empty(len(m), int)
    for i, r in enumerate(m.itertuples()):
        k1, k2 = (r.gender, r.team1), (r.gender, r.team2)
        nreal[i] = min(cnt[k1], cnt[k2])
        cnt[k1] += 1
        cnt[k2] += 1
    m["nreal"] = nreal
    return m, d, x


def add_dead_rubber(m):
    """Walk-forward dead-rubber flags for franchise group games: within each
    (comp, gap-clustered season), a team is DEAD when it can no longer reach
    the playoff cutoff, LOCKED when it cannot fall out of it, assuming 2 pts
    a win and the season's per-team group-game count (public preseason).
    dr1/dr2 = 1 when that side's team is dead-or-locked and the other is not
    yet decided. ntb (divisional groups) and internationals excluded."""
    m = m.copy()
    m["dr1"] = 0
    m["dr2"] = 0
    dates = pd.to_datetime(m.date)
    for comp, gc in m[(m.comp != "t20s") & (m.comp != "ntb")].groupby("comp"):
        gd = dates[gc.index]
        season = (gd.diff().dt.days.fillna(999) > 45).cumsum()
        spots = PLAYOFF_SPOTS.get(comp, 4)
        for _, sc in gc.groupby(season):
            grp = sc[sc.ko == 0]
            if len(grp) < 10:
                continue
            teams = pd.concat([grp.team1, grp.team2])
            total = teams.value_counts()
            n_gp = int(total.mode().iloc[0])
            pts = defaultdict(int)
            played = defaultdict(int)
            for idx, r in grp.iterrows():
                # standings BEFORE this match
                if played and len(pts) >= spots + 1:
                    rows = [(pts[t], pts[t] + 2 * (n_gp - played[t])) for t in total.index]
                    cur = sorted((c for c, _ in rows), reverse=True)
                    mx = sorted((x for _, x in rows), reverse=True)
                    cut_cur = cur[spots - 1] if len(cur) >= spots else 0
                    cut_mx = mx[spots] if len(mx) > spots else 0
                    flags = []
                    for t in (r.team1, r.team2):
                        maxp = pts[t] + 2 * (n_gp - played[t])
                        dead = maxp < cut_cur                 # cannot reach current cutoff
                        locked = pts[t] > cut_mx              # cannot be caught by 5th's max
                        flags.append(int(dead or locked))
                    if flags[0] != flags[1]:
                        m.loc[idx, "dr1"], m.loc[idx, "dr2"] = flags
                w = r.winner
                for t in (r.team1, r.team2):
                    played[t] += 1
                pts[w] += 2
    return m


# ------------------------------------------------------------------ Elo
def run_elo(m, K, home, regress, scale=400.0, mov=0.0, xfmt=0.0):
    """`extra` rows (cross-format internationals) update ratings at weight
    xfmt and are never scored."""
    """Per-gender pool; franchise teams regress toward their comp mean at
    each season boundary. Returns pre-match P(team1) and prior-match counts."""
    R = {}
    n = defaultdict(int)
    season_seen = {}
    p1 = np.empty(len(m))
    n_min = np.empty(len(m), int)
    comp_sum = defaultdict(float)
    comp_cnt = defaultdict(int)
    for i, r in enumerate(m.itertuples()):
        g = r.gender
        yr = r.date[:4]
        k1, k2 = (g, r.team1), (g, r.team2)
        ck = (g, r.comp)
        if r.comp != "t20s":
            for kk in (k1, k2):
                if season_seen.get((kk, ck)) not in (None, yr):
                    mean = comp_sum[ck] / max(comp_cnt[ck], 1)
                    R[kk] = R.get(kk, 1500.0) + regress * (mean - R.get(kk, 1500.0))
                season_seen[(kk, ck)] = yr
        r1, r2 = R.get(k1, 1500.0), R.get(k2, 1500.0)
        e1 = 1.0 / (1.0 + 10 ** (-((r1 - r2 + home * r.home1) / scale)))
        p1[i] = e1
        n_min[i] = min(n[k1], n[k2])
        kk_mult = 1.0 + mov * np.log1p(abs(r.margin_runs) / 20.0)
        if getattr(r, "extra", 0):
            kk_mult = xfmt
        delta = K * kk_mult * (r.y1 - e1)
        R[k1] = r1 + delta
        R[k2] = r2 - delta
        n[k1] += 1
        n[k2] += 1
        for kk, rr in ((k1, R[k1]), (k2, R[k2])):
            comp_sum[ck] += rr - comp_sum[ck] / max(comp_cnt[ck], 1) if comp_cnt[ck] else rr
            comp_cnt[ck] += 1
    return p1, n_min


def tune_elo(m):
    """Iteration 2: per-segment parameters (the pools are disjoint anyway;
    franchise rosters churn at auctions -> regression, internationals do
    not; the intl logistic scale may sharpen extremes)."""
    out = {}
    for seg in ("intl_m", "intl_f", "fr"):
        ms = m[m.seg == seg]
        xf_opts = XFMT_GRID if seg != "fr" else (0.0,)
        tr = (ms.date >= EVAL_LO) & (ms.date < TRAIN_END) & (ms.extra == 0) \
            & (ms.get("nreal", pd.Series(99, index=ms.index)) >= MIN_PRIOR)
        best, best_ll = None, np.inf
        for K, home, regress, scale, mov in itertools.product(*ELO_GRID.values()):
            for xf in xf_opts:
                p1, nmin = run_elo(ms, K, home, regress, scale, mov, xf)
                mask = tr.to_numpy()
                cur = ll(p1[mask], ms.y1.to_numpy()[mask]).mean()
                if cur < best_ll:
                    best_ll, best = cur, (K, home, regress, scale, mov, xf)
        out[seg] = (best, best_ll)
        print(f"  elo[{seg}]: K={best[0]} home={best[1]} regress={best[2]} "
              f"scale={best[3]:.0f} mov={best[4]} xfmt={best[5]}  train LL={best_ll:.5f}")
    return out


def run_elo_seg(m, elo_params):
    """m here includes extra rows (sorted by date); returns arrays aligned
    to m — callers slice back to real rows via m.extra == 0."""
    p1 = np.empty(len(m)); nmin = np.empty(len(m), int)
    for seg in ("intl_m", "intl_f", "fr"):
        idx = (m.seg == seg).to_numpy()
        (K, home, regress, scale, mov, xf), _ = elo_params[seg]
        ps, ns = run_elo(m[idx], K, home, regress, scale, mov, xf)
        p1[idx] = ps; nmin[idx] = ns
    return p1, nmin


# ------------------------------------------------- player composition v2
def per_match_player_tables(d):
    bat = (d.groupby(["match_id", "batter", "phase"])
           .agg(balls=("runs_batter", "size"), runs=("runs_batter", "sum"))
           .reset_index())
    bowl = (d.groupby(["match_id", "bowler", "phase"])
            .agg(balls=("runs_total", "size"), runs=("runs_total", "sum"),
                 wkts=("wicket", "sum")).reset_index())
    return ({k: v for k, v in bat.groupby("match_id")},
            {k: v for k, v in bowl.groupby("match_id")})


def player_pass(m, bat_g, bowl_g, wicket_val, shrink):
    """Chronological pass; returns diff (runs per 120 balls, team1 - team2)
    and a per-team data-richness weight."""
    base = defaultdict(lambda: np.array([1.25, 1.20, 1.55]))   # per (comp,gender) phase rpb
    bval = defaultdict(float); bwt = defaultdict(float); bballs = defaultdict(float)
    oval = defaultdict(float); owt = defaultdict(float); oballs = defaultdict(float)
    roster = defaultdict(dict)
    diff = np.empty(len(m)); rich = np.empty(len(m))

    def team_value(team):
        ros = roster[team]
        if not ros:
            return 0.0, 0.0
        top = sorted(ros.items(), key=lambda kv: -kv[1])[:11]
        bw = sum(w * bballs[p] for p, w in top)
        tb = (sum(w * bballs[p] * bval[p] * min(bwt[p] / shrink, 1.0) for p, w in top)
              / bw) if bw > 0 else 0.0
        ow = sum(w * oballs[p] for p, w in top)
        to = (sum(w * oballs[p] * oval[p] * min(owt[p] / shrink, 1.0) for p, w in top)
              / ow) if ow > 0 else 0.0
        seen = sum(min(bwt[p] / shrink, 1.0) for p, _ in top) / max(len(top), 1)
        return tb + to, seen

    for i, r in enumerate(m.itertuples()):
        (v1, s1), (v2, s2) = team_value(r.team1), team_value(r.team2)
        diff[i] = 120.0 * (v1 - v2)
        rich[i] = min(s1, s2)
        bk = (r.comp, r.gender)
        bt, bo = bat_g.get(r.match_id), bowl_g.get(r.match_id)
        if bt is not None:
            for p, ph, balls, runs in zip(bt.batter, bt.phase, bt.balls, bt.runs):
                delta = runs / balls - base[bk][ph]
                a = 1 - (1 - VAL_ALPHA) ** balls
                bval[p] = (1 - a) * bval[p] + a * delta
                bwt[p] = bwt[p] * (1 - VAL_ALPHA) ** balls + balls
            pm_balls = bt.groupby("batter").balls.sum()
            for p, nb in pm_balls.items():
                bballs[p] = 0.7 * bballs[p] + 0.3 * nb if bballs[p] else float(nb)
        if bo is not None:
            for p, ph, balls, runs, wk in zip(bo.bowler, bo.phase, bo.balls, bo.runs, bo.wkts):
                delta = (base[bk][ph] - runs / balls) + wicket_val * wk / balls
                a = 1 - (1 - VAL_ALPHA) ** balls
                oval[p] = (1 - a) * oval[p] + a * delta
                owt[p] = owt[p] * (1 - VAL_ALPHA) ** balls + balls
            pm_balls = bo.groupby("bowler").balls.sum()
            for p, nb in pm_balls.items():
                oballs[p] = 0.7 * oballs[p] + 0.3 * nb if oballs[p] else float(nb)
        if bt is not None:
            for ph, g2 in bt.groupby("phase"):
                rpb = g2.runs.sum() / g2.balls.sum()
                base[bk] = base[bk].copy()
                base[bk][ph] = 0.99 * base[bk][ph] + 0.01 * rpb
        played = set()
        if bt is not None:
            played |= set(bt.batter)
        if bo is not None:
            played |= set(bo.bowler)
        for team, xi in ((r.team1, r.xi1), (r.team2, r.xi2)):
            names = set(xi) if len(xi) else played
            ros = roster[team]
            for p in list(ros):
                ros[p] *= (1 - APP_ALPHA)
            for p in names:
                ros[p] = ros.get(p, 0.0) + APP_ALPHA
    return diff, rich


def tune_player(m, bat_g, bowl_g):
    tr = ((m.date >= EVAL_LO) & (m.date < TRAIN_END)).to_numpy()
    y = m.y1.to_numpy()
    best, best_ll = None, np.inf
    for wv, sh in itertools.product(WICKET_GRID, SHRINK_GRID):
        diff, rich = player_pass(m, bat_g, bowl_g, wv, sh)
        mask = tr & (rich > 0.3)
        for sig in SIGMA_GRID:
            from scipy.stats import norm as _n
            p = _n.cdf(diff[mask] / sig)
            cur = ll(p, y[mask]).mean()
            if cur < best_ll:
                best_ll, best = cur, (wv, sh, sig, diff, rich)
    return best, best_ll


# ------------------------------------------------------------------ blend
# piecewise-monotone logit map: slopes on |z| in [0,1], (1,2], (2,inf),
# output capped at |z'| <= Z_CAP. Sharpens well-ordered mid/high regions
# without the cubic's tail blow-ups (which turned modest wrong-side errors
# into catastrophic 0.06-vs-market-0.34 rows — dev diagnostic 2026-08-30).
KO_GRID = (0.6, 0.8, 1.0)     # knockout temperature (late-season dev finding)
DR_GRID = (0.0, 0.2, 0.4)     # logit penalty toward the alive team when the
                              # other is a dead rubber (eliminated/locked)
PLAYOFF_SPOTS = {"hnd": 3}    # default 4; ntb (divisional groups) excluded
S1_GRID = (0.8, 1.0, 1.25)
S2_GRID = (0.5, 1.0, 1.5, 2.0)
S3_GRID = (0.0, 0.5, 1.0)
Z_CAP = 3.2


def zmap(z, s1, s2, s3):
    a = np.abs(z)
    out = np.where(a <= 1, s1 * a,
                   np.where(a <= 2, s1 + s2 * (a - 1), s1 + s2 + s3 * (a - 2)))
    return np.sign(z) * np.minimum(out, Z_CAP)


def fit_blend(m, zs, nmin, rich, ko=None):
    """Per-segment simplex weights over the components plus a cubic-logit
    link z' = b*z + c*z^3 (b = temperature, c sharpens extremes), all on
    train-era rows."""
    y = m.y1.to_numpy()
    tr = ((m.date >= EVAL_LO) & (m.date < TRAIN_END)).to_numpy()
    names = list(zs)
    params = {}
    grid = [w for w in itertools.product((0, 0.25, 0.5, 0.75, 1.0), repeat=len(names))
            if abs(sum(w) - 1.0) < 1e-9]
    for seg in ("intl_m", "intl_f", "fr"):
        mask = tr & (m.seg == seg).to_numpy() & (nmin >= MIN_PRIOR)  # nmin arg = nreal
        kof = (ko if ko is not None else m.ko.to_numpy())[mask]
        drd = (m.dr1.to_numpy() - m.dr2.to_numpy())[mask]   # +1: team1 dead
        dr_opts = DR_GRID if seg == "fr" else (0.0,)
        best, best_ll = None, np.inf
        for w in grid:
            z = sum(wi * zs[n][mask] for wi, n in zip(w, names))
            for s1, s2, s3 in itertools.product(S1_GRID, S2_GRID, S3_GRID):
                zm0 = zmap(z, s1, s2, s3)
                for tk in KO_GRID:
                    zm1 = np.where(kof == 1, tk * zm0, zm0)
                    for dr in dr_opts:
                        p = inv(zm1 - dr * drd)
                        cur = ll(p, y[mask]).mean()
                        if cur < best_ll:
                            best_ll, best = cur, (w, s1, s2, s3, tk, dr)
        params[seg] = best
        print(f"  blend[{seg}]: w={dict(zip(names, best[0]))} slopes="
              f"{best[1:4]} t_ko={best[4]} dr={best[5]} (train LL {best_ll:.5f}, "
              f"n={int(mask.sum())}, ko n={int(kof.sum())}, dr n={int((drd != 0).sum())})")
    return params


def blended_p(m, zs, params):
    names = list(zs)
    out = np.empty(len(m))
    for seg in ("intl_m", "intl_f", "fr"):
        mask = (m.seg == seg).to_numpy()
        w, s1, s2, s3, tk, dr = params[seg]
        z = sum(wi * zs[n][mask] for wi, n in zip(w, names))
        zm = zmap(z, s1, s2, s3)
        kof = m.ko.to_numpy()[mask]
        drd = (m.dr1.to_numpy() - m.dr2.to_numpy())[mask]
        out[mask] = inv(np.where(kof == 1, tk * zm, zm) - dr * drd)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dev", action="store_true")
    args = ap.parse_args()
    m, d, x = load()
    print(f"matches {len(m)} ({m.date.min()}..{m.date.max()}), deliveries {len(d)}, "
          f"extra intl results {len(x)}")
    x["nreal"] = 0
    mx = pd.concat([m, x[["match_id", "comp", "gender", "date", "team1", "team2",
                          "city", "winner", "result", "home1", "y1", "seg",
                          "margin_runs", "extra", "nreal"]]], ignore_index=True)
    mx = mx.sort_values(["date", "match_id"]).reset_index(drop=True)
    elo_params = tune_elo(mx)
    p_elo_x, nmin_x = run_elo_seg(mx, elo_params)
    real = (mx.extra == 0).to_numpy()
    order = mx[real].match_id.to_numpy()
    pos = pd.Series(np.arange(real.sum()), index=order).reindex(m.match_id).to_numpy()
    p_elo, nmin = p_elo_x[real][pos], nmin_x[real][pos]
    bat_g, bowl_g = per_match_player_tables(d)
    (wv, sh, sig, diff, rich), pll = tune_player(m, bat_g, bowl_g)
    print(f"player2: wicket_val={wv} shrink={sh} sigma={sig}  train LL={pll:.5f}")
    from scipy.stats import norm as _n
    p_pl = _n.cdf(diff / sig)
    # v1 player component (the July model class, alpha=0.002, sigma=40):
    import fp_player_model as pmv1
    bat1 = (d.groupby(["match_id", "batter"]).agg(balls=("runs_batter", "size"),
            runs=("runs_batter", "sum")).reset_index())
    bowl1 = (d.groupby(["match_id", "bowler"]).agg(balls=("runs_total", "size"),
             runs=("runs_total", "sum"), wkts=("wicket", "sum")).reset_index())
    vals1 = pmv1.ratings_pass(m, bat1, bowl1, 0.002)
    diff1 = np.array([120 * ((vals1[r.match_id][r.team1][0] + vals1[r.match_id][r.team1][1])
                             - (vals1[r.match_id][r.team2][0] + vals1[r.match_id][r.team2][1]))
                      for r in m.itertuples()])
    p_v1 = _n.cdf(diff1 / 40.0)
    zs = {"elo": logit(p_elo), "pl2": logit(p_pl), "pl1": logit(p_v1)}
    params = fit_blend(m, zs, m.nreal.to_numpy(), rich)
    p_blend = blended_p(m, zs, params)

    tr = ((m.date >= EVAL_LO) & (m.date < TRAIN_END)).to_numpy() & (m.nreal.to_numpy() >= MIN_PRIOR)
    y = m.y1.to_numpy()
    print("\ntrain-era walk-forward LL (2018..2024-05, >=10 prior real matches):")
    for name, p in (("elo", p_elo), ("player2", p_pl), ("player1", p_v1), ("blend", p_blend)):
        print(f"  {name:8s} {ll(p[tr], y[tr]).mean():.5f}")
        for seg in ("intl_m", "intl_f", "fr"):
            msk = tr & (m.seg == seg).to_numpy()
            print(f"      {seg}: {ll(p[msk], y[msk]).mean():.5f} (n={int(msk.sum())})")

    if not args.dev:
        return
    # ---------------- dev scoring (logged touch of the P population) ----
    b = pd.read_parquet(os.path.join(ROOT, "data", "pm_benchmark.parquet"))
    xw = pd.read_parquet(os.path.join(ROOT, "data", "pm_crosswalk.parquet"))
    b = b.merge(xw[xw.status == "ok"][["market_id", "match_id", "outcome0_is_team1"]], on="market_id")
    pmap = pd.Series(p_blend, index=m.match_id)
    b = b[b.match_id.isin(pmap.index)].copy()
    p1 = pmap.reindex(b.match_id).to_numpy()
    b["p_model"] = np.where(b.outcome0_is_team1, p1, 1 - p1)
    b["seg"] = np.where(b.comp == "t20s", "international", "franchise")
    b["ll_model"], b["ll_open"], b["ll_close"] = ll(b.p_model, b.y), ll(b.p_open, b.y), ll(b.p_close, b.y)
    print(f"\n== DEV (registration-P population): n={len(b)} ==")
    dd, t = clustered_t((b.ll_model - b.ll_open).values, b.date)
    print(f"pooled model-open = {dd:+.5f} (clustered t={t:.1f})  cal={100*(b.p_model.mean()-b.y.mean()):+.2f}pp")
    for seg, g in b.groupby("seg"):
        d2, t2 = clustered_t((g.ll_model - g.ll_open).values, g.date)
        print(f"  {seg:13s} n={len(g):3d} model-open={d2:+.5f} (t={t2:.1f}) "
              f"cal={100*(g.p_model.mean()-g.y.mean()):+.1f}pp  LL(model)={g.ll_model.mean():.4f} LL(open)={g.ll_open.mean():.4f}")
    d3, t3 = clustered_t((b.ll_model - b.ll_close).values, b.date)
    print(f"vs pre-toss close: {d3:+.5f} (t={t3:.1f})")
    roi(b, "p_model", "dev")
    roi(b.assign(p_model=b.p_open), "p_model", "dev PLACEBO")
    b.to_parquet(os.path.join(ROOT, "data", "pm_preds_v2.parquet"), index=False)


if __name__ == "__main__":
    main()
