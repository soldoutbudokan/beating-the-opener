"""MLB pitcher talent engine (registration K, PROGRESS.md Market 6).

The WNBA T1 / NHL N template applied to strikeout props, where the
generative story is K = batters faced x K-rate:

  pitcher state  kr = K per batter faced   (obs k/bf,  R = rvar/bf)
  batter  state  br = K per plate app.     (obs so/pa, R = rvar/pa)
  workload       bf_hat = pre-start batters-faced estimate, one of three
                 candidate paths selected ONCE by tuning-era MSE
  opponent       strictly-prior EXPECTED lineup (the opponent's starting
                 nine from its most recent game vs a same-handed starter
                 within 10 days, else its last game) -> mean br of the nine
  combination    log5 of kr and the lineup rate against the walk-forward
                 league K/PA rate, one scalar gamma on the lineup term
  park           per-venue K factor, shrunk, prior seasons only
  outs           bf_hat x outs-per-BF state (obs outs/bf)

Scalar Kalman per (player, stat): predict x += trajectory drift (delta-method
career curves), P += Q (x10 across a season boundary); update with
K = P/(P+R). Prediction is written BEFORE the update, so every number is
strictly pre-game by construction. Curves, rvar, q/p0 grids, gamma, park
shrinkage, sigma_bf and the workload-path choice are all fit on starts
before TUNE_CUTOFF (2021-01-01); K-G1 is scored walk-forward on 2021-2024.
No market-derived quantity enters anywhere.

Usage: python3 src/talent_mlb.py           # K-G1 market-free gate
       python3 src/talent_mlb.py --build   # write data/talent_mlb.pkl
"""
import argparse
import glob
import os
from collections import defaultdict

import numpy as np
import pandas as pd

ROOT = os.path.join(os.path.dirname(__file__), "..")
DATA = os.path.join(ROOT, "data", "mlb")

TUNE_CUTOFF = "2021-01-01"        # everything fit/tuned strictly before
TUNE_LO = "2017-01-01"            # tuning metric window (2 seasons burn-in)
VAL_LO, VAL_HI = "2021-03-01", "2025-01-01"   # K-G1 window (registered)
BF_FLOOR = 10                     # K-G1 rows: starts with bf >= 10
ALPHA_F, ALPHA_S, W_FAST = 0.25, 0.08, 0.6     # incumbent EW blend (features.py)
GRID_Q = (1e-4, 3e-4, 1e-3, 3e-3)
GRID_P0 = (0.05, 0.15, 0.5)
OFFSEASON_Q_MULT = 10.0
P_BUCKET, P_MAXB = 30, 15         # pitcher career curves (appearances)
B_BUCKET, B_MAXB = 100, 12        # batter career curves (games)
LINEUP_DAYS = 10
PARK_N0 = 5000.0                  # PA shrinkage for park factors
GRID_GAMMA = np.arange(0.0, 1.51, 0.25)
# level filters (bf / pitches per start) track a manager-controlled quantity
# and need far more process noise than the rate states: wider grid, fixed
# before the first full run (tuning-era choice, never touches dev)
LEVEL_GRID_Q = (1e-3, 3e-3, 1e-2, 3e-2, 1e-1, 3e-1, 1.0)
LEVEL_GRID_P0 = (0.15, 0.5, 1.0)


# ------------------------------------------------------------------ loading
def _load(prefix):
    fs = sorted(glob.glob(os.path.join(DATA, f"{prefix}_*.parquet")))
    return pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True)


def load_all():
    sched = _load("schedule")
    sched = sched[sched.gameType.isin(["R", "F", "D", "L", "W"])].copy()
    sched["ts"] = pd.to_datetime(sched.gameDate, utc=True)
    sched["season"] = pd.to_datetime(sched.officialDate).dt.year
    sched = sched.drop_duplicates("gamePk")[
        ["gamePk", "ts", "season", "gameType", "venue_id", "home", "away"]].rename(
        columns={"home": "home_abbr", "away": "away_abbr"})   # box rows carry home=bool
    pit = _load("pitcher_box").merge(sched, on="gamePk", how="inner")
    bat = _load("batter_box").merge(sched, on="gamePk", how="inner")
    for c in ["outs", "k", "h_allowed", "bb_allowed", "er", "bf", "pitches"]:
        pit[c] = pd.to_numeric(pit[c], errors="coerce")
    for c in ["pa", "so", "order"]:
        bat[c] = pd.to_numeric(bat[c], errors="coerce")
    pit = pit[pit.bf > 0].sort_values(["pid", "ts", "gamePk"]).reset_index(drop=True)
    pit["home"] = pit.home.astype(int)
    bat["home"] = bat.home.astype(int)
    bat = bat[bat.pa > 0].sort_values(["pid", "ts", "gamePk"]).reset_index(drop=True)
    pit["date"] = pd.to_datetime(pit.date)
    bat["date"] = pd.to_datetime(bat.date)
    ppl_path = os.path.join(DATA, "people.parquet")
    people = pd.read_parquet(ppl_path) if os.path.exists(ppl_path) else None
    return sched, pit, bat, people


def add_roles(pit):
    """Group for the pitcher curves: prior-season majority role, rookies SP."""
    ss = (pit.groupby(["pid", "season"]).started.mean().rename("sp_share")
          .reset_index())
    ss["season"] = ss.season + 1           # applies to the NEXT season
    pit = pit.merge(ss, on=["pid", "season"], how="left")
    pit["grp"] = np.where(pit.sp_share.fillna(1.0) >= 0.5, "SP", "RP")
    pit["gp"] = pit.groupby("pid").cumcount()          # career appearances
    pit["y_kr"] = pit.k / pit.bf
    pit["den_kr"] = pit.bf
    pit["y_opb"] = pit.outs / pit.bf
    pit["den_opb"] = pit.bf
    return pit


def add_batter_cols(bat):
    bat["grp"] = "B"
    bat["gp"] = bat.groupby("pid").cumcount()
    bat["y_br"] = bat.so / bat.pa
    bat["den_br"] = bat.pa
    return bat


# ------------------------------------------------------------------ engine
def fit_curves(d, cutoff, st, bucket, maxb, groups):
    f = d[d.date < cutoff].copy()
    f["bucket"] = np.minimum(f.gp // bucket, maxb)
    g = (f.assign(w=f[f"den_{st}"], wy=f[f"den_{st}"] * f[f"y_{st}"])
         .groupby(["pid", "grp", "bucket"])[["w", "wy"]].sum())
    g = (g.wy / g.w).rename("rate").reset_index().sort_values(["pid", "bucket"])
    g["d_rate"] = g.groupby("pid").rate.diff()
    g["from_b"] = g.groupby("pid").bucket.shift(1)
    steps = g[(g.bucket - g.from_b) == 1]
    curves = {}
    for grp in groups:
        base = g[(g.grp == grp) & (g.bucket == 0)].rate.mean()
        inc = (steps[steps.grp == grp].groupby("from_b").d_rate.mean()
               .reindex(range(maxb), fill_value=0.0)
               .rolling(3, center=True, min_periods=1).mean())
        curves[grp] = (float(base), inc.cumsum().to_numpy(float))
    return curves


def curve_level(curves, grp, gp, bucket, maxb):
    base, inc = curves[grp]
    b = min(int(gp // bucket), maxb)
    return base + (inc[b - 1] if b > 0 else 0.0)


def fit_rvar(d, cutoff, st, min_gp=10):
    f = d[(d.date < cutoff) & (d.gp >= min_gp)]
    den = f[f"den_{st}"]
    ok = f[f"y_{st}"].notna() & (den > 0)
    fo, do = f[ok], den[ok]
    pm = fo.groupby("pid")[f"y_{st}"].transform("mean")
    return float((do * (fo[f"y_{st}"] - pm) ** 2).sum() / do.sum() * do.mean())


def run_filter(d, curves, rvar, q, p0, st, bucket, maxb):
    """Returns (pre-game prediction, post-update state) aligned to d."""
    xs, ps, last_season = {}, {}, {}
    pred = np.empty(len(d))
    post = np.empty(len(d))
    pid = d.pid.to_numpy()
    grp = d.grp.to_numpy()
    gp = d.gp.to_numpy()
    sea = d.season.to_numpy()
    den = d[f"den_{st}"].to_numpy(float)
    y = d[f"y_{st}"].to_numpy(float)
    R0, Q, P0 = rvar, q * rvar, p0 * rvar
    for i in range(len(d)):
        a = pid[i]
        if a not in xs:
            xs[a] = curve_level(curves, grp[i], 0, bucket, maxb)
            ps[a] = P0
            last_season[a] = sea[i]
        else:
            drift = (curve_level(curves, grp[i], gp[i], bucket, maxb)
                     - curve_level(curves, grp[i], max(gp[i] - 1, 0), bucket, maxb))
            xs[a] += drift
            ps[a] += Q * (OFFSEASON_Q_MULT if sea[i] != last_season[a] else 1.0)
            last_season[a] = sea[i]
        pred[i] = xs[a]
        if np.isfinite(y[i]) and den[i] > 0:
            R = R0 / den[i]
            k = ps[a] / (ps[a] + R)
            xs[a] += k * (y[i] - xs[a])
            ps[a] *= (1 - k)
        post[i] = xs[a]
    return pred, post


def rate_mse(d, pred, st, lo, hi, extra=None):
    m = ((d.date >= lo) & (d.date < hi) & d[f"y_{st}"].notna()
         & (d[f"den_{st}"] > 0))
    if extra is not None:
        m &= extra
    w = d[f"den_{st}"][m].to_numpy(float)
    err = (d[f"y_{st}"][m].to_numpy(float) - pred[m.to_numpy()]) ** 2
    return float((w * err).sum() / w.sum())


def tune(d, st, bucket, maxb, groups, extra=None, label=""):
    curves = fit_curves(d, TUNE_CUTOFF, st, bucket, maxb, groups)
    rvar = fit_rvar(d, TUNE_CUTOFF, st)
    pre = d[d.date < TUNE_CUTOFF].reset_index(drop=True)
    ex = extra[d.date < TUNE_CUTOFF].reset_index(drop=True) if extra is not None else None
    best, best_mse = None, np.inf
    for q in GRID_Q:
        for p0 in GRID_P0:
            pred, _ = run_filter(pre, curves, rvar, q, p0, st, bucket, maxb)
            mse = rate_mse(pre, pred, st, TUNE_LO, TUNE_CUTOFF, ex)
            if mse < best_mse:
                best, best_mse = (q, p0), mse
    print(f"{label or st}: tuned < {TUNE_CUTOFF} q={best[0]} p0={best[1]} "
          f"(rate mse {best_mse:.6f}, rvar {rvar:.5f})")
    return curves, rvar, best


# ------------------------------------------------------------ level filters
def level_filter(x, den, season, pid, q, p0, rvar):
    """Kalman on a per-start LEVEL (bf or pitches): obs y=x, R=rvar."""
    xs, ps, last = {}, {}, {}
    pred = np.empty(len(x))
    Q, P0 = q * rvar, p0 * rvar
    for i in range(len(x)):
        a = pid[i]
        if a not in xs:
            xs[a], ps[a], last[a] = np.nan, P0, season[i]
        else:
            ps[a] += Q * (OFFSEASON_Q_MULT if season[i] != last[a] else 1.0)
            last[a] = season[i]
        pred[i] = xs[a]
        if np.isfinite(x[i]):
            if not np.isfinite(xs[a]):
                xs[a] = x[i]
            else:
                k = ps[a] / (ps[a] + rvar)
                xs[a] += k * (x[i] - xs[a])
                ps[a] *= (1 - k)
    return pred


def shift_ew(s, key, alpha):
    return s.groupby(key, sort=False).transform(
        lambda v: v.shift(1).ewm(alpha=alpha, min_periods=1).mean())


def workload_paths(starts):
    """Three registered candidates for bf_hat on STARTS (sorted by pid, ts):
    (i) EW blend, (ii) BF level Kalman, (iii) pitches Kalman / pitches-per-BF
    Kalman. Level-filter q/p0 tuned pre-cutoff on next-start BF MSE."""
    s = starts
    key = s.pid
    out = {}
    out["ew"] = (W_FAST * shift_ew(s.bf, key, ALPHA_F)
                 + (1 - W_FAST) * shift_ew(s.bf, key, ALPHA_S)).to_numpy(float)
    pre = (s.date < TUNE_CUTOFF).to_numpy()
    win = pre & (s.date >= TUNE_LO).to_numpy() & (s.bf >= BF_FLOOR).to_numpy()
    bf = s.bf.to_numpy(float)
    sea, pid = s.season.to_numpy(), s.pid.to_numpy()

    def tune_level(x):
        rv = float(np.nanvar(x[pre]))
        best, bm = None, np.inf
        for q in LEVEL_GRID_Q:
            for p0 in LEVEL_GRID_P0:
                pr = level_filter(x, None, sea, pid, q, p0, rv)
                m = win & np.isfinite(pr)
                mse = float(np.mean((x[m] - pr[m]) ** 2))
                if mse < bm:
                    best, bm = (q, p0, rv), mse
        return best

    qb = tune_level(bf)
    out["kal_bf"] = level_filter(bf, None, sea, pid, qb[0], qb[1], qb[2])
    pitches = s.pitches.to_numpy(float)
    ppb = np.where(bf > 0, pitches / bf, np.nan)
    qp = tune_level(pitches)
    qr = tune_level(ppb)
    pit_hat = level_filter(pitches, None, sea, pid, qp[0], qp[1], qp[2])
    ppb_hat = level_filter(ppb, None, sea, pid, qr[0], qr[1], qr[2])
    out["kal_pitch"] = pit_hat / ppb_hat
    print(f"workload level filters: bf q={qb[0]} p0={qb[1]}; pitches q={qp[0]} "
          f"p0={qp[1]}; pitches/bf q={qr[0]} p0={qr[1]}")
    scores = {}
    for name, pr in out.items():
        m = win & np.isfinite(pr)
        scores[name] = float(np.mean((bf[m] - pr[m]) ** 2))
    chosen = min(scores, key=scores.get)
    print("workload path tuning-era next-start BF MSE: "
          + "  ".join(f"{k}={v:.3f}" for k, v in scores.items())
          + f"  -> {chosen}")
    return out, chosen


# ---------------------------------------------------------------- league
def league_rate(bat):
    """Walk-forward league K/PA: trailing 365-day totals as of the day
    BEFORE each date (strictly prior)."""
    daily = bat.groupby("date")[["so", "pa"]].sum().sort_index()
    idx = pd.date_range(daily.index.min(), daily.index.max() + pd.Timedelta(days=1))
    daily = daily.reindex(idx, fill_value=0)
    roll = daily.rolling("365D").sum().shift(1)
    lr = (roll.so / roll.pa)
    return lr.ffill().fillna(lr.mean())


# ------------------------------------------------------------ lineups
def build_lineup_index(bat, pit, people):
    """Per team: chronological games with the starting nine and the opposing
    starter's hand. Strictly game-level facts, used only for games that
    precede the start being priced."""
    nine = bat[(bat.order % 100 == 0) & bat.order.between(100, 900)]
    nine = nine.groupby(["team", "gamePk"]).pid.apply(list).rename("nine").reset_index()
    st = pit[pit.started.fillna(False)][["gamePk", "team", "pid", "ts"]]
    hand = {}
    if people is not None:
        hand = dict(zip(people.pid, people.pitch_hand))
    st = st.assign(hand=st.pid.map(hand))
    # opposing starter for (team, game) = the starter whose team != team
    opp = st.rename(columns={"team": "opp_team", "pid": "opp_sp", "hand": "opp_hand"})
    g = nine.merge(opp, on="gamePk")
    g = g[g.team != g.opp_team]
    g = g.sort_values(["team", "ts"])
    idx = {}
    for team, grp in g.groupby("team"):
        idx[team] = (grp.ts.to_numpy(), grp.opp_hand.to_numpy(object),
                     grp.nine.to_numpy(object))
    return idx, hand


def expected_lineup(idx, team, ts, sp_hand):
    """Opponent's starting nine from its most recent game vs a same-handed
    starter within LINEUP_DAYS, else its last game. None if no history."""
    if team not in idx:
        return None
    tss, hands, nines = idx[team]
    j = np.searchsorted(tss, ts, side="left") - 1     # last game strictly before
    if j < 0:
        return None
    lim = ts - np.timedelta64(LINEUP_DAYS, "D")
    if sp_hand in ("L", "R"):
        k = j
        while k >= 0 and tss[k] >= lim:
            if hands[k] == sp_hand:
                return nines[k]
            k -= 1
    return nines[j]


class BatterStates:
    """Post-update br state per batter as of any timestamp (searchsorted)."""
    def __init__(self, bat, post, prior):
        self.prior = prior
        self.t = defaultdict(list)
        self.x = defaultdict(list)
        for p, ts, v in zip(bat.pid.to_numpy(), bat.ts.to_numpy(), post):
            self.t[p].append(ts)
            self.x[p].append(v)
        self.t = {p: np.asarray(v) for p, v in self.t.items()}
        self.x = {p: np.asarray(v) for p, v in self.x.items()}

    def at(self, pid, ts):
        if pid not in self.t:
            return self.prior
        j = np.searchsorted(self.t[pid], ts, side="left") - 1
        return self.x[pid][j] if j >= 0 else self.prior


def logit(p):
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


def log5(pp, pb, pl):
    num = pp * pb / pl
    den = num + (1 - pp) * (1 - pb) / (1 - pl)
    return num / den


def park_factors(bat, sched, season):
    """K/PA ratio per venue from seasons strictly before `season`, shrunk
    toward 1 with PARK_N0 plate appearances."""
    prior = bat[bat.season < season]
    if not len(prior):
        return {}
    lg = prior.so.sum() / prior.pa.sum()
    v = prior.groupby("venue_id")[["so", "pa"]].sum()
    ratio = (v.so / v.pa) / lg
    shrink = v.pa / (v.pa + PARK_N0)
    return (1 + (ratio - 1) * shrink).to_dict()


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    args = ap.parse_args()
    sched, pit, bat, people = load_all()
    pit = add_roles(pit)
    bat = add_batter_cols(bat)
    print(f"pitcher rows {len(pit)} ({pit.pid.nunique()} pitchers), batter rows "
          f"{len(bat)} ({bat.pid.nunique()} batters), seasons "
          f"{pit.season.min()}-{pit.season.max()}, people="
          f"{'yes' if people is not None else 'NO (hand-matching disabled)'}")

    # 1. pitcher kr and outs-per-BF states; batter br states
    cv_kr, rv_kr, (q_kr, p_kr) = tune(pit, "kr", P_BUCKET, P_MAXB, ["SP", "RP"], label="kr (K/BF)")
    kr_pred, _ = run_filter(pit, cv_kr, rv_kr, q_kr, p_kr, "kr", P_BUCKET, P_MAXB)
    cv_ob, rv_ob, (q_ob, p_ob) = tune(pit, "opb", P_BUCKET, P_MAXB, ["SP", "RP"], label="opb (outs/BF)")
    opb_pred, _ = run_filter(pit, cv_ob, rv_ob, q_ob, p_ob, "opb", P_BUCKET, P_MAXB)
    cv_br, rv_br, (q_br, p_br) = tune(bat, "br", B_BUCKET, B_MAXB, ["B"], label="br (K/PA)")
    _, br_post = run_filter(bat, cv_br, rv_br, q_br, p_br, "br", B_BUCKET, B_MAXB)
    pit["kr_hat"] = kr_pred
    pit["opb_hat"] = opb_pred

    # 2. starts only from here: workload paths
    starts = pit[pit.started.fillna(False)].copy().sort_values(["pid", "ts", "gamePk"]).reset_index(drop=True)
    paths, chosen = workload_paths(starts)
    for k, v in paths.items():
        starts[f"bf_{k}"] = v
    starts["bf_hat"] = starts[f"bf_{chosen}"]

    # 3. league rate, lineups, park, gamma
    lr = league_rate(bat)
    starts["lg"] = lr.reindex(starts.date).to_numpy()
    idx, hand = build_lineup_index(bat, pit, people)
    bstates = BatterStates(bat, br_post, prior=cv_br["B"][0])
    lineup_br = np.full(len(starts), np.nan)
    n_hand = 0
    for i, (opp, ts, pid) in enumerate(zip(starts.opp.to_numpy(), starts.ts.to_numpy(), starts.pid.to_numpy())):
        nine = expected_lineup(idx, opp, ts, hand.get(pid))
        if nine is None:
            continue
        lineup_br[i] = np.mean([bstates.at(b, ts) for b in nine])
    starts["lineup_br"] = lineup_br
    print(f"expected-lineup coverage: {np.isfinite(lineup_br).mean():.1%}")
    pf = {}
    for sea in sorted(starts.season.unique()):
        pf[sea] = park_factors(bat, sched, sea)
    starts["park"] = [pf[s].get(v, 1.0) for s, v in zip(starts.season, starts.venue_id)]

    # gamma on the lineup term, fit on tuning-era starts (per-start K MSE)
    pre = (starts.date >= TUNE_LO) & (starts.date < TUNE_CUTOFF) & (starts.bf >= BF_FLOOR) \
        & starts.lineup_br.notna() & starts.bf_hat.notna()
    best_g, best_m = 1.0, np.inf
    for g in GRID_GAMMA:
        pk = p_strike(starts, g)
        mu = pk * starts.bf_hat
        m = float(((starts.k - mu)[pre] ** 2).mean())
        if m < best_m:
            best_g, best_m = float(g), m
    print(f"gamma (lineup term) tuned < {TUNE_CUTOFF}: {best_g} (K mse {best_m:.4f})")
    starts["p_k"] = p_strike(starts, best_g)
    starts["mu_k"] = starts.p_k * starts.bf_hat
    starts["mu_outs"] = starts.opb_hat * starts.bf_hat

    # 4. K-G1: walk-forward next-start MSE vs the incumbent EW blend
    key = starts.pid
    for c in ("k", "outs"):
        starts[f"{c}_blend"] = (W_FAST * shift_ew(starts[c], key, ALPHA_F)
                                + (1 - W_FAST) * shift_ew(starts[c], key, ALPHA_S))
    val = (starts.date >= VAL_LO) & (starts.date < VAL_HI) & (starts.bf >= BF_FLOOR)
    verdict = {}
    print(f"\nK-G1 walk-forward validation, {VAL_LO}..{VAL_HI}, starts with bf>={BF_FLOOR}, per-start MSE:")
    for stat, mu_col in (("k", "mu_k"), ("outs", "mu_outs")):
        m = val & starts[mu_col].notna() & starts[f"{stat}_blend"].notna()
        e = float(((starts[stat] - starts[mu_col])[m] ** 2).mean())
        b = float(((starts[stat] - starts[f"{stat}_blend"])[m] ** 2).mean())
        verdict[stat] = e < b
        print(f"  {stat:5s}: engine={e:.4f}  ew_blend={b:.4f}  "
              f"{'ENGINE' if verdict[stat] else 'ew'}  (n={int(m.sum())})")
        # diagnostics: workload alternatives on the same rows
        if stat == "k":
            for name in paths:
                mu_alt = starts.p_k * starts[f"bf_{name}"]
                ma = m & mu_alt.notna()
                print(f"      [diag] bf path {name}: K mse {float(((starts.k - mu_alt)[ma] ** 2).mean()):.4f}")
            mu_nol = log5(starts.kr_hat, starts.lg, starts.lg) * starts.park * starts.bf_hat
            print(f"      [diag] no lineup term: K mse {float(((starts.k - mu_nol)[m] ** 2).mean()):.4f}")
    cell = [{"k": "strikeouts", "outs": "outs_recorded"}[s] for s in ("k", "outs") if verdict[s]]
    print(f"K-G1: k={'PASS' if verdict['k'] else 'FAIL'} outs={'PASS' if verdict['outs'] else 'FAIL'}"
          f" -> registered cell: {cell if cell else 'EMPTY (stop; nothing touches dev/holdout)'}")

    if args.build:
        cols = ["pid", "gamePk", "date", "season", "team", "opp", "home", "venue_id",
                "bf", "k", "outs", "kr_hat", "opb_hat",
                "bf_hat", "lineup_br", "lg", "park", "p_k", "mu_k", "mu_outs",
                "k_blend", "outs_blend"] + [f"bf_{k}" for k in paths]
        out = starts[cols].copy()
        out.attrs["params"] = {"kr": (q_kr, p_kr), "opb": (q_ob, p_ob), "br": (q_br, p_br),
                               "gamma": best_g, "bf_path": chosen, "cell": cell}
        out.to_pickle(os.path.join(ROOT, "data", "talent_mlb.pkl"))
        pd.Series(out.attrs["params"]).to_json(os.path.join(ROOT, "data", "talent_mlb_params.json"))
        print(f"talent_mlb.pkl written: {len(out)} starts, params fit/tuned < {TUNE_CUTOFF}, cell={cell}")


def p_strike(starts, gamma):
    """log5(kr, lineup') vs league, lineup' = league-logit + gamma * (lineup
    logit - league logit); park multiplies the resulting rate."""
    lg = starts.lg.to_numpy(float)
    lb = starts.lineup_br.to_numpy(float)
    lb = np.where(np.isfinite(lb), lb, lg)
    lz = logit(lg) + gamma * (logit(lb) - logit(lg))
    pb = 1 / (1 + np.exp(-lz))
    p = log5(np.clip(starts.kr_hat.to_numpy(float), 0.02, 0.6), pb, lg)
    return np.clip(p * starts.park.to_numpy(float), 0.02, 0.7)


if __name__ == "__main__":
    main()
