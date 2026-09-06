"""Historical WNBA context available *before* a forecast's timestamp.

This research module does not read any live files. It makes postgame states,
then looks them up by publication time. A game is available no earlier than
eight hours after its scheduled tip, or one hour after its last timestamped
play, whichever is later. These are conservative assumptions, not an archive
of when ESPN first published each value. Date-only sources get two full days.

Play-by-play adds shot location/type, assisted scoring relationships, recorded
late-game actions and substitutions. Recorded actions are NOT player tracking,
touches, possession time, or reconstructed minutes. Teammate features describe
prior games only: the eventual roster of a forecast game is never consulted.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


STATS = {
    "minutes": "min", "points": "poi", "rebounds": "reb", "assists": "ass",
    "three_point_field_goals_made": "tpm",
    "three_point_field_goals_attempted": "tpa", "field_goals_attempted": "fga",
    "free_throws_attempted": "fta", "steals": "ste", "blocks": "blo",
    "turnovers": "tur",
}
RATE_STATS = ("poi", "reb", "ass", "tpm", "ste", "blo", "tur")
ALPHAS = {"f": 0.18, "s": 0.05}
ID_COLUMNS = ("game_id", "athlete_id", "team_id", "opponent_team_id")


@dataclass
class StateTables:
    player: pd.DataFrame
    team: pd.DataFrame
    league: pd.DataFrame
    coverage: dict


def _ids(values):
    """Keep numeric IDs stable across parquet int/float/string schemas."""
    return values.astype("string").str.replace(r"\.0$", "", regex=True)


def _normalise(df):
    out = df.copy()
    for col in ID_COLUMNS:
        if col in out:
            out[col] = _ids(out[col])
    return out


def _number(df, col, default=np.nan):
    if col in df:
        return pd.to_numeric(df[col], errors="coerce")
    return pd.Series(default, index=df.index, dtype=float)


def _bool(df, col):
    if col not in df:
        return pd.Series(False, index=df.index)
    return df[col].astype("string").str.lower().isin(("true", "1", "1.0"))


def _datetime(values):
    return pd.to_datetime(values, utc=True, errors="coerce").astype("datetime64[ns, UTC]")


def _game_times(box, schedule, pbp, availability_hours=8):
    """One stable availability timestamp per game, independent of prop rows."""
    source = schedule.copy() if len(schedule) else box.copy()
    source = _normalise(source).drop_duplicates("game_id")
    time_col = next((c for c in ("tip_at", "game_date_time", "date", "game_date")
                     if c in source), None)
    if time_col is None:
        raise ValueError("A schedule tip_at/game_date_time or game_date is required")
    times = source[["game_id"]].copy()
    times["tip_at"] = _datetime(source[time_col])
    # Date-only ESPN columns do not establish the hour of a game.
    lag = pd.Timedelta(days=2) if time_col == "game_date" else pd.Timedelta(hours=availability_hours)
    times["source_at"] = times.tip_at + lag
    if "completed_at" in source:
        completion = _datetime(source.completed_at) + pd.Timedelta(hours=1)
        times["source_at"] = times.source_at.where(times.source_at >= completion,
                                                    completion).fillna(times.source_at)
    if len(pbp) and "wallclock" in pbp:
        last = pd.DataFrame({"game_id": _ids(pbp.game_id),
                             "last_play": _datetime(pbp.wallclock)})
        last = last.groupby("game_id", as_index=False).last_play.max()
        times = times.merge(last, on="game_id", how="left")
        last_available = times.last_play + pd.Timedelta(hours=1)
        times["source_at"] = times.source_at.where(
            times.source_at >= last_available, last_available).fillna(times.source_at)
        times = times.drop(columns="last_play")
    if times.tip_at.isna().any():
        raise ValueError("Missing game timestamps; cannot establish chronology")
    return times


def _ew(df, key, columns, alpha):
    """Adjusted EWs AFTER each completed game (never the stale pregame row)."""
    return df.groupby(key, sort=False)[columns].transform(
        lambda s: s.ewm(alpha=alpha, min_periods=1, adjust=True).mean())


def aggregate_pbp(pbp):
    """Return player-game and team-game descriptions of completed games.

    Shot distance is parsed from explicit feet in the play description first.
    Otherwise transformed ESPN coordinates are accepted only within the court:
    |x| <= 47 and |y| <= 25, with distance measured to the offensive basket
    (+41.75 for home, -41.75 for away in wehoop's transformed coordinates).
    Sentinel coordinates remain missing. Layups/dunks supply a coarse rim zone
    when distance is missing; they do not receive an invented exact distance.
    """
    if pbp is None or not len(pbp):
        return pd.DataFrame(columns=["game_id", "athlete_id"]), pd.DataFrame(
            columns=["game_id", "team_id"]), {"pbp_rows": 0, "pbp_games": 0}
    p = _normalise(pbp)
    p["athlete_id"] = _ids(p.get("athlete_id_1", p.get("athlete_id")))
    text = p.get("text", pd.Series("", index=p.index)).fillna("").str.lower()
    kind = p.get("type_text", pd.Series("", index=p.index)).fillna("").str.lower()
    order = ["game_id"] + [c for c in ("sequence_number", "game_play_number") if c in p]
    p["_text"] = text
    p["_kind"] = kind
    p = p.sort_values(order, kind="stable").reset_index(drop=True)
    text, kind = p._text, p._kind
    # A shot need not have shooting_play=True in every historical schema.
    attempted = _number(p, "points_attempted", 0)
    is_free = kind.str.contains("free throw") | text.str.contains("free throw")
    shot = (_bool(p, "shooting_play") | attempted.isin((2, 3)) |
            text.str.contains(r"\b(?:makes|made|misses|missed)\b")) & ~is_free
    made = shot & (_bool(p, "scoring_play") | text.str.contains(r"\b(?:makes|made)\b"))
    three = shot & ((attempted == 3) | text.str.contains("three[ -]point|3[ -]point|3pt") |
                    (made & _number(p, "score_value").eq(3)))
    distance = pd.to_numeric(text.str.extract(r"(\d+(?:\.\d+)?)[ -](?:foot|ft)\b", expand=False),
                             errors="coerce").where(lambda x: x.between(0, 94))
    x, y = _number(p, "coordinate_x"), _number(p, "coordinate_y")
    valid_xy = x.abs().le(47) & y.abs().le(25)
    home = _ids(p.get("home_team_id", pd.Series(pd.NA, index=p.index)))
    away = _ids(p.get("away_team_id", pd.Series(pd.NA, index=p.index)))
    team_known = p.team_id.eq(home) | p.team_id.eq(away)
    basket_x = pd.Series(np.where(p.team_id.eq(home).fillna(False), 41.75, -41.75), index=p.index)
    xy_distance = np.sqrt((basket_x - x) ** 2 + y ** 2).where(valid_xy & team_known)
    distance = distance.fillna(xy_distance).where(shot)
    layup = text.str.contains("layup|dunk|tip shot|tip in") | kind.str.contains("layup|dunk|tip")
    rim = shot & ~three & (distance.le(4) | (distance.isna() & layup))
    mid = shot & ~three & distance.gt(4)
    zone_known = rim | mid | three
    assisted = made & text.str.contains(r"\b(?:assists|assisted by)\b")
    quarter = _number(p, "period_number").fillna(_number(p, "qtr"))
    score_diff = (_number(p, "home_score") - _number(p, "away_score"))
    # Use score before the event to label close-game actions, even though the
    # completed game's entire record is used only in future forecasts.
    pre_diff = score_diff.groupby(p.game_id).shift(1).fillna(0)
    rebound = kind.str.contains("rebound")
    foul = kind.str.contains("foul") & ~kind.str.contains("turnover|technical|flagrant")
    turnover = kind.str.contains("turnover|traveling|double dribble")
    action = shot | is_free | rebound | foul | turnover
    sub = kind.str.contains("substitution") | text.str.contains("enters the game")
    clock = p.get("clock_display_value", pd.Series("", index=p.index)).astype(str)
    clock_parts = clock.str.extract(r"^(\d+):(\d+(?:\.\d+)?)$")
    remaining = pd.to_numeric(clock_parts[0], errors="coerce") * 60 + pd.to_numeric(
        clock_parts[1], errors="coerce")

    events = p[["game_id", "team_id", "athlete_id"]].copy()
    indicators = {
        "pbp_fga": shot, "pbp_fgm": made, "pbp_rim_fga": rim,
        "pbp_rim_fgm": rim & made, "pbp_mid_fga": mid, "pbp_mid_fgm": mid & made,
        "pbp_three_fga": three, "pbp_three_fgm": three & made,
        "pbp_zone_known": zone_known, "pbp_assisted_fgm": assisted,
        "pbp_pullup_fga": shot & text.str.contains("pullup|pull-up|step back|step-back"),
        "pbp_driving_fga": shot & text.str.contains("driving"),
        "pbp_putback_fga": shot & text.str.contains("putback|tip shot|tip in"),
        "pbp_running_fga": shot & text.str.contains("running"),
        "pbp_recorded_actions": action, "pbp_q4_actions": action & quarter.ge(4),
        "pbp_close_actions": action & quarter.ge(4) & pre_diff.abs().le(5),
        "pbp_early_fouls": foul & quarter.le(2), "pbp_fouls": foul,
        "pbp_off_rebounds": kind.str.contains("offensive rebound"),
        "pbp_def_rebounds": kind.str.contains("defensive rebound"),
        "pbp_sub_in": sub,
    }
    for name, mask in indicators.items():
        events[name] = mask.astype(float)
    events["pbp_distance_sum"] = distance.fillna(0)
    events["pbp_distance_count"] = distance.notna().astype(float)
    metrics = list(indicators) + ["pbp_distance_sum", "pbp_distance_count"]
    player = events.dropna(subset=["athlete_id"]).groupby(
        ["game_id", "athlete_id"], as_index=False)[metrics].sum()
    team = events.dropna(subset=["team_id"]).groupby(
        ["game_id", "team_id"], as_index=False)[metrics].sum()

    # Receiver -> creator IDs are explicitly supplied on assisted makes. Do not
    # infer passes/potential assists from unassisted or missed shots.
    assisters = _ids(p.get("athlete_id_2", pd.Series(pd.NA, index=p.index)))
    assists = p.loc[assisted & assisters.notna(), ["game_id", "athlete_id"]].copy()
    assists = assists.rename(columns={"athlete_id": "receiver_id"})
    assists["athlete_id"] = assisters.loc[assists.index]
    assists["pbp_created_makes"] = 1.0
    assists["pbp_created_rim"] = rim.loc[assists.index].astype(float)
    assists["pbp_created_three"] = three.loc[assists.index].astype(float)
    assists["pbp_created_points"] = np.where(three.loc[assists.index], 3.0, 2.0)
    if len(assists):
        created = assists.groupby(["game_id", "athlete_id"], as_index=False)[
            ["pbp_created_makes", "pbp_created_rim", "pbp_created_three", "pbp_created_points"]].sum()
        links = assists.groupby(["game_id", "athlete_id", "receiver_id"]).size().rename("n").reset_index()
        links["share2"] = (links.n / links.groupby(["game_id", "athlete_id"]).n.transform("sum")) ** 2
        concentration = links.groupby(["game_id", "athlete_id"], as_index=False).share2.sum().rename(
            columns={"share2": "pbp_receiver_concentration"})
        created = created.merge(concentration, on=["game_id", "athlete_id"])
        player = player.merge(created, on=["game_id", "athlete_id"], how="outer")
    for col in ("pbp_created_makes", "pbp_created_rim", "pbp_created_three", "pbp_created_points"):
        if col not in player:
            player[col] = 0.0
        player[col] = player[col].fillna(0)
    if "pbp_receiver_concentration" not in player:
        player["pbp_receiver_concentration"] = np.nan
    outgoing = p.loc[sub & assisters.notna(), ["game_id"]].copy()
    outgoing["athlete_id"] = assisters.loc[outgoing.index]
    outgoing["pbp_sub_out"] = 1.0
    outgoing["pbp_first_q1_exit_minutes"] = ((600 - remaining) / 60).where(
        quarter.eq(1) & remaining.between(0, 600)).loc[outgoing.index]
    out = outgoing.groupby(["game_id", "athlete_id"], as_index=False).agg(
        pbp_sub_out=("pbp_sub_out", "sum"),
        pbp_first_q1_exit_minutes=("pbp_first_q1_exit_minutes", "min"))
    player = player.merge(out, on=["game_id", "athlete_id"], how="left")
    player["pbp_sub_out"] = player.pbp_sub_out.fillna(0)
    coverage = {"pbp_rows": int(len(p)), "pbp_games": int(p.game_id.nunique()),
                "field_goal_events": int(shot.sum()),
                "shot_zone_coverage": float(zone_known.sum() / max(shot.sum(), 1)),
                "shot_distance_coverage": float(distance.notna().sum() / max(shot.sum(), 1)),
                "assisted_makes_with_creator_id": int(len(assists)),
                "assisted_makes": int(assisted.sum())}
    return player, team, coverage


def _ratio(df, numerator, denominator):
    return df[numerator] / df[denominator].where(df[denominator] > 0)


def _profiles(df):
    """Compute rates from a game's observed opportunities; zero-denominator is unknown."""
    out = pd.DataFrame(index=df.index)
    for zone in ("rim", "mid", "three"):
        out[f"pbp_{zone}_share"] = _ratio(df, f"pbp_{zone}_fga", "pbp_fga")
        out[f"pbp_{zone}_accuracy"] = _ratio(df, f"pbp_{zone}_fgm", f"pbp_{zone}_fga")
    for kind in ("pullup", "driving", "putback", "running"):
        out[f"pbp_{kind}_share"] = _ratio(df, f"pbp_{kind}_fga", "pbp_fga")
    out["pbp_assisted_make_share"] = _ratio(df, "pbp_assisted_fgm", "pbp_fgm")
    out["pbp_shot_distance"] = _ratio(df, "pbp_distance_sum", "pbp_distance_count")
    out["pbp_zone_coverage"] = _ratio(df, "pbp_zone_known", "pbp_fga")
    out["pbp_q4_action_share"] = _ratio(df, "pbp_q4_actions", "pbp_recorded_actions")
    out["pbp_close_action_share"] = _ratio(df, "pbp_close_actions", "pbp_recorded_actions")
    return out


def _reconcile_pbp(box, player_events, team_events):
    """Invalidate a game's affected PBP family when counts disagree with boxes.

    This is deliberately conservative. A wrong shooter/creator ID anywhere in
    a game can change player relationships, so that game's entire family is
    missing, not zero, for all players and both teams. Historical averages may
    retain older valid values; family-specific source times expose their age.
    """
    if not len(player_events):
        return player_events, team_events, {}
    player_events, team_events = player_events.copy(), team_events.copy()
    mapping = {
        "shot": {"field_goals_attempted": "pbp_fga", "field_goals_made": "pbp_fgm",
                 "three_point_field_goals_made": "pbp_three_fgm",
                 "three_point_field_goals_attempted": "pbp_three_fga"},
        "assist": {"assists": "pbp_created_makes"},
        "rebound": {"offensive_rebounds": "pbp_off_rebounds", "defensive_rebounds": "pbp_def_rebounds"},
    }
    available_games = set(player_events.game_id)
    metrics = {}
    shot_cols = [c for c in player_events if c.startswith(("pbp_rim_", "pbp_mid_", "pbp_three_"))]
    shot_cols += ["pbp_fga", "pbp_fgm", "pbp_zone_known", "pbp_assisted_fgm", "pbp_pullup_fga",
                  "pbp_driving_fga", "pbp_putback_fga", "pbp_running_fga", "pbp_distance_sum",
                  "pbp_distance_count"]
    family_cols = {"shot": shot_cols,
                   "assist": [c for c in player_events if c.startswith("pbp_created_")] + ["pbp_receiver_concentration"],
                   "rebound": ["pbp_off_rebounds", "pbp_def_rebounds"]}
    for family, pairs in mapping.items():
        if any(c not in box for c in pairs):
            invalid = available_games
            mismatched_rows = None
        else:
            cmp = box[["game_id", "athlete_id"] + list(pairs)].merge(
                player_events[["game_id", "athlete_id"] + list(pairs.values())],
                on=["game_id", "athlete_id"], how="outer", indicator=True)
            cmp = cmp[cmp.game_id.isin(available_games)]
            bad = pd.Series(False, index=cmp.index)
            for raw, event in pairs.items():
                expected = _number(cmp, raw)
                observed = _number(cmp, event).fillna(0)
                # An actor with no played box is acceptable only for zero
                # events in this family (e.g. a bench player substituted in).
                expected = expected.where(cmp._merge.ne("right_only"), 0)
                bad |= expected.isna() | (expected - observed).abs().gt(.01)
            invalid = set(cmp.loc[bad, "game_id"])
            mismatched_rows = int(bad.sum())
        valid_col = f"pbp_{family}_valid"
        player_events[valid_col] = ~player_events.game_id.isin(invalid)
        team_events[valid_col] = ~team_events.game_id.isin(invalid)
        for frame in (player_events, team_events):
            cols = [c for c in family_cols[family] if c in frame]
            frame.loc[frame.game_id.isin(invalid), cols] = np.nan
        metrics[family] = {"available_games": len(available_games), "valid_games": len(available_games - invalid),
                           "invalid_games": len(invalid), "mismatched_player_rows": mismatched_rows}
    return player_events, team_events, metrics


def build_state_tables(box, team, pbp=None, schedule=None, availability_hours=8):
    """Build postgame states through 2025; refuses protected 2026 outcomes.

    Inputs are native wehoop/ESPN pandas tables. Pass a complete team table,
    not just games/teams with betting markets. Optional talent snapshots can
    subsequently be merged into ``states.player`` on athlete_id/game_id.
    """
    box, team = _normalise(box), _normalise(team)
    pbp = pd.DataFrame() if pbp is None else pbp
    schedule = pd.DataFrame() if schedule is None else schedule
    for name, table in (("box", box), ("team", team), ("pbp", pbp)):
        if len(table) and "game_date" in table:
            if (_datetime(table.game_date).dt.year > 2025).any():
                raise ValueError(f"{name}: this research branch refuses 2026+ outcomes")
        if len(table) and "season" in table and (_number(table, "season") > 2025).any():
            raise ValueError(f"{name}: this research branch refuses 2026+ outcomes")
    if availability_hours < 8:
        raise ValueError("availability_hours must be at least the conservative eight-hour base")
    times = _game_times(box, schedule, pbp, availability_hours)
    if (times.tip_at.dt.year > 2025).any():
        raise ValueError("Schedule contains 2026+ games; load research years only")
    excluded = set()
    if len(schedule) and "type_abbreviation" in schedule:
        excluded = set(_ids(schedule.loc[schedule.type_abbreviation.astype(str).str.upper().eq(
            "ALLSTAR"), "game_id"]))
    box = box[~box.game_id.isin(excluded)].copy()
    team = team[~team.game_id.isin(excluded)].copy()
    if len(pbp):
        pbp = pbp[~_ids(pbp.game_id).isin(excluded)]
    p_events, t_events, coverage = aggregate_pbp(pbp)
    for col in STATS:
        box[col] = _number(box, col)
    box = box[box.minutes.gt(0) & box.athlete_id.notna() & ~_bool(box, "did_not_play")].copy()
    if box.duplicated(["game_id", "athlete_id"]).any():
        raise ValueError("Duplicate player-game box rows")
    if team.duplicated(["game_id", "team_id"]).any():
        raise ValueError("Duplicate team-game box rows")
    p_events, t_events, reconciliation = _reconcile_pbp(box, p_events, t_events)
    coverage["reconciliation"] = reconciliation
    box = box.merge(times, on="game_id", how="left", validate="many_to_one")
    team = team.merge(times, on="game_id", how="left", validate="many_to_one")
    if box.source_at.isna().any() or team.source_at.isna().any():
        raise ValueError("Box game absent from schedule: chronology cannot be established")
    # A state contains the whole preceding history. If a prior game's source
    # arrived unusually late, a later state must wait for that source too.
    # This also keeps externally merged postgame talent states safe.
    box = box.sort_values(["athlete_id", "tip_at", "game_id"]).reset_index(drop=True)
    team = team.sort_values(["team_id", "tip_at", "game_id"]).reset_index(drop=True)
    box["source_at"] = box.groupby("athlete_id").source_at.cummax()
    team["source_at"] = team.groupby("team_id").source_at.cummax()
    box["gp"] = box.groupby("athlete_id").cumcount() + 1
    box["started"] = _bool(box, "starter").astype(float)
    basecols = list(STATS) + ["started"]
    player = box[["game_id", "athlete_id", "team_id", "source_at", "tip_at", "gp"]].copy()
    for tag, alpha in ALPHAS.items():
        smoothed = _ew(box, "athlete_id", basecols, alpha)
        for col, short in STATS.items():
            player[f"{short}_ew{tag}"] = smoothed[col]
        if tag == "f":
            player["started_ewf"] = smoothed.started
    rates = box[["athlete_id"]].copy()
    for col, short in STATS.items():
        if short in RATE_STATS:
            rates[f"{short}_rate_ewf"] = box[col] / box.minutes
    ratecols = [c for c in rates if c != "athlete_id"]
    player[ratecols] = _ew(rates, "athlete_id", ratecols, ALPHAS["f"])

    # Use opportunities, not opponent points alone, to describe the environment.
    team["poss"] = (_number(team, "field_goals_attempted") - _number(team, "offensive_rebounds")
                    + _number(team, "total_turnovers") + .44 * _number(team, "free_throws_attempted"))
    team_stats = {
        "poss": "pace", "team_score": "pts_for", "opponent_team_score": "pts_against",
        "assists": "ast_for", "total_rebounds": "reb_for",
        "three_point_field_goals_attempted": "tpa_for", "field_goals_attempted": "fga_for",
        "free_throws_attempted": "fta_for", "offensive_rebounds": "oreb_for",
        "defensive_rebounds": "dreb_for",
    }
    for col in team_stats:
        team[col] = _number(team, col)
    t_state = team[["game_id", "team_id", "source_at", "tip_at"]].copy()
    t_ew = _ew(team, "team_id", list(team_stats), .10)
    for col, name in team_stats.items():
        t_state[f"{name}_ew"] = t_ew[col]

    # Same historical game, teammates excluding the player. No future lineup
    # membership is used; these are previous-role descriptions, not injuries.
    totals = box.groupby(["game_id", "team_id"])[
        ["field_goals_attempted", "assists", "rebounds", "minutes"]].transform("sum")
    teammate = pd.DataFrame({"athlete_id": box.athlete_id})
    for col, label in (("field_goals_attempted", "shooting"), ("assists", "assisting"),
                       ("rebounds", "rebounding")):
        teammate[f"teammate_{label}_demand"] = (totals[col] - box[col]) / (totals.minutes / 5).clip(lower=1)
        teammate[f"historical_{label}_share"] = box[col] / totals[col].where(totals[col] > 0)
    tcols = [c for c in teammate if c != "athlete_id"]
    player[[c + "_ewf" for c in tcols]] = _ew(teammate, "athlete_id", tcols, .18).to_numpy()
    player["minutes_sd_10"] = box.groupby("athlete_id").minutes.transform(
        lambda s: s.rolling(10, min_periods=3).std())
    player["minutes_change_last"] = box.minutes - player.min_ews

    if len(p_events):
        history = box[["game_id", "athlete_id", "minutes"]].merge(
            p_events, on=["game_id", "athlete_id"], how="left", validate="one_to_one")
        rich = _profiles(history)
        for kind in ("rim", "three"):
            rich[f"pbp_created_{kind}_share"] = _ratio(history, f"pbp_created_{kind}", "pbp_created_makes")
        rich["pbp_receiver_concentration"] = history.pbp_receiver_concentration
        for col in ("pbp_fga", "pbp_created_makes", "pbp_created_points", "pbp_recorded_actions",
                    "pbp_early_fouls", "pbp_fouls", "pbp_off_rebounds", "pbp_def_rebounds",
                    "pbp_sub_in", "pbp_sub_out"):
            rich[col + "_per_min"] = history[col] / history.minutes
        rich["pbp_first_q1_exit_minutes"] = history.pbp_first_q1_exit_minutes
        rich["pbp_q4_recorded_participation"] = history.pbp_q4_actions.gt(0).astype(float).where(
            history.pbp_recorded_actions.notna())
        rich["athlete_id"] = history.athlete_id
        rich_cols = [c for c in rich if c != "athlete_id"]
        player[[c + "_ewf" for c in rich_cols]] = _ew(rich, "athlete_id", rich_cols, .18).to_numpy()
        # A separate age/count is needed: missing PBP must not look like zero.
        player["pbp_games"] = history.pbp_recorded_actions.notna().groupby(history.athlete_id).cumsum()
        all_valid = pd.Series(True, index=history.index)
        for family in ("shot", "assist", "rebound"):
            valid = history[f"pbp_{family}_valid"].eq(True)
            player[f"pbp_{family}_source_at"] = box.source_at.where(valid).groupby(box.athlete_id).ffill()
            player[f"pbp_{family}_valid_games"] = valid.groupby(box.athlete_id).cumsum()
            all_valid &= valid
        player["pbp_source_at"] = box.source_at.where(all_valid).groupby(box.athlete_id).ffill()
        t_history = team[["game_id", "team_id"]].merge(t_events, on=["game_id", "team_id"], how="left")
        t_rich = _profiles(t_history)
        t_rich["pbp_fg_misses"] = t_history.pbp_fga - t_history.pbp_fgm
        t_rich["pbp_three_misses"] = t_history.pbp_three_fga - t_history.pbp_three_fgm
        t_rich["pbp_rim_misses"] = t_history.pbp_rim_fga - t_history.pbp_rim_fgm
        t_rich["pbp_off_rebounds"] = t_history.pbp_off_rebounds
        t_rich["pbp_def_rebounds"] = t_history.pbp_def_rebounds
        # Obtain the opposing team's actual historical shot profile by ID.
        # There are exactly two team rows per normal completed game.
        t_rich = pd.concat([t_history[["game_id", "team_id"]], t_rich], axis=1)
        paired = t_rich.merge(t_rich, on="game_id", suffixes=("", "_against"))
        paired = paired[paired.team_id.ne(paired.team_id_against)]
        if paired.duplicated(["game_id", "team_id"]).any():
            raise ValueError("More than two teams in a historical game")
        against_cols = [c for c in paired if c.endswith("_against") and c != "team_id_against"]
        allow = paired[["game_id", "team_id"] + against_cols].rename(columns={
            c: "pbp_allowed_" + c.removeprefix("pbp_").removesuffix("_against") for c in against_cols})
        t_rich = t_rich.merge(allow, on=["game_id", "team_id"], how="left")
        rich_cols = [c for c in t_rich if c not in ("game_id", "team_id")]
        t_state[[c + "_ew" for c in rich_cols]] = _ew(t_rich, "team_id", rich_cols, .10).to_numpy()
    else:
        player["pbp_games"] = 0
        player["pbp_source_at"] = pd.NaT

    # Every completed team-game contributes once. Query batches/offer counts
    # and later target outcomes cannot change the normalizing league mean.
    league = team.groupby("source_at", as_index=False).agg(
        poss_sum=("poss", "sum"), poss_n=("poss", "count"),
        allowed_sum=("opponent_team_score", "sum"), allowed_n=("opponent_team_score", "count"))
    league = league.sort_values("source_at")
    recent = league.set_index("source_at").rolling("365D").sum()
    league["lg_pace_asof"] = (recent.poss_sum / recent.poss_n.replace(0, np.nan)).to_numpy()
    league["lg_pts_against_asof"] = (recent.allowed_sum / recent.allowed_n.replace(0, np.nan)).to_numpy()
    league = league[["source_at", "lg_pace_asof", "lg_pts_against_asof"]]
    coverage.update({"box_player_games": len(box), "team_games": len(team),
                     "excluded_allstar_games": len(excluded),
                     "availability_rule": f"max(tip + {availability_hours}h, last timestamped play + 1h); date-only + 2d",
                     "forecast_features": "postgame states, strictly before forecast_at"})
    return StateTables(player, t_state, league, coverage)


def _join_asof(queries, snapshots, by, source_name, prefix=""):
    if not len(snapshots):
        out = queries.copy()
        out[source_name] = pd.NaT
        return out
    snapshots = snapshots.copy()
    rename = {"source_at": source_name}
    rename.update({c: prefix + c for c in snapshots if c not in (by, "source_at")})
    snapshots = snapshots.rename(columns=rename)
    return pd.merge_asof(
        queries.sort_values("forecast_at", kind="stable"),
        snapshots.sort_values(source_name, kind="stable"),
        left_on="forecast_at", right_on=source_name, by=by,
        direction="backward", allow_exact_matches=False)


def query_features(states, queries):
    """Look up all states strictly before each forecast; preserve row order.

    Required query fields: athlete_id, team_id, opponent_team_id, forecast_at.
    tip_at/home/game_id are carried through when supplied. This function never
    uses query outcomes, line values, final rosters, or the other query rows.
    """
    q = _normalise(queries)
    required = {"athlete_id", "team_id", "opponent_team_id", "forecast_at"}
    if missing := required.difference(q):
        raise ValueError(f"Missing query fields: {sorted(missing)}")
    original_index = q.index
    q["_query_order"] = np.arange(len(q))
    q["forecast_at"] = _datetime(q.forecast_at)
    if q.forecast_at.isna().any():
        raise ValueError("forecast_at must be a valid UTC-convertible timestamp")
    if "tip_at" in q:
        q["tip_at"] = _datetime(q.tip_at)
        if (q.forecast_at >= q.tip_at).any():
            raise ValueError("Pregame forecasts must precede tip_at")
    p = states.player.rename(columns={"game_id": "player_source_game_id", "team_id": "player_prior_team_id",
                                      "tip_at": "player_last_tip_at"})
    q = _join_asof(q, p, "athlete_id", "player_source_at")
    team = states.team.drop(columns=["game_id", "tip_at"], errors="ignore")
    q = _join_asof(q, team, "team_id", "team_source_at", "tm_")
    opp = team.rename(columns={"team_id": "opponent_team_id"})
    q = _join_asof(q, opp, "opponent_team_id", "opponent_source_at", "opp_")
    q = _join_asof(q, states.league, None, "league_source_at")
    sourcecols = ["player_source_at", "team_source_at", "opponent_source_at", "league_source_at"]
    q["max_source_at"] = q[sourcecols].max(axis=1)
    if (q.max_source_at >= q.forecast_at).any():
        raise AssertionError("A future source entered an as-of forecast")
    q["rest"] = ((q.get("tip_at", q.forecast_at) - q.player_last_tip_at).dt.total_seconds() / 86400).clip(upper=30)
    q["player_state_age_days"] = (q.forecast_at - q.player_source_at).dt.total_seconds() / 86400
    q["pbp_state_age_days"] = (q.forecast_at - _datetime(q.pbp_source_at)).dt.total_seconds() / 86400
    for family in ("shot", "assist", "rebound"):
        col = f"pbp_{family}_source_at"
        if col in q:
            q[f"pbp_{family}_age_days"] = (q.forecast_at - _datetime(q[col])).dt.total_seconds() / 86400
    q["same_team_as_last_game"] = q.team_id.eq(q.player_prior_team_id).astype(float)
    q = q.sort_values("_query_order").drop(columns="_query_order")
    q.index = original_index
    return q


def feature_columns(states):
    """Explicit model-input groups; excludes IDs, source times and outcomes."""
    player = [c for c in states.player if c.endswith(("_ewf", "_ews"))]
    basic = [c for c in player if not c.startswith(("pbp_", "teammate_", "historical_"))]
    basic += ["gp", "rest", "home"]
    basic += [prefix + c for prefix in ("tm_", "opp_") for c in states.team
              if c.endswith("_ew") and not c.startswith("pbp_")]
    basic += ["lg_pace_asof", "lg_pts_against_asof"]
    rich = [c for c in player if c not in basic]
    rich += [prefix + c for prefix in ("tm_", "opp_") for c in states.team if c.startswith("pbp_")]
    rich += ["minutes_sd_10", "minutes_change_last", "same_team_as_last_game",
             "pbp_games", "pbp_state_age_days", "player_state_age_days"]
    if "pbp_shot_source_at" in states.player:
        rich += [f"pbp_{family}_{suffix}" for family in ("shot", "assist", "rebound")
                 for suffix in ("age_days", "valid_games")]
    return {"basic": basic, "rich": rich}
