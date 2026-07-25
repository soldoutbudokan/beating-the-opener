"""De-vigging and scoring utilities."""
import numpy as np


def implied_raw(odds):
    """odds: (n,3) decimal odds -> raw implied probs (with vig) and booksum."""
    inv = 1.0 / odds
    booksum = inv.sum(axis=1)
    return inv, booksum


def devig_proportional(odds):
    inv, booksum = implied_raw(odds)
    return inv / booksum[:, None]


def devig_shin(odds, iters=50):
    """Shin (1993) de-vig for 3-outcome markets, vectorized bisection on z.

    Accounts for favorite-longshot bias better than proportional scaling.
    """
    inv, booksum = implied_raw(odds)
    pi = inv  # bookmaker implied probs, sum > 1

    def shin_p(z):
        # z: (n,) insider trading fraction
        b = booksum[:, None]
        return (np.sqrt(z[:, None] ** 2 + 4 * (1 - z[:, None]) * pi ** 2 / b)
                - z[:, None]) / (2 * (1 - z[:, None]))

    lo = np.zeros(len(odds))
    hi = np.full(len(odds), 0.2)
    for _ in range(iters):
        mid = (lo + hi) / 2
        s = shin_p(mid).sum(axis=1)
        # s decreases as z increases; want s == 1
        too_high = s > 1.0
        lo = np.where(too_high, mid, lo)
        hi = np.where(too_high, hi, mid)
    p = shin_p((lo + hi) / 2)
    return p / p.sum(axis=1)[:, None]


def log_loss_vec(probs, outcome_idx):
    """Per-match negative log likelihood. probs (n,3), outcome_idx (n,) in {0,1,2}."""
    p = np.clip(probs[np.arange(len(probs)), outcome_idx], 1e-12, 1)
    return -np.log(p)


def brier_vec(probs, outcome_idx):
    y = np.zeros_like(probs)
    y[np.arange(len(probs)), outcome_idx] = 1.0
    return ((probs - y) ** 2).sum(axis=1)


OUTCOME_IDX = {"H": 0, "D": 1, "A": 2}
