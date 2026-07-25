"""Two-way odds utilities for O/U prop markets."""
import numpy as np


def amer_to_prob(cost):
    """American odds -> raw implied probability (with vig)."""
    c = np.asarray(cost, float)
    with np.errstate(invalid="ignore"):
        return np.where(c < 0, -c / (-c + 100.0), 100.0 / (c + 100.0))


def amer_to_dec(cost):
    """American odds -> decimal odds."""
    c = np.asarray(cost, float)
    return np.where(c < 0, 1.0 + 100.0 / -c, 1.0 + c / 100.0)


def devig_prop(p_over_raw, p_under_raw):
    """Proportional (multiplicative) devig for a two-way market."""
    s = p_over_raw + p_under_raw
    return p_over_raw / s


def devig_power(p_over_raw, p_under_raw, tol=1e-10):
    """Power devig: find k with p_o^k + p_u^k = 1 (vectorized bisection).

    Handles favorite-longshot bias better than proportional when the two
    prices are asymmetric.
    """
    po = np.asarray(p_over_raw, float)
    pu = np.asarray(p_under_raw, float)
    lo = np.full(po.shape, 0.5)
    hi = np.full(po.shape, 10.0)
    for _ in range(80):
        k = (lo + hi) / 2
        s = po ** k + pu ** k
        too_big = s > 1.0  # need larger k
        lo = np.where(too_big, k, lo)
        hi = np.where(too_big, hi, k)
        if np.nanmax(np.abs(s - 1.0)) < tol:
            break
    return po ** k


def ll_binary(p, y):
    """Per-observation log loss for binary outcome."""
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    y = np.asarray(y, float)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))
