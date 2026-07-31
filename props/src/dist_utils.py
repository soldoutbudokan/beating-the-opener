"""Per-(sport, market) stat distributions: (line, p_over) <-> implied mean.

Families (FAMILY): Normal with sigma(mu) for volume stats, Poisson for low
counts, NegBin for overdispersed MLB batter counts (total_bases, hrr) with a
per-market dispersion r - Poisson fallback when fitted r > 200 (~no
overdispersion). Over a half-line L means actual >= ceil(L): threshold L+0.5
under the Normal (continuity), ceil(L) under the discrete families.

SIGMA_AB / NB_R are FITTED by build_modelset on panel rows STRICTLY BEFORE a
cutoff date (default: the earliest odds date, so the fit is out-of-sample to
every odds row - the wnba version fit on the full panel including eval, a
documented sin) and persisted to data/dist_params_<sport>.json; train_eval
reloads them via load_params(). Nothing here is hardcoded from data we
haven't seen.
"""
import json
import os

import numpy as np
import pandas as pd
from scipy import stats as sps

from grade_props import STAT_COLS  # market -> (role, ...): single source

ROOT = os.path.join(os.path.dirname(__file__), "..")

FAMILY = {
    "MLB": {
        "strikeouts": "pois", "hits_allowed": "pois", "walks_allowed": "pois",
        "earned_runs": "pois", "hits": "pois",
        "outs_recorded": "norm",
        "total_bases": "nb", "hrr": "nb",
    },
    "NBA": {  # identical family split to wnba
        "points": "norm", "rebounds": "norm", "assists": "norm",
        "pra": "norm", "pts_ast": "norm", "pts_reb": "norm", "reb_ast": "norm",
        "threes": "pois", "steals": "pois", "blocks": "pois",
    },
    "NHL": {  # low counts -> NegBin with fitted r (Poisson fallback);
        # saves ~25/game -> Normal
        "goals": "nb", "assists": "nb", "points": "nb", "shots": "nb",
        "blocked_shots": "nb", "saves": "norm",
    },
}

# market -> panel EW cols summing to a fundamentals projection (gap_ew uses
# this too) and the matching panel actual cols (short names from features.py)
EW_PROJ = {
    "MLB": {
        "strikeouts": ["k_ewf"], "outs_recorded": ["outs_ewf"],
        "hits_allowed": ["ha_ewf"], "walks_allowed": ["bb_ewf"],
        "earned_runs": ["er_ewf"], "hits": ["h_ewf"],
        "total_bases": ["tb_ewf"], "hrr": ["h_ewf", "r_ewf", "rbi_ewf"],
    },
    "NBA": {
        "points": ["pts_ewf"], "rebounds": ["reb_ewf"], "assists": ["ast_ewf"],
        "threes": ["tpm_ewf"], "steals": ["stl_ewf"], "blocks": ["blk_ewf"],
        "pra": ["pts_ewf", "reb_ewf", "ast_ewf"],
        "pts_ast": ["pts_ewf", "ast_ewf"], "pts_reb": ["pts_ewf", "reb_ewf"],
        "reb_ast": ["reb_ewf", "ast_ewf"],
    },
    "NHL": {
        "goals": ["g_ewf"], "assists": ["a_ewf"], "points": ["p_ewf"],
        "shots": ["sog_ewf"], "blocked_shots": ["blk_ewf"],
        "saves": ["sv_ewf"],
    },
}
ACTUALS = {
    "MLB": {
        "strikeouts": ["k"], "outs_recorded": ["outs"], "hits_allowed": ["ha"],
        "walks_allowed": ["bb"], "earned_runs": ["er"], "hits": ["h"],
        "total_bases": ["tb"], "hrr": ["h", "r", "rbi"],
    },
    "NBA": {
        "points": ["pts"], "rebounds": ["reb"], "assists": ["ast"],
        "threes": ["tpm"], "steals": ["stl"], "blocks": ["blk"],
        "pra": ["pts", "reb", "ast"], "pts_ast": ["pts", "ast"],
        "pts_reb": ["pts", "reb"], "reb_ast": ["reb", "ast"],
    },
    "NHL": {
        "goals": ["g"], "assists": ["a"], "points": ["p"], "shots": ["sog"],
        "blocked_shots": ["blk"], "saves": ["sv"],
    },
}

# filled at runtime by build_modelset (fit_*) or load_params(); keyed
# (sport, market). NB_R value None = fitted, no overdispersion -> Poisson.
SIGMA_AB = {}
NB_R = {}


def family(sport, market):
    if sport not in FAMILY:
        raise NotImplementedError(
            f"{sport}: no distribution families ported yet - add {sport} to "
            f"FAMILY/EW_PROJ/ACTUALS in dist_utils.py (MLB/NBA only for now)")
    return FAMILY[sport][market]


def sigma(sport, market, mu):
    try:
        a, b = SIGMA_AB[(sport, market)]
    except KeyError:
        raise KeyError(f"sigma not fitted for {sport}/{market} - run "
                       f"build_modelset (or load_params) first")
    return np.sqrt(np.maximum(a + b * np.maximum(mu, 0.0), 0.25))


def _nb_r(sport, market):
    try:
        return NB_R[(sport, market)]
    except KeyError:
        raise KeyError(f"dispersion not fitted for {sport}/{market} - run "
                       f"build_modelset (or load_params) first")


def p_over(sport, market, mu, line):
    """P(actual > line) given mean mu (half-lines assumed)."""
    fam = family(sport, market)
    mu = np.asarray(mu, float)
    line = np.asarray(line, float)
    if fam == "norm":
        return 1.0 - sps.norm.cdf(line + 0.5, mu, sigma(sport, market, mu))
    k = np.ceil(line).astype(int)  # need >= k successes
    m = np.maximum(mu, 1e-6)
    r = _nb_r(sport, market) if fam == "nb" else None
    if r is None:
        return 1.0 - sps.poisson.cdf(k - 1, m)
    return 1.0 - sps.nbinom.cdf(k - 1, r, r / (r + m))


def implied_mu(sport, market, line, p):
    """Invert p_over: the mean the market believes, given its line and price."""
    fam = family(sport, market)
    line = np.asarray(line, float)
    p = np.clip(np.asarray(p, float), 0.02, 0.98)
    if fam == "norm":
        mu = line + 0.5  # sigma depends on mu -> fixed-point, converges fast
        for _ in range(4):
            mu = line + 0.5 + sigma(sport, market, mu) * sps.norm.ppf(p)
        return mu
    r = _nb_r(sport, market) if fam == "nb" else None
    lo = np.full(line.shape, 0.01)
    hi = np.full(line.shape, 40.0)
    k = np.ceil(line).astype(int)
    for _ in range(60):
        mid = (lo + hi) / 2
        if r is None:
            pm = 1.0 - sps.poisson.cdf(k - 1, mid)
        else:
            pm = 1.0 - sps.nbinom.cdf(k - 1, r, r / (r + mid))
        lo = np.where(pm < p, mid, lo)
        hi = np.where(pm < p, hi, mid)
    return (lo + hi) / 2


def scale(sport, market, mu):
    """Distribution SD at mean mu - standardizes the move target/residual."""
    fam = family(sport, market)
    mu = np.asarray(mu, float)
    if fam == "norm":
        return sigma(sport, market, mu)
    m = np.maximum(mu, 0.3)  # floor copied from wnba train_eval.sd()
    r = _nb_r(sport, market) if fam == "nb" else None
    if r is None:
        return np.sqrt(m)
    return np.sqrt(m + m * m / r)


def _fit_rows(panel, sport, market, cutoff):
    """(EW-mean, actual) pairs from rows STRICTLY before cutoff, gp>=8."""
    role = STAT_COLS[sport][market][0]
    ew, ac = EW_PROJ[sport][market], ACTUALS[sport][market]
    d = panel[(panel.role == role)
              & (panel.date < pd.Timestamp(cutoff))].dropna(subset=ew + ac)
    d = d[d.gp >= 8]  # wnba fit_sigma verbatim
    mu = sum(d[c] for c in ew)
    act = sum(d[c] for c in ac)
    return mu.to_numpy(float), act.to_numpy(float)


def fit_sigma(panel, sport, cutoff):
    """sigma^2 = a + b*mu around the fast-EW mean, Normal markets only.

    Fit uses ONLY rows strictly before cutoff (out-of-sample to all odds)."""
    out = {}
    for mkt in FAMILY[sport]:
        if FAMILY[sport][mkt] != "norm":
            continue
        mu, act = _fit_rows(panel, sport, mkt, cutoff)
        if len(mu) < 100:
            raise RuntimeError(f"{sport}/{mkt}: only {len(mu)} pre-cutoff "
                               f"panel rows to fit sigma (archive mid-backfill?)")
        b, a = np.polyfit(mu, (act - mu) ** 2, 1)
        out[mkt] = (round(max(a, 0.1), 2), round(max(b, 0.2), 2))
        SIGMA_AB[(sport, mkt)] = out[mkt]
    return out


def fit_dispersion(panel, sport, cutoff):
    """NegBin r by method of moments on residuals around the fast-EW mean
    (var = mu + mu^2/r), rows strictly before cutoff. r > 200 or negative
    excess variance -> None (Poisson fallback)."""
    out = {}
    for mkt in FAMILY[sport]:
        if FAMILY[sport][mkt] != "nb":
            continue
        mu, act = _fit_rows(panel, sport, mkt, cutoff)
        if len(mu) < 100:
            raise RuntimeError(f"{sport}/{mkt}: only {len(mu)} pre-cutoff "
                               f"panel rows to fit dispersion")
        excess = np.sum((act - mu) ** 2 - mu)
        r = float(np.sum(mu ** 2) / excess) if excess > 0 else np.inf
        out[mkt] = None if (r <= 0 or r > 200) else round(r, 1)
        NB_R[(sport, mkt)] = out[mkt]
    return out


def dispersion_audit(panel, sport, cutoff):
    """var/mean by EW-mean quartile per discrete-family market (>1 means
    overdispersed vs Poisson; sanity check on the family assignments)."""
    print("dispersion audit (var/mean by EW-mean quartile, pre-cutoff rows):")
    for mkt in FAMILY[sport]:
        if FAMILY[sport][mkt] == "norm":
            continue
        mu, act = _fit_rows(panel, sport, mkt, cutoff)
        if len(mu) < 100:
            print(f"  {mkt:<14} only {len(mu)} rows - skipped")
            continue
        df = pd.DataFrame({"mu": mu, "r2": (act - mu) ** 2})
        df["b"] = pd.qcut(df.mu, 4, duplicates="drop")
        g = df.groupby("b", observed=True)
        ratios = "  ".join(f"{v:.2f}" for v in (g.r2.mean() / g.mu.mean()))
        print(f"  {mkt:<14} n={len(df):<7} {ratios}")


def _params_path(sport):
    return os.path.join(ROOT, "data", f"dist_params_{sport.lower()}.json")


def save_params(sport, cutoff):
    d = {"cutoff": str(cutoff),
         "sigma_ab": {m: list(v) for (s, m), v in SIGMA_AB.items() if s == sport},
         "nb_r": {m: v for (s, m), v in NB_R.items() if s == sport}}
    with open(_params_path(sport), "w") as f:
        json.dump(d, f, indent=1)
    return _params_path(sport)


def load_params(sport):
    try:
        with open(_params_path(sport)) as f:
            d = json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"{_params_path(sport)} missing - run "
                                f"build_modelset --sport {sport} first")
    for m, ab in d["sigma_ab"].items():
        SIGMA_AB[(sport, m)] = tuple(ab)
    for m, r in d["nb_r"].items():
        NB_R[(sport, m)] = r
    return d
