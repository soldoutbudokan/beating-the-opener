"""Per-market stat distributions: (line, p_over) <-> implied mean.

Volume stats (points, rebounds, assists, combos): Normal with sigma(mu) fitted
per market from the panel (variance of actual around the player's fast EW mean).
Low-count stats (threes, steals, blocks, turnovers): Poisson.

Over a line L means actual >= floor(L)+1 - which is ceil(L) on a half-line,
but L+1 on a whole-number line, where L itself pushes. With continuity that is
threshold L+0.5 under the Normal (unchanged either way) and floor(L)+1 under
the Poisson. Whole-number lines are ~6% of archived consensus closes; using
ceil() there made "over 2.0" mean P(X>=2) instead of P(X>=3), a ~27pp error.
"""
import numpy as np
from scipy import stats as sps

POISSON = {"threes", "steals", "blocks", "turnovers", "stl_blk"}

# sigma^2 = a + b * mu vs the player's fast EW mean, from fit_sigma() on the
# 2003-2026 panel (slightly wide: includes EW-estimate error; consistent
# across open/close/model so comparisons stay fair)
SIGMA_AB = {
    "points": (6.86, 2.44), "rebounds": (0.67, 1.35), "assists": (0.34, 1.14),
    "pra": (16.62, 2.14), "pts_ast": (9.07, 2.27), "pts_reb": (12.59, 2.29),
    "reb_ast": (2.52, 1.23),
}


def sigma(market, mu):
    a, b = SIGMA_AB[market]
    return np.sqrt(np.maximum(a + b * np.maximum(mu, 0.0), 0.25))


def p_over(market, mu, line):
    """P(actual > line) given mean mu (half-lines assumed)."""
    mu = np.asarray(mu, float)
    line = np.asarray(line, float)
    if market in POISSON:
        k = np.floor(line).astype(int) + 1  # need >= k successes
        return 1.0 - sps.poisson.cdf(k - 1, np.maximum(mu, 1e-6))
    return 1.0 - sps.norm.cdf(line + 0.5, mu, sigma(market, mu))


def implied_mu(market, line, p):
    """Invert p_over: the mean the market believes, given its line and price."""
    line = np.asarray(line, float)
    p = np.clip(np.asarray(p, float), 0.02, 0.98)
    if market in POISSON:
        lo = np.full(line.shape, 0.01)
        hi = np.full(line.shape, 40.0)
        k = np.floor(line).astype(int) + 1
        for _ in range(60):
            mid = (lo + hi) / 2
            pm = 1.0 - sps.poisson.cdf(k - 1, mid)
            lo = np.where(pm < p, mid, lo)
            hi = np.where(pm < p, hi, mid)
        return (lo + hi) / 2
    mu = line + 0.5  # sigma depends on mu -> fixed-point, converges fast
    for _ in range(4):
        mu = line + 0.5 + sigma(market, mu) * sps.norm.ppf(p)
    return mu


def fit_sigma(panel):
    """Refit SIGMA_AB from the panel; returns dict for pasting above."""
    ew = {"points": "poi_ewf", "rebounds": "reb_ewf", "assists": "ass_ewf"}
    out = {}
    combos = {"pra": ["points", "rebounds", "assists"],
              "pts_ast": ["points", "assists"], "pts_reb": ["points", "rebounds"],
              "reb_ast": ["rebounds", "assists"]}
    for mkt in ["points", "rebounds", "assists"]:
        d = panel.dropna(subset=[ew[mkt]])
        d = d[d.gp >= 8]
        mu = d[ew[mkt]]
        resid2 = (d[mkt] - mu) ** 2
        b, a = np.polyfit(mu, resid2, 1)
        out[mkt] = (round(max(a, 0.1), 2), round(max(b, 0.2), 2))
    for mkt, cols in combos.items():
        d = panel.dropna(subset=[ew[c] for c in cols])
        d = d[d.gp >= 8]
        mu = sum(d[ew[c]] for c in cols)
        resid2 = (sum(d[c] for c in cols) - mu) ** 2
        b, a = np.polyfit(mu, resid2, 1)
        out[mkt] = (round(max(a, 0.1), 2), round(max(b, 0.2), 2))
    out["stl_blk"] = out.get("reb_ast", (0.3, 1.0))
    return out
