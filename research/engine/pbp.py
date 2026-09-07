"""Strict descriptions of *completed historical* basketball games.

No upcoming-game roster is accepted here. The caller must put the returned
records behind the point-in-time store's availability boundary. Box starters
initialize only the historical game's first five; missing substitutions are
never repaired from a final roster. Failed families contain ``None``, not zero.

Possessions use an explicit event-end convention: a made basket (and-one free
throw included), ordinary final made free throw, turnover, defensive rebound,
changed jump-ball control, or an unfinished period ends an offensive possession.
Offensive rebounds and retained-possession technical/flagrant/clear-path free
throws do not. Player exposure credits the five at that possession's endpoint;
it is not tracking-derived touch time or a claim about publication timestamps.

The API uses plain dictionaries/lists and the standard library. DataFrames are
accepted by duck typing for the source adapter; pandas is not a dependency.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import math
import re


def _rows(value):
    return value.to_dict("records") if hasattr(value, "to_dict") else list(value)


def _id(value):
    if value is None or str(value).lower() in ("nan", "none", "<na>", ""):
        return None
    text = str(value)
    return text[:-2] if text.endswith(".0") else text


def _number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _flag(value):
    return value is True or str(value).lower() in ("true", "1", "1.0")


def _clock(value, period):
    match = re.fullmatch(r"(\d+):(\d+(?:\.\d+)?)", str(value))
    if not match:
        raise ValueError("missing_or_invalid_clock")
    minute, second = int(match[1]), float(match[2])
    remaining = 60 * minute + second
    duration = 600 if period <= 4 else 300
    if not 0 <= second < 60 or not 0 <= remaining <= duration:
        raise ValueError("clock_out_of_range")
    start = (period - 1) * 600 if period <= 4 else 2400 + (period - 5) * 300
    return start + duration - remaining


def _classify(row):
    kind = str(row.get("type_text", "")).lower()
    text = str(row.get("text", "")).lower()
    free = "free throw" in kind or "free throw" in text
    attempted = _number(row.get("points_attempted"))
    shot = not free and (_flag(row.get("shooting_play")) or attempted in (2, 3)
                         or bool(re.search(r"\b(?:makes|made|misses|missed)\b", text)))
    # A nullified shot is not an observed attempt.
    shot = shot and "no shot" not in kind
    made = _flag(row.get("scoring_play")) or bool(re.search(r"\b(?:makes|made)\b", text))
    score_value = _number(row.get("score_value"))
    three = (score_value == 3 if shot and made and score_value in (2, 3) else
             attempted == 3 or bool(re.search(r"three[ -]point|3[ -]point|3pt", text)))
    retained = free and any(word in kind + " " + text
                           for word in ("technical", "flagrant", "clear path"))
    sequence = re.search(r"(\d+)\s+of\s+(\d+)", kind + " " + text)
    final_free = free and not retained and sequence is not None and sequence[1] == sequence[2]
    turnover = ("turnover" in kind or "traveling" in kind or "double dribble" in kind)
    turnover = turnover and "no turnover" not in kind
    foul = "foul" in kind and not any(x in kind for x in ("technical", "turnover", "double"))
    return dict(kind=kind, text=text, free=free, shot=shot, made=made, three=three,
                retained=retained, final_free=final_free,
                and_one=free and not retained and sequence is not None and (sequence[1], sequence[2]) == ("1", "1"),
                turnover=turnover, foul=foul, sub="substitution" in kind or "enters the game" in text,
                rebound="rebound" in kind, defensive="defensive rebound" in kind,
                offensive="offensive rebound" in kind,
                assist=shot and made and bool(re.search(r"assists|assisted by", text)),
                jump="jumpball" in kind or "jump ball" in kind,
                period_end="end period" in kind or "end of" in kind and "quarter" in kind)


def select_pilot_games(schedule, season, n=20):
    """The registered sample; select identities before examining PBP values."""
    ids = sorted({_id(row.get("game_id")) for row in _rows(schedule)
                  if _number(row.get("season", season)) == season} - {None})
    ids.sort(key=lambda gid: hashlib.sha256(f"structural-pilot-v1:{gid}".encode()).hexdigest())
    return ids[:n]


def parse_game(pbp, player_box, *, game_id=None):
    """Return historical player descriptions, team possessions and family QC.

    ``players`` is keyed by athlete ID; ``parse_games`` adds the game ID key.
    ``on_off`` stores raw shot/assist/rebound share numerators and denominators
    for each teammate and court state. Shrinkage belongs to the forecast model.
    Partial valid windows remain in ``stints`` for audit, but failed complete
    lineup reconstruction exposes no usable full-game minutes or on/off counts.
    """
    raw, boxes = _rows(pbp), _rows(player_box)
    gids = {_id(r.get("game_id")) for r in raw + boxes} - {None}
    gid = _id(game_id) or (next(iter(gids)) if len(gids) == 1 else None)
    if gid is None or any(g != gid for g in gids):
        raise ValueError("parse_game requires exactly one historical game")
    if any((_number(r.get("season")) or 0) >= 2026 for r in raw + boxes):
        raise ValueError("Protected 2026+ games are not development sources")
    errors = {name: [] for name in ("identity", "ordering", "possessions", "stints", "foul_margin")}

    def error(family, reason):
        if reason not in errors[family]:
            errors[family].append(reason)

    by_player = {}
    roster = defaultdict(set)
    lineups = defaultdict(set)
    for row in boxes:
        athlete, team = _id(row.get("athlete_id")), _id(row.get("team_id"))
        if not athlete or not team or athlete in by_player:
            error("identity", "missing_or_duplicate_box_identity")
            continue
        row = dict(row)
        # Explicit historical DNP records carry null statistics in wehoop.
        # This does not convert an absent/missing roster record into a DNP.
        if _flag(row.get("did_not_play")):
            for field in ("minutes", "points", "rebounds", "assists", "three_point_field_goals_made"):
                if _number(row.get(field)) is None:
                    row[field] = 0.0
        by_player[athlete] = row
        roster[team].add(athlete)
        if _flag(row.get("starter")):
            lineups[team].add(athlete)
        minutes = _number(row.get("minutes"))
        if minutes is None or not 0 <= minutes <= 60:
            error("identity", "invalid_box_minutes")
        for field in ("points", "rebounds", "assists", "three_point_field_goals_made"):
            value = _number(row.get(field))
            if value is None or value < 0 or not value.is_integer():
                error("identity", "invalid_box_counts")
    teams = sorted(roster)
    if len(teams) != 2:
        error("identity", "requires_two_box_teams")
    for team in teams:
        if len(lineups[team]) != 5:
            error("stints", "requires_exactly_five_initial_starters")

    events = []
    sequences, event_ids = set(), set()
    for row in raw:
        # Source sequence_number includes later insertions/corrections. The
        # provider's game_play_number is the chronological play order.
        seq = _number(row.get("game_play_number", row.get("sequence_number")))
        eid = _id(row.get("id"))
        if seq is None or seq in sequences or eid is not None and eid in event_ids:
            error("ordering", "missing_or_duplicate_event_order")
            continue
        sequences.add(seq)
        if eid is not None:
            event_ids.add(eid)
        period = _number(row.get("period_number", row.get("qtr")))
        try:
            if period is None or not period.is_integer() or not 1 <= period <= 8:
                raise ValueError("invalid_period")
            elapsed = _clock(row.get("clock_display_value"), int(period))
        except ValueError as exc:
            error("ordering", str(exc))
            continue
        events.append(dict(row=row, seq=seq, period=int(period), elapsed=elapsed,
                           team=_id(row.get("team_id")), actor=_id(row.get("athlete_id_1")),
                           actor2=_id(row.get("athlete_id_2")), **_classify(row)))
    events.sort(key=lambda row: row["seq"])
    actor_role_swaps = 0
    for event in events:
        # Older ESPN events put the stealer/blocker first, unlike recent
        # files. A unique actor on the explicit event team resolves that
        # historical role without player-name guesses.
        if (event["shot"] or event["turnover"]) and event["team"] in roster:
            members = roster[event["team"]]
            if event["actor"] not in members and event["actor2"] in members:
                event["actor"], event["actor2"] = event["actor2"], event["actor"]
                actor_role_swaps += 1
    if not events:
        error("ordering", "missing_pbp_events")
    if any(b["elapsed"] < a["elapsed"] for a, b in zip(events, events[1:])):
        error("ordering", "clock_reverses_in_source_order")
    periods = {e["period"] for e in events}
    if periods and periods != set(range(1, max(periods) + 1)):
        error("ordering", "missing_period")
    if events and (events[-1]["period"] < 4 or events[-1]["elapsed"] !=
                   2400 + max(0, events[-1]["period"] - 4) * 300):
        error("ordering", "incomplete_game_clock")

    # Look ahead only within this completed historical game's same-clock dead
    # ball sequence. A made basket plus its one-shot FT is one possession.
    for index, event in enumerate(events):
        event["followed_by_and_one"] = False
        if event["shot"] and event["made"]:
            for later in events[index + 1:]:
                if later["elapsed"] != event["elapsed"]:
                    break
                if later["and_one"] and later["actor"] == event["actor"]:
                    event["followed_by_and_one"] = True
                    break

    minutes = defaultdict(float)
    blowout_minutes = defaultdict(float)
    late_minutes = defaultdict(float)
    fouls = defaultdict(int)
    early_fouls = defaultdict(int)
    first_exit = {}
    starts = {a: 0.0 for lineup in lineups.values() for a in lineup}
    lengths = defaultdict(list)
    endpoints = defaultdict(int)
    player_possessions = defaultdict(int)
    production = defaultdict(lambda: defaultdict(int))
    on_off = defaultdict(lambda: defaultdict(lambda: {
        "on": defaultdict(int), "off": defaultdict(int)}))
    stints, possessions = [], []
    pending = None
    previous_elapsed = 0.0
    previous_scores = (0, 0)
    previous_reported_scores = (0, 0)
    scoring_totals = {team: 0 for team in teams}
    stale_score_rows = 0
    previous_period = 1
    lineup_ok = not errors["identity"] and not errors["stints"] and not errors["ordering"]

    def shares(team, actor, metric, amount=1):
        if not lineup_ok or team not in roster:
            return
        for player in lineups[team]:
            for teammate in roster[team] - {player}:
                state = "on" if teammate in lineups[team] else "off"
                record = on_off[player][teammate][state]
                record[f"team_{metric}"] += amount
                if player == actor:
                    record[f"player_{metric}"] += amount

    def finish(team, event, reason):
        nonlocal pending
        if team not in roster:
            error("possessions", "unattributed_possession_endpoint")
            pending = None
            return
        endpoints[team] += 1
        possessions.append({"team_id": team, "sequence_number": event["seq"],
                            "elapsed_seconds": event["elapsed"], "reason": reason})
        if lineup_ok:
            for player in lineups[team]:
                player_possessions[player] += 1
            shares(team, None, "possessions")
        pending = None

    for event in events:
        team, actor, actor2 = event["team"], event["actor"], event["actor2"]
        elapsed = event["elapsed"]
        delta = max(0, elapsed - previous_elapsed)
        if lineup_ok and delta:
            snapshot = {t: sorted(lineups[t]) for t in teams}
            if stints and stints[-1]["lineups"] == snapshot and stints[-1]["end"] == previous_elapsed:
                stints[-1]["end"] = elapsed
            else:
                stints.append({"start": previous_elapsed, "end": elapsed, "lineups": snapshot})
            # Integrate clock intervals at the score BEFORE the next event.
            late_start = max(previous_elapsed, 1800)
            late_delta = max(0, elapsed - late_start)
            for lineup in lineups.values():
                for player in lineup:
                    minutes[player] += delta / 60
                    late_minutes[player] += late_delta / 60
                    if abs(previous_scores[0] - previous_scores[1]) >= 15:
                        blowout_minutes[player] += late_delta / 60
        if event["period"] != previous_period and pending is not None:
            error("possessions", "unterminated_possession_at_period_change")
            pending = None
        scores = (_number(event["row"].get("home_score")), _number(event["row"].get("away_score")))
        if any(s is None or s < 0 or not s.is_integer() for s in scores):
            error("foul_margin", "missing_or_invalid_score")
        else:
            if any(s < old for s, old in zip(scores, previous_reported_scores)):
                stale_score_rows += 1
            previous_reported_scores = scores

        if event["sub"]:
            valid = (team in roster and actor in roster[team] and actor2 in roster[team]
                     and actor != actor2 and actor2 in lineups[team] and actor not in lineups[team])
            if not valid:
                error("stints", "incoherent_or_missing_substitution")
                lineup_ok = False
            elif lineup_ok:
                lengths[actor2].append((elapsed - starts.pop(actor2)) / 60)
                if event["period"] == 1:
                    first_exit.setdefault(actor2, elapsed / 60)
                lineups[team].remove(actor2)
                lineups[team].add(actor)
                starts[actor] = elapsed
        elif event["shot"] or event["free"] or event["turnover"] or event["rebound"] or event["foul"]:
            if team not in roster:
                error("possessions", "action_missing_team")
            if actor is not None and actor not in roster.get(team, set()):
                error("identity", "event_actor_not_on_box_team")
            if lineup_ok and actor is not None and actor not in lineups[team] and not event["retained"]:
                error("stints", "action_actor_not_on_court")
                lineup_ok = False

        if event["foul"] and actor:
            fouls[actor] += 1
            if event["period"] <= 2:
                early_fouls[actor] += 1
        if event["shot"]:
            pending = team
            production[actor]["shots"] += 1
            shares(team, actor, "shots")
            if event["made"]:
                production[actor]["points"] += 3 if event["three"] else 2
                production[actor]["threes"] += int(event["three"])
                if event["assist"]:
                    if actor2 not in roster.get(team, set()):
                        error("identity", "assister_identity_missing")
                    else:
                        production[actor2]["assists"] += 1
                        shares(team, actor2, "assists")
                if not event["followed_by_and_one"]:
                    finish(team, event, "made_field_goal")
        elif event["free"]:
            if not event["retained"]:
                pending = team
            if event["made"]:
                production[actor]["points"] += 1
            if event["final_free"] and event["made"]:
                finish(team, event, "final_free_throw")
            elif not event["retained"] and not re.search(r"\d+\s+of\s+\d+", event["kind"] + event["text"]):
                error("possessions", "free_throw_sequence_unknown")
        elif event["turnover"]:
            finish(team, event, "turnover")
        elif event["rebound"]:
            if actor:
                production[actor]["rebounds"] += 1
                shares(team, actor, "rebounds")
            if event["defensive"]:
                offense = next((t for t in teams if t != team), None)
                finish(offense, event, "defensive_rebound")
            elif event["offensive"]:
                pending = team
            else:
                error("possessions", "rebound_control_unknown")
        elif event["jump"] and team in roster:
            if pending is not None and pending != team:
                finish(pending, event, "jump_ball_control_change")
            pending = team
        if event["period_end"] and pending is not None:
            finish(pending, event, "unfinished_period")
        # Non-scoring rows in older files can carry stale scoreboard values.
        # Rebuild from scoring events and require a final box reconciliation;
        # never interpolate a score backward from a later game result.
        if event["made"] and (event["shot"] or event["free"]) and team in scoring_totals:
            scoring_totals[team] += 1 if event["free"] else (3 if event["three"] else 2)
        previous_scores = tuple(scoring_totals[t] for t in teams)
        previous_elapsed, previous_period = elapsed, event["period"]

    if lineup_ok:
        for player, started in starts.items():
            lengths[player].append((previous_elapsed - started) / 60)
        for player, row in by_player.items():
            if abs(minutes[player] - (_number(row.get("minutes")) or 0)) > 1.1:
                error("stints", "reconstructed_minutes_disagree_with_box")
    if any(endpoints[t] <= 0 for t in teams):
        error("possessions", "missing_team_possessions")
    # A source convention or correction must be investigated rather than
    # silently presented as a validated possession reconstruction.
    if len(teams) == 2 and abs(endpoints[teams[0]] - endpoints[teams[1]]) > 5:
        error("possessions", "team_possession_imbalance")
    for team in teams:
        expected_score = sum((_number(by_player[a].get("points")) or 0) for a in roster[team])
        if scoring_totals[team] != expected_score:
            error("foul_margin", "scoring_events_disagree_with_box")
    count_mismatches = []
    for player, row in by_player.items():
        for metric, column in (("points", "points"), ("rebounds", "rebounds"),
                               ("assists", "assists"), ("threes", "three_point_field_goals_made")):
            expected = _number(row.get(column))
            if expected is not None and production[player][metric] != expected:
                count_mismatches.append({"athlete_id": player, "field": metric,
                                         "pbp": production[player][metric], "box": expected})
    valid_identity = not errors["identity"]
    valid_order = not errors["ordering"]
    valid_possessions = valid_identity and valid_order and not errors["possessions"]
    valid_stints = valid_possessions and not errors["stints"]
    valid_foul = valid_identity and valid_order and not errors["foul_margin"]
    valid_shares = valid_stints and not count_mismatches
    players = {}
    for player, box in by_player.items():
        lens = [x for x in lengths[player] if x > 0]
        players[player] = {"game_id": gid, "athlete_id": player, "team_id": _id(box.get("team_id")),
            "on_court_possessions": player_possessions[player] if valid_stints else None,
            "court_minutes": minutes[player] if valid_stints else None,
            "stint_count": len(lens) if valid_stints else None,
            "mean_stint_minutes": sum(lens) / len(lens) if valid_stints and lens else None,
            "first_q1_exit_minutes": first_exit.get(player) if valid_stints else None,
            "early_fouls": early_fouls[player] if valid_foul else None,
            "foul_trouble": int(early_fouls[player] >= 3) if valid_foul else None,
            "late_minutes": late_minutes[player] if valid_stints else None,
            "late_large_margin_minutes": blowout_minutes[player] if valid_stints and valid_foul else None,
            "on_off": {mate: {state: dict(values) for state, values in groups.items()}
                       for mate, groups in on_off[player].items()} if valid_shares else None}
    return {"game_id": gid, "players": players,
            "teams": {t: {"possessions": endpoints[t] if valid_possessions else None} for t in teams},
            "stints": stints, "possession_endpoints": possessions,
            "qc": {"game_id": gid, "events": len(events), "requested_events": len(raw),
                   "box_identity": valid_identity, "event_ordering": valid_order,
                   "possessions": valid_possessions, "stints": valid_stints,
                   "foul_margin": valid_foul, "on_off_shares": valid_shares,
                   "errors": errors, "count_mismatches": count_mismatches,
                   "valid_window_seconds": sum(s["end"] - s["start"] for s in stints),
                   "duration_seconds": previous_elapsed, "actor_role_swaps": actor_role_swaps,
                   "stale_score_rows": stale_score_rows,
                   "margin_basis": "forward_scoring_events_reconciled_to_box"}}


def parse_games(pbp, player_box, *, requested_game_ids=None):
    """Return ``{(game_id, athlete_id): features}, [game QC]`` with all requests."""
    events, boxes = defaultdict(list), defaultdict(list)
    for row in _rows(pbp):
        events[_id(row.get("game_id"))].append(row)
    for row in _rows(player_box):
        boxes[_id(row.get("game_id"))].append(row)
    ids = sorted(set(events) | set(boxes)) if requested_game_ids is None else [_id(x) for x in requested_game_ids]
    if len(set(ids)) != len(ids) or None in ids:
        raise ValueError("Requested game identities must be unique and present")
    players, quality = {}, []
    for gid in ids:
        result = parse_game(events[gid], boxes[gid], game_id=gid)
        players.update({(gid, athlete): value for athlete, value in result["players"].items()})
        quality.append(result["qc"])
    return players, quality
