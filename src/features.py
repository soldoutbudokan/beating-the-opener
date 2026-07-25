"""Build a leak-free player-game feature panel from wehoop box scores.

Every feature is computed from STRICTLY EARLIER games (shift-then-ewm within
player/team). Output: data/panel.pkl, one row per player-game actually played,
2003-present.

build_panel() also accepts appended FIXTURE stub rows (tonight's games, stats
NaN, is_fixture=True): they receive the player's state-as-of-today without
polluting anyone else's features - used by live_pipeline.

Availability features (the injury problem):
  absent_ew_min       EW minutes of teammates missing tonight who played in one
                      of the team's last 2 games. Known at tip (props void on
                      DNP) - fair vs the CLOSE, not vs the OPEN.
  absent_prior_ew_min only teammates ALSO missing the previous game (ongoing
                      publicly-known absences) - fair vs the OPEN. For fixture
                      rows both are set to the ongoing-absence version.
"""
import glob
import os

import numpy as np
import pandas as pd

ROOT = os.path.join(os.path.dirname(__file__), "..")

STATS = ["minutes", "points", "rebounds", "assists",
         "three_point_field_goals_made", "three_point_field_goals_attempted",
         "field_goals_attempted", "free_throws_attempted",
         "steals", "blocks", "turnovers"]
SHORT = {"three_point_field_goals_made": "tpm",
         "three_point_field_goals_attempted": "tpa",
         "field_goals_attempted": "fga", "free_throws_attempted": "fta"}
ALPHAS = {"f": 0.18, "s": 0.05}  # fast / slow EW
ALLSTAR = {"CLA", "COL", "TMW", "TMS", "USA", "WNBA", "LIB", "WIL"}


def load_player_box():
    parts = [pd.read_parquet(p) for p in sorted(
        glob.glob(os.path.join(ROOT, "data", "wehoop", "player_box_*.parquet")))]
    box = pd.concat(parts, ignore_index=True)
    box = box[~box.team_abbreviation.isin(ALLSTAR)
              & ~box.opponent_team_abbreviation.isin(ALLSTAR)]
    box["game_date"] = pd.to_datetime(box["game_date"])
    for c in STATS:
        box[c] = pd.to_numeric(box[c], errors="coerce")
    box = box.sort_values(["game_date", "game_id"]).reset_index(drop=True)
    return box


def load_team_box():
    parts = [pd.read_parquet(p) for p in sorted(
        glob.glob(os.path.join(ROOT, "data", "wehoop", "team_box_*.parquet")))]
    tb = pd.concat(parts, ignore_index=True)
    tb = tb[~tb.team_abbreviation.isin(ALLSTAR)
            & ~tb.opponent_team_abbreviation.isin(ALLSTAR)]
    tb["game_date"] = pd.to_datetime(tb["game_date"])
    for c in ["field_goals_attempted", "free_throws_attempted", "total_turnovers",
              "offensive_rebounds", "team_score", "opponent_team_score",
              "assists", "total_rebounds", "three_point_field_goals_attempted"]:
        tb[c] = pd.to_numeric(tb[c], errors="coerce")
    return tb


def ew_features(played):
    """Per-player shifted EW means of each stat + per-minute rates."""
    df = played.sort_values(["athlete_id", "game_date"]).copy()
    g = df.groupby("athlete_id", sort=False)
    out = {}
    for c in STATS:
        name = SHORT.get(c, c[:3] if c != "minutes" else "min")
        prev = g[c].shift(1)
        for tag, a in ALPHAS.items():
            out[f"{name}_ew{tag}"] = (
                prev.groupby(df.athlete_id, sort=False)
                .transform(lambda s, a=a: s.ewm(alpha=a, min_periods=1).mean()))
    for c in ["points", "rebounds", "assists",
              "three_point_field_goals_made", "steals", "blocks", "turnovers"]:
        name = SHORT.get(c, c[:3])
        rate = df[c].where(df.minutes > 0) / df.minutes
        prev_rate = rate.groupby(df.athlete_id, sort=False).shift(1)
        out[f"{name}_rate_ewf"] = (
            prev_rate.groupby(df.athlete_id, sort=False)
            .transform(lambda s: s.ewm(alpha=ALPHAS["f"], min_periods=1).mean()))
    out["gp"] = g.cumcount()
    out["rest"] = (df.game_date - g["game_date"].shift(1)).dt.days.clip(upper=30)
    out["started_ewf"] = (
        g["starter"].shift(1).astype(float).groupby(df.athlete_id, sort=False)
        .transform(lambda s: s.ewm(alpha=ALPHAS["f"], min_periods=1).mean()))
    feat = pd.DataFrame(out, index=df.index)
    return pd.concat([df, feat], axis=1)


def team_features(tb):
    """Team pace/scoring + opponent allowances, shifted EW, from team box."""
    tb = tb.copy()
    tb["poss"] = (tb.field_goals_attempted - tb.offensive_rebounds
                  + tb.total_turnovers + 0.44 * tb.free_throws_attempted)
    tb = tb.sort_values(["team_id", "game_date"])
    g = tb.groupby("team_id", sort=False)
    for src, name in [("poss", "pace"), ("team_score", "pts_for"),
                      ("opponent_team_score", "pts_against"),
                      ("assists", "ast_for"), ("total_rebounds", "reb_for"),
                      ("three_point_field_goals_attempted", "tpa_for")]:
        prev = g[src].shift(1)
        tb[f"tm_{name}_ew"] = (prev.groupby(tb.team_id, sort=False)
                               .transform(lambda s: s.ewm(alpha=0.10, min_periods=1).mean()))
    return tb[["game_id", "team_id", "tm_pace_ew", "tm_pts_for_ew",
               "tm_pts_against_ew", "tm_ast_for_ew", "tm_reb_for_ew",
               "tm_tpa_for_ew"]]


def absence_features(box, played, fixture_gids=frozenset()):
    """Per (game, team): EW-minute weight of missing regulars."""
    ew_min = played.set_index(["athlete_id", "game_id"])["min_ewf"]
    rows = []
    for tid, tg in box.groupby("team_id", sort=False):
        games = tg.drop_duplicates("game_id")[["game_id", "game_date"]] \
                  .sort_values("game_date")
        gids = games.game_id.tolist()
        roster_hist = {}  # athlete -> last game index played
        played_sets = {gid: set(tg[(tg.game_id == gid) & (tg.minutes > 0)]
                                .athlete_id) for gid in gids}
        for i, gid in enumerate(gids):
            is_fix = gid in fixture_gids
            tonight = played_sets[gid]
            absent = absent_prior = 0.0
            for ath, last_i in roster_hist.items():
                if i - last_i > 2:
                    continue
                w = ew_min.get((ath, gids[last_i]), np.nan)
                if np.isnan(w) or w < 12:
                    continue
                if is_fix:
                    # tonight's roster unknown: count ongoing absences only
                    if last_i <= i - 2:
                        absent += w
                        absent_prior += w
                elif ath not in tonight:
                    absent += w
                    if last_i < i - 1:
                        absent_prior += w
            rows.append({"game_id": gid, "team_id": tid,
                         "absent_ew_min": absent,
                         "absent_prior_ew_min": absent_prior})
            for ath in tonight:
                roster_hist[ath] = i
    return pd.DataFrame(rows)


def build_panel(box, tb, fixture_gids=frozenset()):
    """box/tb may include fixture stub rows (is_fixture=True, stats NaN)."""
    if "is_fixture" not in box.columns:
        box["is_fixture"] = False
    played = box[((box.minutes > 0) & ~box.did_not_play.fillna(False).astype(bool))
                 | box.is_fixture].copy()
    panel = ew_features(played)
    tf = team_features(tb)
    panel = panel.merge(tf, on=["game_id", "team_id"], how="left")
    opp = tf.rename(columns={c: c.replace("tm_", "opp_") for c in tf.columns
                             if c.startswith("tm_")})
    panel = panel.merge(opp, left_on=["game_id", "opponent_team_id"],
                        right_on=["game_id", "team_id"], how="left",
                        suffixes=("", "_oppdup"))
    panel = panel.drop(columns=[c for c in panel.columns if c.endswith("_oppdup")])
    ab = absence_features(box, panel, fixture_gids)
    panel = panel.merge(ab, on=["game_id", "team_id"], how="left")
    panel["home"] = (panel.home_away == "home").astype(int)
    return panel


def main():
    box = load_player_box()
    tb = load_team_box()
    panel = build_panel(box, tb)
    panel = panel[~panel.is_fixture]
    print(f"player-games played: {len(panel)} "
          f"({box.game_date.min().date()} .. {box.game_date.max().date()})")
    panel.to_pickle(os.path.join(ROOT, "data", "panel.pkl"))
    feat_cols = [c for c in panel.columns if c.endswith(("_ewf", "_ews", "_ew"))
                 or c in ("gp", "rest", "home", "absent_ew_min",
                          "absent_prior_ew_min")]
    print(f"panel: {panel.shape}, {len(feat_cols)} feature cols")


if __name__ == "__main__":
    main()
