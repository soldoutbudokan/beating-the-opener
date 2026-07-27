"""Assemble the modelling table: base + efficiency + SRS + availability + RAPM."""
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import advanced_features as af  # noqa: E402
import features  # noqa: E402
import rapm  # noqa: E402
from market import add_market_probs, load_games_odds  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "raw", "dataset_v3.csv")

MARKET_KEEP = [
    "game_id", "mkt_mult", "mkt_shin", "mkt_add", "mkt_power", "mkt_open_mult",
    "home_ml_close", "away_ml_close", "home_ml_open", "away_ml_open",
    "market_spread", "market_total", "total_pts", "home_score", "away_score",
    "mkt_overround",
]


def main():
    t0 = time.time()
    df = load_games_odds()
    df = add_market_probs(df)
    d = df[df.season_type.isin([2, 3, 5])].copy()
    d["game_id"] = d.game_id.astype(str)
    print(f"games {len(d)}  ({time.time()-t0:.1f}s)")

    base = features.build(d)
    base["game_id"] = base.game_id.astype(str)

    eff = af.team_efficiency(d)
    e = af.build_efficiency_features(d, eff)
    e["game_id"] = e.game_id.astype(str)

    srs = af.ridge_ratings(d)
    srs["game_id"] = srs.game_id.astype(str)

    avail = af.build_availability(d)
    avail["game_id"] = avail.game_id.astype(str)

    pb = rapm.load_player_minutes()
    rp, _ = rapm.build(d, pb)
    rp["game_id"] = rp.game_id.astype(str)
    print(f"features done ({time.time()-t0:.1f}s)")

    X = (base.merge(e, on="game_id")
             .merge(srs, on="game_id")
             .merge(avail, on="game_id")
             .merge(rp, on="game_id"))

    keep = [c for c in MARKET_KEEP if c in d.columns]
    X = X.merge(d[keep], on="game_id")

    # Interaction terms the tree models cannot easily synthesise.
    X["rapm_x_rest"] = X.rapm_diff * X.rest_diff
    X["srs_plus_rapm"] = X.srs_pred_margin + X.rapm_pred_margin
    X["elo_srs_gap"] = X.elo_diff / 25.0 - X.srs_diff
    X["form_gap"] = X.ewma_margin_diff - X.srs_diff
    X["is_playoff"] = (X.season_type == 3).astype(int)
    X["days_into_season"] = X.groupby("season_year").cumcount() / 1200.0
    X["early_season"] = ((X.home_gp < 12) | (X.away_gp < 12)).astype(int)

    X.to_csv(OUT, index=False)
    print(f"wrote {X.shape} -> {OUT}  ({time.time()-t0:.1f}s)")
    return X


if __name__ == "__main__":
    main()
