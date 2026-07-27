"""Build the clean modelling table (v4).

Replaces the garbage-time-contaminated v1 availability/RAPM features with the
rotation-restricted v2 versions. Includes an automatic leakage guard.
"""
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import advanced_features as af  # noqa: E402
import availability_v2 as av2  # noqa: E402
import features  # noqa: E402
import rapm  # noqa: E402
from market import add_market_probs, load_games_odds  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "raw", "dataset_v4.csv")
ROT_MIN = 15.0

MARKET_KEEP = [
    "game_id", "mkt_mult", "mkt_shin", "mkt_add", "mkt_power", "mkt_open_mult",
    "home_ml_close", "away_ml_close", "home_ml_open", "away_ml_open",
    "market_spread", "market_total", "total_pts", "home_score", "away_score",
    "mkt_overround",
]


def leakage_guard(X, cols, thresh=0.12):
    """Flag any feature suspiciously correlated with |margin| (a game outcome)."""
    am = X.margin.abs().values
    bad = []
    for c in cols:
        v = X[c].astype(float).values
        if not np.isfinite(v).any() or np.nanstd(v) == 0:
            continue
        r = np.corrcoef(np.nan_to_num(v), am)[0, 1]
        if abs(r) > thresh:
            bad.append((c, r))
    return sorted(bad, key=lambda x: -abs(x[1]))


def main():
    t0 = time.time()
    df = load_games_odds()
    df = add_market_probs(df)
    d = df[df.season_type.isin([2, 3, 5])].copy()
    d["game_id"] = d.game_id.astype(str)

    base = features.build(d)
    base["game_id"] = base.game_id.astype(str)

    eff = af.team_efficiency(d)
    e = af.build_efficiency_features(d, eff)
    e["game_id"] = e.game_id.astype(str)

    srs = af.ridge_ratings(d)
    srs["game_id"] = srs.game_id.astype(str)

    pb = rapm.load_player_minutes()
    _, (pids, hist) = rapm.build(d, pb)
    rp2 = av2.rapm_lineup(d, pb, hist, pids, rot_min=ROT_MIN)
    rp2["game_id"] = rp2.game_id.astype(str)

    avail = av2.build(d, pb, rot_min=ROT_MIN)
    avail["game_id"] = avail.game_id.astype(str)
    print(f"features done ({time.time()-t0:.1f}s)")

    X = (base.merge(e, on="game_id")
             .merge(srs, on="game_id")
             .merge(avail, on="game_id")
             .merge(rp2, on="game_id"))
    keep = [c for c in MARKET_KEEP if c in d.columns]
    X = X.merge(d[keep], on="game_id")

    X["srs_plus_rapm2"] = X.srs_pred_margin + X.rapm2_pred_margin
    X["rapm2_x_rest"] = X.rapm2_diff * X.rest_diff
    X["elo_srs_gap"] = X.elo_diff / 25.0 - X.srs_diff
    X["form_gap"] = X.ewma_margin_diff - X.srs_diff
    X["is_playoff"] = (X.season_type == 3).astype(int)
    X["early_season"] = ((X.home_gp < 12) | (X.away_gp < 12)).astype(int)
    X["rot_missing_x_srs"] = X.rot_missing_diff * np.sign(X.srs_diff)

    X.to_csv(OUT, index=False)
    print(f"wrote {X.shape} -> {OUT}")

    import model as M
    cols = M.blind_features(X)
    bad = leakage_guard(X, cols)
    print("\n=== LEAKAGE GUARD: |corr(feature, |margin|)| > 0.12 ===")
    if not bad:
        print("  clean - no feature is suspiciously tied to game outcome")
    for c, r in bad[:15]:
        print(f"  {c:32s} {r:+.4f}")
    return X


if __name__ == "__main__":
    main()
