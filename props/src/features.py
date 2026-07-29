"""Leak-free player-game feature panels, per sport.

Every rolling feature is computed from the player's STRICTLY EARLIER games
(shift(1) then ewm, grouped per player; per team for team context) - no
feature may read the current row's outcome (the wnba discipline).

Panel key: (native_id, nname, role); nname uses grade_props.norm so the
modelset join is by native game id + name, never name+date (AUDIT H1/C2).
Postseason rows are KEPT with a `post` flag; nothing else excluded (spring/
preseason never fetched; NBA All-Star exhibition rows dropped like wnba).

Usage: python3 src/features.py --sport MLB   -> data/panel_mlb.pkl
"""
import argparse
import glob
import os

import numpy as np
import pandas as pd

from grade_props import norm
from sports_cfg import SPORTS

ROOT = os.path.join(os.path.dirname(__file__), "..")

MLB_P_ALPHAS = {"f": 0.25, "s": 0.08}   # pitcher fast/slow EW
MLB_B_ALPHAS = {"f": 0.10, "s": 0.03}   # batter fast/slow EW
MLB_TEAM_ALPHA = 0.05
NBA_ALPHAS = {"f": 0.18, "s": 0.05}
NBA_TEAM_ALPHA = 0.05
# exhibition team codes present in the fetched 2024-2026 hoopR files
NBA_ALLSTAR = {"EAST", "WEST", "CAN", "CHK", "KEN", "SHQ",
               "STARS", "STRIPES", "WORLD"}


def shift_ew(s, key, alpha):
    """EW mean of the player's PRIOR values: shift(1) within key, then ewm."""
    prev = s.groupby(key, sort=False).shift(1)
    return prev.groupby(key, sort=False).transform(
        lambda x, a=alpha: x.ewm(alpha=a, min_periods=1).mean())


def _load_parquets(sport_dir, prefix):
    paths = sorted(glob.glob(os.path.join(ROOT, "data", sport_dir,
                                          f"{prefix}_*.parquet")))
    if not paths:
        raise RuntimeError(f"no {prefix}_*.parquet under data/{sport_dir} - "
                           f"run the fetch script first")
    return pd.concat([pd.read_parquet(p) for p in paths], ignore_index=True)


# ---------------------------------------------------------------- MLB

def _mlb_post_flag(df):
    sched = _load_parquets("mlb", "schedule")
    gtype = sched.drop_duplicates("gamePk").set_index("gamePk")["gameType"]
    return (df.gamePk.map(gtype).fillna("R") != "R").astype(int)


def mlb_team_context(bat):
    """Team offense EW from batter-box aggregates: runs-for + K% (so per pa).
    Merged as tm_* for own team, opp_* for opponent (opp_so_pa_ew is the
    headline strikeouts feature: how much tonight's lineup whiffs)."""
    tg = bat.groupby(["gamePk", "team"], as_index=False).agg(
        date=("date", "first"), runs=("r", "sum"),
        so=("so", "sum"), pa=("pa", "sum"))
    tg["so_pa"] = tg.so / tg.pa
    tg = tg.sort_values(["team", "date", "gamePk"]).reset_index(drop=True)
    tg["tm_runs_ew"] = shift_ew(tg.runs, tg.team, MLB_TEAM_ALPHA)
    tg["tm_so_pa_ew"] = shift_ew(tg.so_pa, tg.team, MLB_TEAM_ALPHA)
    return tg[["gamePk", "team", "tm_runs_ew", "tm_so_pa_ew"]]


def add_mlb_team_context(panel, tc):
    panel = panel.merge(tc, on=["gamePk", "team"], how="left")
    opp = tc.rename(columns={"team": "opp", "tm_runs_ew": "opp_runs_ew",
                             "tm_so_pa_ew": "opp_so_pa_ew"})
    return panel.merge(opp, on=["gamePk", "opp"], how="left")


def pitcher_panel(pit):
    d = pit[pit.started.fillna(False)].copy()  # starts only throughout
    d["date"] = pd.to_datetime(d.date)
    d = d.sort_values(["pid", "date", "gamePk"]).reset_index(drop=True)
    ren = {"pitches": "pit", "bb_allowed": "bb", "h_allowed": "ha"}
    for src in ["k", "outs", "bf", "pitches", "bb_allowed", "h_allowed", "er"]:
        d[ren.get(src, src)] = pd.to_numeric(d[src], errors="coerce")
    key = d.pid
    for c in ["k", "outs", "bf", "pit", "bb", "ha", "er"]:
        for tag, a in MLB_P_ALPHAS.items():
            d[f"{c}_ew{tag}"] = shift_ew(d[c], key, a)
    for num in ("k", "bb"):  # per-batter-faced rates
        rate = d[num].where(d.bf > 0) / d.bf
        d[f"{num}_bf_ewf"] = shift_ew(rate, key, MLB_P_ALPHAS["f"])
    d["gp"] = d.groupby("pid", sort=False).cumcount()  # career START count
    d["rest"] = (d.date - d.groupby("pid", sort=False)["date"].shift(1)
                 ).dt.days.clip(upper=30)
    d["home"] = d.home.astype(int)
    d["post"] = _mlb_post_flag(d)
    d["role"] = "pitcher"
    return d


def batter_panel(bat):
    d = bat[bat.pa.fillna(0) >= 1].copy()
    d["date"] = pd.to_datetime(d.date)
    d = d.sort_values(["pid", "date", "gamePk"]).reset_index(drop=True)
    for c in ["h", "tb", "hr", "r", "rbi", "pa", "so"]:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    key = d.pid
    for c in ["h", "tb", "hr", "r", "rbi", "pa"]:
        for tag, a in MLB_B_ALPHAS.items():
            d[f"{c}_ew{tag}"] = shift_ew(d[c], key, a)
    for c in ["h", "tb", "hr", "so"]:  # per-PA rates
        rate = d[c].where(d.pa > 0) / d.pa
        d[f"{c}_pa_ewf"] = shift_ew(rate, key, MLB_B_ALPHAS["f"])
    # batting order (100=leadoff): known pregame but shifted anyway - one rule
    ordv = (pd.to_numeric(d.order, errors="coerce") / 100.0
            ).groupby(key, sort=False).ffill()
    d["ord_ewf"] = shift_ew(ordv, key, MLB_B_ALPHAS["f"])
    d["gp"] = d.groupby("pid", sort=False).cumcount()
    d["rest"] = (d.date - d.groupby("pid", sort=False)["date"].shift(1)
                 ).dt.days.clip(upper=30)
    d["home"] = d.home.astype(int)
    d["post"] = _mlb_post_flag(d)
    d["role"] = "batter"
    return d


def build_mlb():
    pit = _load_parquets("mlb", "pitcher_box")
    bat = _load_parquets("mlb", "batter_box")
    bat["date"] = pd.to_datetime(bat.date)
    tc = mlb_team_context(bat)
    pp = add_mlb_team_context(pitcher_panel(pit), tc)
    bp = add_mlb_team_context(batter_panel(bat), tc)
    panel = pd.concat([pp, bp], ignore_index=True, sort=False)
    panel["native_id"] = panel.gamePk.astype("int64")
    panel["nname"] = panel.name.map(norm)
    return panel


# ---------------------------------------------------------------- NBA

NBA_STATS = {"minutes": "min", "points": "pts", "rebounds": "reb",
             "assists": "ast", "three_point_field_goals_made": "tpm",
             "steals": "stl", "blocks": "blk"}


def nba_team_features(tb):
    """Team pace + points for/against, shifted EW (alpha 0.05) from team box."""
    tb = tb.copy()
    for c in ["field_goals_attempted", "offensive_rebounds", "total_turnovers",
              "free_throws_attempted", "team_score", "opponent_team_score"]:
        tb[c] = pd.to_numeric(tb[c], errors="coerce")
    tb["poss"] = (tb.field_goals_attempted - tb.offensive_rebounds
                  + tb.total_turnovers + 0.44 * tb.free_throws_attempted)
    tb = tb.sort_values(["team_id", "game_date"]).reset_index(drop=True)
    key = tb.team_id
    for src, name in [("poss", "pace"), ("team_score", "pts_for"),
                      ("opponent_team_score", "pts_against")]:
        tb[f"tm_{name}_ew"] = shift_ew(tb[src], key, NBA_TEAM_ALPHA)
    return tb[["game_id", "team_id", "tm_pace_ew", "tm_pts_for_ew",
               "tm_pts_against_ew"]]


def build_nba():
    box = _load_parquets("nba", "player_box")
    tb = _load_parquets("nba", "team_box")
    for df in (box, tb):
        df["game_date"] = pd.to_datetime(df.game_date)
    box = box[~box.team_abbreviation.isin(NBA_ALLSTAR)
              & ~box.opponent_team_abbreviation.isin(NBA_ALLSTAR)].copy()
    tb = tb[~tb.team_abbreviation.isin(NBA_ALLSTAR)
            & ~tb.opponent_team_abbreviation.isin(NBA_ALLSTAR)]
    for src, name in NBA_STATS.items():
        box[name] = pd.to_numeric(box[src], errors="coerce")

    dnp = box.did_not_play if "did_not_play" in box else False
    d = box[(box["min"] > 0)
            & ~pd.Series(dnp, index=box.index).fillna(False).astype(bool)].copy()
    d = d.sort_values(["athlete_id", "game_date", "game_id"]).reset_index(drop=True)
    key = d.athlete_id
    for name in NBA_STATS.values():
        for tag, a in NBA_ALPHAS.items():
            d[f"{name}_ew{tag}"] = shift_ew(d[name], key, a)
    d["started_ewf"] = shift_ew(d.starter.astype(float), key, NBA_ALPHAS["f"])
    d["gp"] = d.groupby("athlete_id", sort=False).cumcount()
    d["rest"] = (d.game_date - d.groupby("athlete_id", sort=False)
                 ["game_date"].shift(1)).dt.days.clip(upper=30)

    tf = nba_team_features(tb)
    d = d.merge(tf, on=["game_id", "team_id"], how="left")
    opp = tf.rename(columns={c: c.replace("tm_", "opp_") for c in tf.columns
                             if c.startswith("tm_")})
    d = d.merge(opp, left_on=["game_id", "opponent_team_id"],
                right_on=["game_id", "team_id"], how="left",
                suffixes=("", "_oppdup"))
    d = d.drop(columns=[c for c in d.columns if c.endswith("_oppdup")])

    d["home"] = (d.home_away == "home").astype(int)
    d["post"] = (d.season_type != 2).astype(int)  # 3=playoffs, 5=play-in
    d["date"] = d.game_date
    d["team"] = d.team_abbreviation
    d["opp"] = d.opponent_team_abbreviation
    d["native_id"] = d.game_id.astype("int64")
    d["nname"] = d.athlete_display_name.map(norm)
    d["role"] = "skater"  # grade_props role name for NBA
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", required=True, choices=sorted(SPORTS))
    args = ap.parse_args()
    sport = args.sport
    if sport == "MLB":
        panel = build_mlb()
    elif sport == "NBA":
        panel = build_nba()
    else:
        raise NotImplementedError(
            f"{sport}: panel builder not ported - the fetch script exists but "
            f"build_{sport.lower()}() does not (MLB/NBA only for now)")
    out = os.path.join(ROOT, "data", f"panel_{sport.lower()}.pkl")
    panel.to_pickle(out)
    print(f"{sport} panel: {len(panel)} player-games "
          f"({panel.date.min().date()} .. {panel.date.max().date()})")
    for role, g in panel.groupby("role"):
        print(f"  {role}: {len(g)} rows, {g.nname.nunique()} players")
    feat = [c for c in panel.columns if c.endswith(("_ewf", "_ews", "_ew"))
            or c in ("gp", "rest", "home", "post")]
    print(f"  {len(feat)} feature cols -> {out}")


if __name__ == "__main__":
    main()
