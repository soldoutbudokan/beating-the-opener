"""Grade archived props against wehoop box scores.

Joins on (normalized player name, game date +/- 1 day). A prop is void if the
player did not play (DNP -> books void O/U props). Adds to props.pkl rows:
  actual   the player's stat that game
  void     True if no minutes
  matched  False if no box-score row found (investigate if common)

Output: data/graded.pkl (same long book-level format as props.pkl + grading cols)
"""
import glob
import os
import re
import unicodedata

import numpy as np
import pandas as pd

ROOT = os.path.join(os.path.dirname(__file__), "..")

STAT_COLS = {
    "points": ["points"], "rebounds": ["rebounds"], "assists": ["assists"],
    "threes": ["three_point_field_goals_made"], "steals": ["steals"],
    "blocks": ["blocks"], "turnovers": ["turnovers"],
    "pra": ["points", "rebounds", "assists"],
    "pts_ast": ["points", "assists"], "pts_reb": ["points", "rebounds"],
    "reb_ast": ["rebounds", "assists"], "stl_blk": ["steals", "blocks"],
}


def norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z ]", "", s.lower()).strip()


def load_box():
    parts = []
    for path in sorted(glob.glob(os.path.join(ROOT, "data", "wehoop", "player_box_*.parquet"))):
        year = int(path[-12:-8])
        if year >= 2025:
            parts.append(pd.read_parquet(path))
    box = pd.concat(parts, ignore_index=True)
    box["nname"] = box["athlete_display_name"].map(norm)
    box["date"] = box["game_date"].astype(str).str[:10]
    return box


def main():
    props = pd.read_pickle(os.path.join(ROOT, "data", "props.pkl"))
    box = load_box()

    need = ["nname", "date", "minutes", "did_not_play"] + sorted(
        {c for cols in STAT_COLS.values() for c in cols})
    idx = {}
    for r in box[need].itertuples(index=False):
        idx[(r.nname, r.date)] = r

    def find(nname, date):
        r = idx.get((nname, date))
        if r is None:  # BP timestamps can straddle midnight vs box-score date
            d = pd.Timestamp(date)
            r = idx.get((nname, str((d + pd.Timedelta("1D")).date()))) or \
                idx.get((nname, str((d - pd.Timedelta("1D")).date())))
        return r

    uniq = props[["event_id", "date", "market", "player"]].drop_duplicates().copy()
    uniq["nname"] = uniq["player"].map(norm)
    actuals, voids, matched = [], [], []
    for r in uniq.itertuples(index=False):
        b = find(r.nname, r.date)
        if b is None:
            actuals.append(np.nan), voids.append(True), matched.append(False)
            continue
        played = (not b.did_not_play) and pd.notna(b.minutes) and b.minutes > 0
        val = sum(getattr(b, c) for c in STAT_COLS[r.market]) if played else np.nan
        actuals.append(val), voids.append(not played), matched.append(True)
    uniq["actual"], uniq["void"], uniq["matched"] = actuals, voids, matched

    graded = props.merge(uniq.drop(columns="nname"),
                         on=["event_id", "date", "market", "player"], how="left")
    graded.to_pickle(os.path.join(ROOT, "data", "graded.pkl"))

    u = uniq
    print(f"unique props: {len(u)}")
    print(f"  matched to box score: {u.matched.mean():.1%}")
    print(f"  void (DNP): {(u.void & u.matched).mean():.1%}")
    um = u[~u.matched]
    if len(um):
        print("  unmatched sample:", um[["date", "player"]].head(8).values.tolist())


if __name__ == "__main__":
    main()
