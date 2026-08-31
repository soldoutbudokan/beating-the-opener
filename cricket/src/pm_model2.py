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
XFMT_GRID = (0.0, 0.25, 0.5, 0.75, 1.0)   # weight of cross-format results
                               # (ODI/ODM/IT20) as extra Elo observations
# membership-tier prior on the INITIAL rating: results cannot resolve the
# class gap between tiers that rarely play each other (dev diagnostic: mixed
# full-member-vs-associate fixtures carry ~10 of 17 intl LL-loss units).
# Public, stable membership lists; the tier GAPS are fit on train.
TIER_FULL = {"India", "Australia", "England", "Pakistan", "South Africa",
             "New Zealand", "Sri Lanka", "Bangladesh", "West Indies",
             "Afghanistan", "Zimbabwe", "Ireland"}
TIER_MID = {"Scotland", "Netherlands", "Nepal", "Oman", "United Arab Emirates",
            "Namibia", "United States of America", "Canada",
            "Papua New Guinea", "Hong Kong", "Uganda", "Jersey", "Italy",
            "Kenya", "Qatar", "Kuwait", "Bahrain", "Malaysia", "Singapore",
            "Thailand", "Bermuda", "Denmark", "Germany", "Guernsey"}
SEED_GRID = (0.0, 100.0, 200.0, 350.0)   # debutant seeded at
                                         # opponent_rating - seed_delta: a
                                         # team's first fixture reveals its
                                         # class (associates play associates),
                                         # and the opponent is known pre-match
# both grids were boundary-limited on the first fit (women chose the maximum
# 450, i.e. still under-separated: women's associates trail full members by
# far more than men's do). Widened 2026-08-30.
TIER_A_GRID = (0, 150, 300, 450, 600, 800)   # full-member offset above MID
TIER_B_GRID = (0, 150, 300, 450)             # MID offset above the rest


def tier_offsets(a, b):
    def off(team):
        if team in TIER_FULL:
            return a
        if team in TIER_MID:
            return 0.0
        return -b
    return off
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
    m["home1_name"] = [home_flag(t, c) for t, c in zip(m.team1, m.city)]
    m["home1_learned"] = learned_home(m)
    m["home1_country"] = venue_country_home(m)
    m["home1"] = m.home1_name          # default; run_elo picks per segment
    m["y1"] = (m.winner == m.team1).astype(float)
    m["seg"] = np.where(m.comp == "t20s",
                        np.where(m.gender == "female", "intl_f", "intl_m"), "fr")
    st = m.get("stage", pd.Series(None, index=m.index)).fillna("").str.lower()
    m["ko"] = st.str.contains("final|qualifier|eliminator|play-off|playoff|semi").astype(int)
    m = add_dead_rubber(m)
    m = add_series_state(m)
    x = pd.read_parquet(os.path.join(ROOT, "data", "matches_extra.parquet"))
    x = x[(x.result == "normal") & x.winner.notna()].copy()
    x["home1"] = [home_flag(t, c) for t, c in zip(x.team1, x.city)]
    x["venue"] = None
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


def venue_country_home(m):
    """Home indicator via venue -> country, learned from which domestic
    league plays at each venue (and from city names for venues seen only in
    internationals, via the same league-derived city map)."""
    vmap, cmap = {}, {}
    for r in m.itertuples():
        c = COMP_COUNTRY.get(r.comp)
        if not c:
            continue
        if isinstance(getattr(r, "venue", None), str):
            vmap.setdefault(r.venue, c)
        if isinstance(getattr(r, "city", None), str):
            cmap.setdefault(r.city, c)
    out = np.zeros(len(m), int)
    for i, r in enumerate(m.itertuples()):
        country = vmap.get(getattr(r, "venue", None)) or cmap.get(getattr(r, "city", None))
        if country and r.team1 == country:
            out[i] = 1
    return out


def learned_home(m, lookback_days=1100, share=0.35):
    """Walk-forward home indicator: team1 is home when it has played a
    dominant share of this VENUE's matches over the prior ~3 years and more
    than team2 (learned, never the venue string). The name-token rule missed
    ~3 of 10 IPL sides (Rajasthan at Jaipur, Gujarat at Ahmedabad, Punjab at
    Mohali). Falls back to the name-token rule when the venue is new."""
    out = np.zeros(len(m), int)
    dates = pd.to_datetime(m.date).to_numpy()
    hist = defaultdict(list)          # venue -> [(date, team1, team2)]
    for i, r in enumerate(m.itertuples()):
        v = r.venue if isinstance(getattr(r, "venue", None), str) else None
        prev = hist.get(v, []) if v else []
        lo = dates[i] - np.timedelta64(lookback_days, "D")
        recent = [(t1, t2) for dt, t1, t2 in prev if dt >= lo]
        if len(recent) >= 8:
            n = len(recent)
            s1 = sum((r.team1 == a) + (r.team1 == b) for a, b in recent) / n
            s2 = sum((r.team2 == a) + (r.team2 == b) for a, b in recent) / n
            if s1 >= share and s1 > s2:
                out[i] = 1
        else:
            out[i] = home_flag(r.team1, r.city)
        if v:
            hist[v].append((dates[i], r.team1, r.team2))
    return out


def add_series_state(m):
    """Bilateral-series dead rubbers for internationals: within one event
    (series) between the same two teams, a match played at 2+ wins of
    separation from match 3 onward is very likely a rotation game. Uses only
    prior results in the same series plus the published match number."""
    m = m.copy()
    m["sr1"] = 0
    m["sr2"] = 0
    if "event_name" not in m.columns:
        return m
    intl = m[(m.comp == "t20s") & m.event_name.notna()
             & m.match_number.notna()]
    for (_, pair), g in intl.groupby(
            [intl.event_name, intl.apply(lambda r: tuple(sorted((r.team1, r.team2))), axis=1)]):
        g = g.sort_values(["match_number", "date"])
        wins = defaultdict(int)
        for idx, r in g.iterrows():
            try:
                mn = int(r.match_number)
            except (TypeError, ValueError):
                mn = 0
            if mn >= 3 and wins:
                lead = max(wins.values())
                trail = min(wins.get(t, 0) for t in (r.team1, r.team2))
                if lead - trail >= 2:
                    leader = max(wins, key=wins.get)
                    m.loc[idx, "sr1"] = int(r.team1 == leader)
                    m.loc[idx, "sr2"] = int(r.team2 == leader)
            wins[r.winner] += 1
    return m


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
def run_elo(m, K, home, regress, scale=400.0, mov=0.0, xfmt=0.0, tier=None,
            home_mode="name", seed_delta=0.0):
    """`extra` rows (cross-format internationals) update ratings at weight
    xfmt and are never scored. `tier`: (a, b) membership-class offsets on
    the initial rating (intl segments only)."""
    off = tier_offsets(*tier) if tier else (lambda t: 0.0)
    hcol = m[f"home1_{home_mode}"].to_numpy() if f"home1_{home_mode}" in m else m.home1.to_numpy()
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
        r1, r2 = R.get(k1), R.get(k2)
        if r1 is None:
            r1 = (r2 - seed_delta if (r2 is not None and seed_delta)
                  else 1500.0 + off(r.team1))
        if r2 is None:
            r2 = (R[k1] - seed_delta if (k1 in R and seed_delta)
                  else 1500.0 + off(r.team2))
        e1 = 1.0 / (1.0 + 10 ** (-((r1 - r2 + home * hcol[i]) / scale)))
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
        tier_opts = ([(a, b) for a in TIER_A_GRID for b in TIER_B_GRID]
                     if seg != "fr" else [(0, 0)])
        tr = (ms.date >= EVAL_LO) & (ms.date < TRAIN_END) & (ms.extra == 0) \
            & (ms.get("nreal", pd.Series(99, index=ms.index)) >= MIN_PRIOR)
        best, best_ll = None, np.inf
        # staged search: core Elo first (tier=0), then the tier offsets with
        # the core fixed — the full product is ~15k sequential passes.
        yv = ms.y1.to_numpy()
        for K, home, regress, scale, mov in itertools.product(*ELO_GRID.values()):
            for xf in xf_opts:
                # stage 1 fixes the home rule (stage 2 re-opens it jointly
                # with the tier prior and the seed rule)
                p1, _ = run_elo(ms, K, home, regress, scale, mov, xf, (0, 0), "name")
                cur = ll(p1[tr.to_numpy()], yv[tr.to_numpy()]).mean()
                if cur < best_ll:
                    best_ll, best = cur, (K, home, regress, scale, mov, xf,
                                          (0, 0), "name")
        if len(tier_opts) > 1:
            K, home, regress, scale, mov, xf, _, _ = best
            # stage 2 re-opens home_mode jointly with the tier prior and the
            # opponent-seed delta: the right home rule depends on how new
            # teams are seeded (a 2026-08-30 miss - stage 1 locked the old
            # rule before the priors existed)
            for tier in tier_opts:
                for hm2 in HOME_MODES:
                    for hv in (0, 40, 80):
                        for sd in SEED_GRID:
                            p1, _ = run_elo(ms, K, hv, regress, scale, mov, xf,
                                            tier, hm2, sd)
                            cur = ll(p1[tr.to_numpy()], yv[tr.to_numpy()]).mean()
                            if cur < best_ll:
                                best_ll, best = cur, (K, hv, regress, scale, mov,
                                                      xf, tier, hm2, sd)
        if len(best) == 8:
            best = best + (0.0,)
        out[seg] = (best, best_ll)
        print(f"  elo[{seg}]: K={best[0]} home={best[1]} regress={best[2]} "
              f"scale={best[3]:.0f} mov={best[4]} xfmt={best[5]} tier={best[6]} "
              f"home_mode={best[7]} seed_delta={best[8]}  train LL={best_ll:.5f}")
    return out


def run_elo_seg(m, elo_params):
    """m here includes extra rows (sorted by date); returns arrays aligned
    to m — callers slice back to real rows via m.extra == 0."""
    p1 = np.empty(len(m)); nmin = np.empty(len(m), int)
    for seg in ("intl_m", "intl_f", "fr"):
        idx = (m.seg == seg).to_numpy()
        (K, home, regress, scale, mov, xf, tier, hm, sd), _ = elo_params[seg]
        ps, ns = run_elo(m[idx], K, home, regress, scale, mov, xf, tier, hm, sd)
        p1[idx] = ps; nmin[idx] = ns
    return p1, nmin


# ------------------------------------------------------ Bradley-Terry
BT_LAMBDA = (0.5, 1.0, 3.0)        # ridge strength toward the tier prior
BT_HALFLIFE = (365.0, 730.0, 1460.0)   # days
BT_REFIT_DAYS = 30


def _bt_fit(i1, i2, hcol, y, w, n_teams, prior, lam, iters=25):
    """Weighted ridge Bradley-Terry by Newton's method, ridge centred on
    `prior` (the membership-tier offsets). Returns (ratings, home_coef)."""
    r = prior.copy()
    h = 0.0
    for _ in range(iters):
        z = r[i1] - r[i2] + h * hcol
        p = 1.0 / (1.0 + np.exp(-z))
        resid = w * (y - p)
        g = np.zeros(n_teams + 1)
        np.add.at(g, i1, resid)
        np.add.at(g, i2, -resid)
        g[-1] = float((resid * hcol).sum())
        g[:n_teams] -= lam * (r - prior)
        wp = w * p * (1 - p)
        diag = np.zeros(n_teams + 1)
        np.add.at(diag, i1, wp)
        np.add.at(diag, i2, wp)
        diag[-1] = float((wp * hcol * hcol).sum())
        diag[:n_teams] += lam
        diag = np.maximum(diag, 1e-6)
        step = g / diag                      # diagonal (Jacobi) Newton step
        step = np.clip(step, -0.5, 0.5)
        r = r + 0.7 * step[:n_teams]
        h = h + 0.7 * step[-1]
        if np.max(np.abs(step)) < 1e-5:
            break
    return r, h


def bt_ratings(m, halflife, lam, tier, refit_days=BT_REFIT_DAYS,
               home_col="home1_country"):
    """Time-decayed, ridge-regularised Bradley-Terry ratings, refit every
    `refit_days` on all STRICTLY PRIOR matches.

    Why not Elo: international schedules are sparse and clustered (associates
    play only associates, for a fortnight, then not for a year). Elo's
    sequential single-match updates propagate that badly, while a joint
    maximum-likelihood fit uses every indirect comparison at once and the
    ridge keeps rarely-seen teams at their membership-tier prior instead of
    drifting. Results only; no market quantity anywhere.
    """
    teams = pd.unique(pd.concat([m.team1, m.team2]))
    tidx = {t: i for i, t in enumerate(teams)}
    off = tier_offsets(*tier) if tier else (lambda t: 0.0)
    prior = np.array([off(t) / 300.0 for t in teams], float)
    dates = pd.to_datetime(m.date).to_numpy()
    y = m.y1.to_numpy(float)
    hcol = (m[home_col].to_numpy(float) if home_col in m
            else np.zeros(len(m), float))
    i1 = m.team1.map(tidx).to_numpy()
    i2 = m.team2.map(tidx).to_numpy()
    base_w = (np.where(m.extra.to_numpy() == 1, 0.5, 1.0)
              if "extra" in m else np.ones(len(m)))
    out = np.zeros(len(m))
    r, h = prior.copy(), 0.0
    last_fit = None
    for i in range(len(m)):
        d = dates[i]
        if last_fit is None or (d - last_fit) / np.timedelta64(1, "D") >= refit_days:
            j = int(np.searchsorted(dates, d))       # strictly prior rows
            if j >= 30:
                age = (d - dates[:j]) / np.timedelta64(1, "D")
                w = base_w[:j] * 0.5 ** (age / halflife)
                keep = w > 1e-3
                if keep.sum() >= 30:
                    r, h = _bt_fit(i1[:j][keep], i2[:j][keep], hcol[:j][keep],
                                   y[:j][keep], w[keep], len(teams), prior, lam)
            last_fit = d
        out[i] = r[i1[i]] - r[i2[i]] + h * hcol[i]
    return out


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
    last_xi = {}                      # team -> its most recent XI
    last_series_xi = {}               # (team, event) -> its last XI in that event
    hist_q = {}                       # team -> EW quality of XIs actually fielded
    diff = np.empty(len(m)); rich = np.empty(len(m)); rot = np.empty(len(m))

    def expected_xi(team, event):
        """Who is likely to play, strictly from prior matches. Measured
        overlap with the actual XI (2026-08-30): last XI in the SAME series
        0.84 international / 0.86 franchise, last XI anywhere 0.69 / 0.74,
        appearance-weighted roster 0.61 / 0.70. Series continuity is the
        single most informative pre-match fact available to us, and the
        smoothed roster was throwing it away."""
        xi = last_series_xi.get((team, event)) if event else None
        if not xi:
            xi = last_xi.get(team)
        if xi:
            return [(p, 1.0) for p in xi]
        ros = roster[team]
        return sorted(ros.items(), key=lambda kv: -kv[1])[:11] if ros else []

    def team_value(team, event=None):
        top = expected_xi(team, event)
        if not top:
            return 0.0, 0.0
        bw = sum(w * bballs[p] for p, w in top)
        tb = (sum(w * bballs[p] * bval[p] * min(bwt[p] / shrink, 1.0) for p, w in top)
              / bw) if bw > 0 else 0.0
        ow = sum(w * oballs[p] for p, w in top)
        to = (sum(w * oballs[p] * oval[p] * min(owt[p] / shrink, 1.0) for p, w in top)
              / ow) if ow > 0 else 0.0
        seen = sum(min(bwt[p] / shrink, 1.0) for p, _ in top) / max(len(top), 1)
        return tb + to, seen

    def xi_quality(names):
        vals = [bval[p] * min(bwt[p] / shrink, 1.0)
                + oval[p] * min(owt[p] / shrink, 1.0) for p in names]
        return float(np.mean(vals)) if vals else np.nan

    for i, r in enumerate(m.itertuples()):
        ev = getattr(r, "event_name", None)
        (v1, s1), (v2, s2) = team_value(r.team1, ev), team_value(r.team2, ev)
        diff[i] = 120.0 * (v1 - v2)
        rich[i] = min(s1, s2)
        # rotation signal: expected-XI quality vs the EW quality of the XIs
        # this team has actually fielded (both strictly prior). A team whose
        # likely XI is weaker than the one that earned its rating is being
        # rotated - the single biggest thing a results-only rating misses.
        rq = []
        for team in (r.team1, r.team2):
            top = [p for p, _ in expected_xi(team, ev)]
            q_now = xi_quality(top) if top else np.nan
            q_hist = hist_q.get(team, np.nan)
            rq.append(q_now - q_hist if np.isfinite(q_now) and np.isfinite(q_hist) else 0.0)
        rot[i] = 120.0 * (rq[0] - rq[1])
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
            if len(xi):
                last_xi[team] = list(xi)
                if ev:
                    last_series_xi[(team, ev)] = list(xi)
            q = xi_quality(list(names))
            if np.isfinite(q):
                hist_q[team] = (0.8 * hist_q[team] + 0.2 * q
                                if team in hist_q else q)
            ros = roster[team]
            for p in list(ros):
                ros[p] *= (1 - APP_ALPHA)
            for p in names:
                ros[p] = ros.get(p, 0.0) + APP_ALPHA
    return diff, rich, rot


def tune_player(m, bat_g, bowl_g):
    tr = ((m.date >= EVAL_LO) & (m.date < TRAIN_END)).to_numpy()
    y = m.y1.to_numpy()
    best, best_ll = None, np.inf
    for wv, sh in itertools.product(WICKET_GRID, SHRINK_GRID):
        diff, rich, rot = player_pass(m, bat_g, bowl_g, wv, sh)
        mask = tr & (rich > 0.3)
        for sig in SIGMA_GRID:
            from scipy.stats import norm as _n
            p = _n.cdf(diff[mask] / sig)
            cur = ll(p, y[mask]).mean()
            if cur < best_ll:
                best_ll, best = cur, (wv, sh, sig, diff, rich, rot)
    return best, best_ll


# ------------------------------------------------------------------ blend
# piecewise-monotone logit map: slopes on |z| in [0,1], (1,2], (2,inf),
# output capped at |z'| <= Z_CAP. Sharpens well-ordered mid/high regions
# without the cubic's tail blow-ups (which turned modest wrong-side errors
# into catastrophic 0.06-vs-market-0.34 rows — dev diagnostic 2026-08-30).
KO_GRID = (0.6, 0.8, 1.0)     # knockout temperature (late-season dev finding)
ROT_GRID = (0.0, 0.25, 0.5, 1.0)   # coefficient on the XI-quality delta
SR_GRID = (-0.3, -0.15, 0.0, 0.15, 0.3)   # logit shift for the leader of a
                              # decided bilateral series. Sign left to train:
                              # rotation would push it negative, momentum /
                              # residual strength positive (leaders win 75%
                              # unconditionally, so Elo may not absorb it all)
DR_GRID = (0.0, 0.2, 0.4)     # logit penalty toward the alive team when the
                              # other is a dead rubber (eliminated/locked)
PLAYOFF_SPOTS = {"hnd": 3}    # default 4; ntb (divisional groups) excluded
HOME_MODES = ("name", "learned", "country")   # per-segment, tuned on train
# each domestic league sits in one country, so the venues it uses identify
# that country; an international at such a venue is a home game for that
# country's team. (Team names ARE country names in internationals, so no
# extra mapping is needed.) Market-free, public, and it fixes the fact that
# "India" never token-matches "Mumbai".
COMP_COUNTRY = {"ipl": "India", "wpl": "India", "bbl": "Australia",
                "wbb": "Australia", "psl": "Pakistan", "cpl": "West Indies",
                "ntb": "England", "hnd": "England", "sat": "South Africa",
                "ilt": "United Arab Emirates",
                "mlc": "United States of America", "lpl": "Sri Lanka",
                "bpl": "Bangladesh", "ssm": "New Zealand"}
ONLINE_N = (100, 200, 400, 800, 1600)   # rolling window, tuned PER SEGMENT
ONLINE_PRIOR = (5.0, 20.0, 60.0)        # pseudo-observations pulling toward
                                        # the train-fit map (shrinks early
                                        # windows). An in-sample isotonic
                                        # oracle on dev says our ORDERING is
                                        # already good enough to beat the
                                        # open in both cells (0.674 vs 0.689
                                        # franchise, 0.506 vs 0.524 intl) -
                                        # what is missing is the probability
                                        # map, so this is the lever that
                                        # matters most.
RICH_GRID = (0.0, 0.5, 1.0)   # exponent on data-richness scaling of the
                              # player component (associates barely appear in
                              # the ball-by-ball archive -> shrink their value)
S1_GRID = (0.8, 1.0, 1.25)
S2_GRID = (0.5, 1.0, 1.5, 2.0, 2.5)
S3_GRID = (0.0, 0.5, 1.0, 1.5)
ZCAP_GRID = (1.8, 2.4, 3.2, 4.0)   # per-segment ceiling on expressed confidence.
                              # T20 is a high-variance format: even a vastly
                              # better side rarely exceeds ~85-90%, and an
                              # uncapped rating gap produced a 0.06 quote on a
                              # full-member women's match that duly lost
                              # (dev diagnostic 2026-08-30). Tuned on train.


def zmap(z, s1, s2, s3, zcap=3.2):
    a = np.abs(z)
    out = np.where(a <= 1, s1 * a,
                   np.where(a <= 2, s1 + s2 * (a - 1), s1 + s2 + s3 * (a - 2)))
    return np.sign(z) * np.minimum(out, zcap)


def fit_blend(m, zs, nmin, rich, ko=None, rot=None):
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
        srd = (m.sr1.to_numpy() - m.sr2.to_numpy())[mask]   # +1: team1 leads a decided series
        dr_opts = DR_GRID if seg == "fr" else (0.0,)
        sr_opts = SR_GRID if seg != "fr" else (0.0,)
        rv = np.clip(rich[mask], 0.0, 1.0)
        rt = np.clip((rot if rot is not None else np.zeros(len(m)))[mask], -3, 3)
        best, best_ll = None, np.inf
        for w, rex in itertools.product(grid, RICH_GRID):
            scale_pl = rv ** rex if rex else np.ones_like(rv)
            z = sum(wi * (zs[n][mask] * (scale_pl if n != "elo" else 1.0))
                    for wi, n in zip(w, names))
            for s1, s2, s3, zc in itertools.product(S1_GRID, S2_GRID, S3_GRID,
                                                    ZCAP_GRID):
                zm0 = zmap(z, s1, s2, s3, zc)
                for tk in KO_GRID:
                    zm1 = np.where(kof == 1, tk * zm0, zm0)
                    for dr in dr_opts:
                        for sr in sr_opts:
                            base = zm1 - dr * drd + sr * srd
                            for rc in ROT_GRID:
                                p = inv(base + rc * rt)
                                cur = ll(p, y[mask]).mean()
                                if cur < best_ll:
                                    best_ll, best = cur, (w, s1, s2, s3, tk, dr,
                                                          rex, sr, rc, zc)
        params[seg] = best
        print(f"  blend[{seg}]: w={dict(zip(names, best[0]))} slopes="
              f"{best[1:4]} t_ko={best[4]} dr={best[5]} rich_exp={best[6]} "
              f"sr={best[7]} rot={best[8]} zcap={best[9]} "
              f"(train LL {best_ll:.5f}, n={int(mask.sum())}, "
              f"ko n={int(kof.sum())}, dr n={int((drd != 0).sum())}, "
              f"sr n={int((srd != 0).sum())})")
    return params


def online_recalibrate(m, z, params_by_seg, win, prior_n, only_seg=None):
    """Walk-forward logistic recalibration of the blended logit, per segment,
    using ONLY this model's own earlier predictions and their results (no
    market data, no future data). Pulls toward the train-fit identity map
    with `prior_n` pseudo-observations, so early windows are not noisy.

    Why: the blend's confidence is fit on the train era, where the model has
    ~0.013 of skill over a coin flip on franchise cricket; in a different
    regime (or a sample of tighter games) that confidence is too high, and
    log loss punishes it. Tracking realized reliability online fixes the
    scale without anyone telling us the answer.
    """
    z = np.asarray(z, float)
    y = m.y1.to_numpy(float)
    out = np.array(z, copy=True)
    for seg in m.seg.unique():
        if only_seg is not None and seg != only_seg:
            continue
        idx = np.flatnonzero((m.seg == seg).to_numpy())
        zi, yi = z[idx], y[idx]
        a, b = 0.0, 1.0
        for j in range(len(idx)):
            lo = max(0, j - win)
            if j >= 25 and j % 10 == 0:      # refit every 10 matches
                zz, yy = zi[lo:j], yi[lo:j]
                # ridge-ish logistic on (1, z) with a prior at (0, 1)
                aa, bb = a, b
                for _ in range(8):
                    pp = 1.0 / (1.0 + np.exp(-(aa + bb * zz)))
                    w = np.clip(pp * (1 - pp), 1e-6, None)
                    r = yy - pp
                    g = np.array([r.sum() - prior_n * aa,
                                  (r * zz).sum() - prior_n * (bb - 1.0)])
                    H = np.array([[w.sum() + prior_n, (w * zz).sum()],
                                  [(w * zz).sum(), (w * zz * zz).sum() + prior_n]])
                    try:
                        step = np.linalg.solve(H, g)
                    except np.linalg.LinAlgError:
                        break
                    aa, bb = aa + step[0], bb + step[1]
                    if np.max(np.abs(step)) < 1e-6:
                        break
                if np.isfinite(aa) and np.isfinite(bb):
                    a, b = float(np.clip(aa, -1.5, 1.5)), float(np.clip(bb, 0.05, 2.0))
            out[idx[j]] = a + b * zi[j]
    return out


def blended_p(m, zs, params, rich, rot=None):
    names = list(zs)
    out = np.empty(len(m))
    for seg in ("intl_m", "intl_f", "fr"):
        mask = (m.seg == seg).to_numpy()
        w, s1, s2, s3, tk, dr, rex, sr, rc, zc = params[seg]
        rv = np.clip(rich[mask], 0.0, 1.0)
        rt = np.clip((rot if rot is not None else np.zeros(len(m)))[mask], -3, 3)
        scale_pl = rv ** rex if rex else np.ones_like(rv)
        z = sum(wi * (zs[n][mask] * (scale_pl if n != "elo" else 1.0))
                for wi, n in zip(w, names))
        zm = zmap(z, s1, s2, s3, zc)
        kof = m.ko.to_numpy()[mask]
        drd = (m.dr1.to_numpy() - m.dr2.to_numpy())[mask]
        srd = (m.sr1.to_numpy() - m.sr2.to_numpy())[mask]
        out[mask] = (np.where(kof == 1, tk * zm, zm) - dr * drd + sr * srd
                     + rc * rt)
    return out           # NOTE: returns the blended LOGIT (z), not p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dev", action="store_true")
    args = ap.parse_args()
    m, d, x = load()
    print(f"matches {len(m)} ({m.date.min()}..{m.date.max()}), deliveries {len(d)}, "
          f"extra intl results {len(x)}")
    x["nreal"] = 0
    xc = x[["match_id", "comp", "gender", "date", "team1", "team2", "city",
            "venue", "winner", "result", "home1", "y1", "seg", "margin_runs",
            "extra", "nreal"]].copy()
    xc["home1_name"] = xc.home1
    xc["home1_learned"] = xc.home1
    xc["home1_country"] = venue_country_home(xc.assign(comp="t20s"))
    for c_ in ("ko", "dr1", "dr2", "sr1", "sr2"):
        xc[c_] = 0
    mx = pd.concat([m, xc], ignore_index=True)
    mx = mx.sort_values(["date", "match_id"]).reset_index(drop=True)
    elo_params = tune_elo(mx)
    p_elo_x, nmin_x = run_elo_seg(mx, elo_params)
    real = (mx.extra == 0).to_numpy()
    order = mx[real].match_id.to_numpy()
    pos = pd.Series(np.arange(real.sum()), index=order).reindex(m.match_id).to_numpy()
    p_elo, nmin = p_elo_x[real][pos], nmin_x[real][pos]
    bat_g, bowl_g = per_match_player_tables(d)
    (wv, sh, sig, diff, rich, rot), pll = tune_player(m, bat_g, bowl_g)
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
    params = fit_blend(m, zs, m.nreal.to_numpy(), rich, rot=rot)
    z_blend = blended_p(m, zs, params, rich, rot)
    p_blend_raw = inv(z_blend)
    # online recalibration hyper-params chosen on train only
    tr0 = ((m.date >= EVAL_LO) & (m.date < TRAIN_END)).to_numpy() & (m.nreal.to_numpy() >= MIN_PRIOR)
    z_on = np.array(z_blend, copy=True)
    for seg in ("intl_m", "intl_f", "fr"):
        segmask = (m.seg == seg).to_numpy()
        trs = tr0 & segmask
        best_on, best_on_ll, best_col = None, np.inf, None
        for win in ONLINE_N:
            for pn in ONLINE_PRIOR:
                zr = online_recalibrate(m, z_blend, params, win, pn, only_seg=seg)
                cur = ll(inv(zr[trs]), m.y1.to_numpy()[trs]).mean()
                if cur < best_on_ll:
                    best_on_ll, best_on, best_col = cur, (win, pn), zr[segmask]
        z_on[segmask] = best_col
        raw_ll = ll(p_blend_raw[trs], m.y1.to_numpy()[trs]).mean()
        print(f"  online recal[{seg}]: win={best_on[0]} prior={best_on[1]} "
              f"(train LL {best_on_ll:.5f} vs raw {raw_ll:.5f})")
    p_blend = inv(z_on)

    tr = ((m.date >= EVAL_LO) & (m.date < TRAIN_END)).to_numpy() & (m.nreal.to_numpy() >= MIN_PRIOR)
    y = m.y1.to_numpy()
    print("\ntrain-era walk-forward LL (2018..2024-05, >=10 prior real matches):")
    for name, p in (("elo", p_elo), ("player2", p_pl), ("player1", p_v1),
                    ("blend_raw", p_blend_raw), ("blend", p_blend)):
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
    dev_stability(b)
    b.to_parquet(os.path.join(ROOT, "data", "pm_preds_v2.parquet"), index=False)


def dev_stability(b):
    """Honesty check: dev has been iterated on, so report it split by time.
    A model that only wins on one half is fitting the half it was tuned to
    look at. Neither half is a claim - the claim arm is pm-prospective-1."""
    b = b.copy()
    mid = b.date.sort_values().iloc[len(b) // 2]
    b["half"] = np.where(b.date <= mid, f"H1 (<= {mid})", f"H2 (> {mid})")
    print("\ndev stability by time half (post-hoc; neither half is a claim):")
    for half, g in b.groupby("half"):
        for seg, gg in list(g.groupby("seg")) + [("pooled", g)]:
            d, t = clustered_t((gg.ll_model - gg.ll_open).values, gg.date)
            print(f"  {half:22s} {seg:14s} n={len(gg):4d} model-open={d:+.5f} (t={t:.1f})")


if __name__ == "__main__":
    main()


