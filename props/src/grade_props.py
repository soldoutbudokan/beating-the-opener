"""Grade archived props against native box scores, per sport.

Port of wnba/src/grade_props.py with two structural changes:
  - joins by (native game id via data/event_map_<sport>.pkl, normalized
    player name) - NEVER (name, date +/- 1): BP dates are UTC, boxscores are
    local, and MLB doubleheaders break date joins (AUDIT H1/C2).
  - per-sport role tables (MLB pitcher/batter, NHL skater/goalie) so a
    same-named player in the other role can't be picked up by mistake.

Void rules (approximations of book rules, monitored at G0.3):
  MLB pitcher markets: void unless the player started that game.
  MLB batter markets:  void unless PA >= 1.
  NBA:                 void unless minutes > 0.
  NFL:                 void unless the player has a stat row that week.
  NHL skater markets:  void unless TOI > 0.
  NHL saves:           void unless the goalie started.

Output: data/graded_<sport>.pkl (props_<sport>.pkl rows + actual/void/matched)

Usage: python3 src/grade_props.py --sport MLB
"""
import argparse
import glob
import os
import re
import unicodedata

import numpy as np
import pandas as pd

from sports_cfg import SPORTS

ROOT = os.path.join(os.path.dirname(__file__), "..")

# market -> (role table, [stat columns summed])
STAT_COLS = {
    "MLB": {
        "strikeouts": ("pitcher", ["k"]), "outs_recorded": ("pitcher", ["outs"]),
        "hits_allowed": ("pitcher", ["h_allowed"]),
        "walks_allowed": ("pitcher", ["bb_allowed"]),
        "earned_runs": ("pitcher", ["er"]),
        "hits": ("batter", ["h"]), "total_bases": ("batter", ["tb"]),
        "hrr": ("batter", ["h", "r", "rbi"]), "homeruns": ("batter", ["hr"]),
        "runs": ("batter", ["r"]), "rbi": ("batter", ["rbi"]),
        "stolen_bases": ("batter", ["sb"]), "singles": ("batter", ["b1"]),
        "doubles": ("batter", ["d2"]), "triples": ("batter", ["t3"]),
    },
    "NBA": {
        "points": ("skater", ["points"]), "rebounds": ("skater", ["rebounds"]),
        "assists": ("skater", ["assists"]),
        "threes": ("skater", ["three_point_field_goals_made"]),
        "pra": ("skater", ["points", "rebounds", "assists"]),
        "pts_ast": ("skater", ["points", "assists"]),
        "pts_reb": ("skater", ["points", "rebounds"]),
        "reb_ast": ("skater", ["rebounds", "assists"]),
        "steals": ("skater", ["steals"]), "blocks": ("skater", ["blocks"]),
    },
    "NFL": {
        "passing_yards": ("skater", ["passing_yards"]),
        "passing_completions": ("skater", ["completions"]),
        "passing_attempts": ("skater", ["attempts"]),
        "passing_tds": ("skater", ["passing_tds"]),
        "interceptions": ("skater", ["passing_interceptions"]),
        "receptions": ("skater", ["receptions"]),
        "receiving_yards": ("skater", ["receiving_yards"]),
        "rushing_yards": ("skater", ["rushing_yards"]),
        "rushing_attempts": ("skater", ["carries"]),
        "rush_rec_yards": ("skater", ["rushing_yards", "receiving_yards"]),
    },
    "NHL": {
        "goals": ("skater", ["goals"]), "points": ("skater", ["points"]),
        "assists": ("skater", ["assists"]), "shots": ("skater", ["sog"]),
        "blocked_shots": ("skater", ["blocked"]),
        "saves": ("goalie", ["saves"]),
    },
}

SUFFIXES = re.compile(r"\s+(jr|sr|ii|iii|iv|v)$")


def norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z ]", "", s.lower()).strip()
    return SUFFIXES.sub("", s)


def load_boxes(sport):
    """Return {role: DataFrame(native_id, nname, played, <stat cols>)}."""
    ddir = os.path.join(ROOT, "data", sport.lower())
    if sport == "MLB":
        pit = pd.concat([pd.read_parquet(p) for p in
                         sorted(glob.glob(os.path.join(ddir, "pitcher_box_*.parquet")))],
                        ignore_index=True)
        bat = pd.concat([pd.read_parquet(p) for p in
                         sorted(glob.glob(os.path.join(ddir, "batter_box_*.parquet")))],
                        ignore_index=True)
        pit["native_id"], pit["nname"] = pit.gamePk, pit.name.map(norm)
        pit["played"] = pit.started.fillna(False)
        bat["native_id"], bat["nname"] = bat.gamePk, bat.name.map(norm)
        bat["played"] = bat.pa.fillna(0) >= 1
        bat["b1"] = bat.h - bat.d2 - bat.t3 - bat.hr
        return {"pitcher": pit, "batter": bat}
    if sport == "NBA":
        box = pd.concat([pd.read_parquet(p) for p in
                         sorted(glob.glob(os.path.join(ddir, "player_box_*.parquet")))],
                        ignore_index=True)
        box["native_id"] = box.game_id
        box["nname"] = box.athlete_display_name.map(norm)
        dnp = box.did_not_play if "did_not_play" in box else False
        box["played"] = (~dnp) & box.minutes.fillna(0).gt(0)
        return {"skater": box}
    if sport == "NFL":
        st = pd.concat([pd.read_parquet(p) for p in
                        sorted(glob.glob(os.path.join(ddir, "stats_player_week_*.parquet")))],
                       ignore_index=True)
        team_col = "team" if "team" in st else "recent_team"
        if "passing_interceptions" not in st and "interceptions" in st:
            st["passing_interceptions"] = st["interceptions"]
        if "game_id" not in st:
            games = pd.read_csv(os.path.join(ddir, "games.csv"))
            long = pd.concat([
                games[["game_id", "season", "week", "home_team"]]
                .rename(columns={"home_team": "tm"}),
                games[["game_id", "season", "week", "away_team"]]
                .rename(columns={"away_team": "tm"}),
            ])
            st = st.merge(long, left_on=["season", "week", team_col],
                          right_on=["season", "week", "tm"], how="left")
        st["native_id"] = st.game_id
        st["nname"] = st.player_display_name.map(norm)
        st["played"] = True  # presence of a stat row is the play indicator
        return {"skater": st}
    if sport == "NHL":
        sk = pd.concat([pd.read_parquet(p) for p in
                        sorted(glob.glob(os.path.join(ddir, "skater_box_*.parquet")))],
                       ignore_index=True)
        gl = pd.concat([pd.read_parquet(p) for p in
                        sorted(glob.glob(os.path.join(ddir, "goalie_box_*.parquet")))],
                       ignore_index=True)
        players = pd.concat([pd.read_parquet(p) for p in
                             sorted(glob.glob(os.path.join(ddir, "players_*.parquet")))],
                            ignore_index=True).drop_duplicates("pid")
        players["full_nname"] = (players["first"].astype(str) + " "
                                 + players["last"].astype(str)).map(norm)
        pid2name = dict(zip(players.pid, players.full_nname))

        def nhl_name(df):
            full = df.pid.map(pid2name)
            # roster gaps: fall back to the abbreviated boxscore name
            return full.fillna(df.name_abbr.map(norm))

        sk["native_id"], sk["nname"] = sk.game_id, nhl_name(sk)
        sk["played"] = sk.toi.fillna("00:00").ne("00:00")
        gl["native_id"], gl["nname"] = gl.game_id, nhl_name(gl)
        gl["played"] = gl.starter.fillna(False)
        return {"skater": sk, "goalie": gl}
    raise ValueError(sport)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", required=True, choices=sorted(SPORTS))
    args = ap.parse_args()
    sport = args.sport
    sl = sport.lower()

    props = pd.read_pickle(os.path.join(ROOT, "data", f"props_{sl}.pkl"))
    emap = pd.read_pickle(os.path.join(ROOT, "data", f"event_map_{sl}.pkl"))
    props = props.merge(emap[["event_id", "native_id"]], on="event_id", how="left")
    boxes = load_boxes(sport)
    stat_cols = STAT_COLS[sport]

    idx, known = {}, {}
    for role, box in boxes.items():
        need = sorted({c for r, cols in stat_cols.values() if r == role
                       for c in cols})
        sub = box[["native_id", "nname", "played"] + need]
        d = {}
        for r in sub.itertuples(index=False):
            d[(r.native_id, r.nname)] = r
        idx[role] = d
        # box tables only contain players who appeared; a prop subject with a
        # season-known name who is absent from THIS game is a true DNP (void),
        # while a never-seen name is an identity-resolution failure (unmatched)
        known[role] = set(box.nname)

    uniq = props[["event_id", "native_id", "market", "player"]].drop_duplicates()
    uniq = uniq.copy()
    uniq["nname"] = uniq.player.map(norm)
    actuals, voids, matched = [], [], []
    for r in uniq.itertuples(index=False):
        role, cols = stat_cols.get(r.market, (None, None))
        b = idx.get(role, {}).get((r.native_id, r.nname)) if role else None
        if b is None or pd.isna(r.native_id):
            is_dnp = (role and pd.notna(r.native_id)
                      and r.nname in known.get(role, ()))
            actuals.append(np.nan)
            voids.append(True)
            matched.append(bool(is_dnp))
            continue
        if b.played:
            actuals.append(float(sum(getattr(b, c) for c in cols)))
            voids.append(False)
        else:
            actuals.append(np.nan)
            voids.append(True)
        matched.append(True)
    uniq["actual"], uniq["void"], uniq["matched"] = actuals, voids, matched

    graded = props.merge(
        uniq.drop(columns="nname"),
        on=["event_id", "native_id", "market", "player"], how="left")
    graded.to_pickle(os.path.join(ROOT, "data", f"graded_{sl}.pkl"))

    u = uniq
    print(f"{sport} unique props: {len(u)}")
    print(f"  mapped to a native game: {u.native_id.notna().mean():.1%}")
    print(f"  matched to a box row:    {u.matched.mean():.1%}")
    print(f"  void (didn't play/start): {(u.void & u.matched).mean():.1%}")
    print("  by market: matched% / void%")
    for m, g in u.groupby("market"):
        print(f"    {m:<18} {g.matched.mean():6.1%} {(g.void & g.matched).mean():6.1%}")
    um = u[~u.matched]
    if len(um):
        print("  unmatched sample:",
              um[["market", "player"]].head(8).values.tolist())


if __name__ == "__main__":
    main()
