"""Participation-aware historical boxes for a separately registered recipe.

Displayed zero minutes do not establish DNP or exact zero elapsed exposure.
This adapter never estimates seconds. The frozen engine adapter is untouched.
Every input player row is retained in ``data.quality['wnba_v2_source_audit']``;
identity conflicts cannot become state updates. Development stops at 2024.
A separate entry point permits through-2025 state initialization only for a
candidate already frozen in 2026 or later; it never fits parameters.
"""
from collections import Counter
import math

import numpy as np
import pandas as pd

from research.clocks import parse, schedule_tip
from research.engine.sources import COUNTS, SOURCE_COMMIT, _id, _normalise, game_clocks
from research.engine.store import Observation, ObservationKind, TimeBasis, payload_digest

SOURCE_RECIPE = "wnba-participation-source-v2"


def _raw(value):
    """Keep JSON-safe raw values; missing values remain missing."""
    if value is None or pd.isna(value):
        return None
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, (str, bool, int, float)):
        return value
    return str(value)


def _number(value):
    if value is None or isinstance(value, (bool, np.bool_)):
        return None
    try:
        number = float(value)
    except (ValueError, TypeError):
        return None
    return number if math.isfinite(number) else None


def _flag(value):
    # Text and numeric flags need an explicit provider contract, not truthiness.
    return bool(value) if isinstance(value, (bool, np.bool_)) else None


def _valid_id(value):
    return isinstance(value, str) and bool(value) and value == value.strip()


def normalise_player_row(row):
    """Classify participation independently of scoring counts.

    A played record with unknown exposure remains a played record. Missing
    counts prevent rate updates without discarding valid minutes. Invalid
    negative/noninteger values and contradictory explicit DNP records
    are retained as conflicts and must not enter state.
    """
    raw_minutes = _raw(row.get("minutes"))
    minutes = _number(row.get("minutes"))
    valid_minutes = minutes is not None and 0 <= minutes <= 60
    dnp = _flag(row.get("did_not_play"))
    raw_counts = {market: _raw(row.get(column)) for market, column in COUNTS.items()}
    counts = {}
    issues = []
    invalid_values = False
    for market, column in COUNTS.items():
        value = _number(row.get(column))
        if value is None or value < 0 or not value.is_integer():
            counts[market] = None
            issues.append("missing_or_invalid_count:" + market)
            invalid_values |= raw_counts[market] is not None
        else:
            counts[market] = value
    if raw_minutes is not None and not valid_minutes:
        issues.append("invalid_reported_minutes")
        invalid_values = True
    if dnp is None:
        issues.append("missing_or_invalid_dnp_flag")

    conflict = bool(dnp is True and (
        (minutes is not None and minutes > 0)
        or any(value is not None and value > 0 for value in counts.values())))
    if conflict:
        issues.append("explicit_dnp_conflicts_with_minutes_or_counts")
    if dnp is True:
        participation, basis = "dnp", "explicit_dnp_flag"
    elif dnp is False:
        participation, basis = "played", "explicit_played_flag"
    elif valid_minutes and minutes > 0:
        participation, basis = "played", "positive_reported_minutes"
    else:
        participation, basis = "unknown", "no_participation_evidence"

    positive = participation == "played" and valid_minutes and minutes > 0
    exposure_status = "observed_positive" if positive else (
        "not_applicable" if participation == "dnp" else "unknown")
    minute_eligible = bool(positive and not conflict and not invalid_values)
    rate_eligible = bool(minute_eligible and all(v is not None for v in counts.values()))
    payload = {
        "source_recipe": SOURCE_RECIPE,
        "participation": participation,
        "participation_basis": basis,
        "did_not_play": True if participation == "dnp" else False if participation == "played" else None,
        "exposure_status": exposure_status,
        "minute_measurement_eligible": minute_eligible,
        "rate_measurement_eligible": rate_eligible,
        "minutes": float(minutes) if positive else 0. if participation == "dnp" and not conflict else None,
        "reported_minutes": raw_minutes,
        "counts": counts,
        "source_fields": {
            "minutes": raw_minutes,
            "did_not_play": _raw(row.get("did_not_play")),
            "counts": raw_counts,
        },
    }
    return {"payload": payload, "issues": sorted(issues), "conflict": conflict,
            "invalid_values": bool(invalid_values)}


def repair_cohort(frame):
    """Freeze all zero/missing-minute explicit played identities without counts.

    Unknown flags remain in the complete audit, outside this repair cohort.
    Source row ordinal distinguishes repeated identities, whose conflicts are
    subsequently retained in the full audit.
    """
    return _repair_cohort(frame, 2024)


def _repair_cohort(frame, max_season):
    needed = ("season", "game_id", "athlete_id", "team_id", "opponent_team_id", "minutes", "did_not_play")
    missing = set(needed) - set(frame.columns)
    if missing:
        raise ValueError("Cohort requires source fields: " + ", ".join(sorted(missing)))
    result = []
    for ordinal, row in enumerate(frame.loc[:, needed].to_dict("records")):
        season = _number(row["season"])
        if season is None or not season.is_integer() or not 2003 <= season <= max_season:
            raise ValueError(f"Source season must be within 2003..{max_season}")
        minutes = _number(row["minutes"])
        missing_minutes = _raw(row["minutes"]) is None
        if _flag(row["did_not_play"]) is False and (minutes == 0 or missing_minutes):
            result.append({
                "source_row": ordinal, "season": int(season),
                "game_id": _id(row["game_id"]), "player_id": _id(row["athlete_id"]),
                "team_id": _id(row["team_id"]), "opponent_id": _id(row["opponent_team_id"]),
                "reported_minutes": _raw(row["minutes"]),
                "reported_did_not_play": _raw(row["did_not_play"]),
            })
    return result


def _scope(data, max_season):
    for name in ("player_box", "team_box", "pbp", "schedule"):
        frame = getattr(data, name)
        if frame.empty:
            continue
        if "season" not in frame:
            raise ValueError(name + " requires explicit source seasons")
        seasons = pd.to_numeric(frame.season, errors="raise")
        if not (seasons.between(2003, max_season) & seasons.eq(np.floor(seasons))).all():
            raise ValueError(f"Source season must be within 2003..{max_season}")


def observations(data, availability_hours=8):
    """Build audited historical records using the unchanged clock assumptions."""
    return _observations(data, availability_hours, max_season=2024)


def inference_seed_observations(data, *, frozen_at):
    """Normalize through-2025 history for an already frozen future candidate.

    ``frozen_at`` is an explicit, timezone-aware candidate freeze time in 2026
    or later. This timestamp check does not prove an external registration or
    authorize fitting: the caller must first verify the frozen candidate and
    must use the returned records only to initialize future inference state.
    The source availability assumption remains eight hours, extended by known
    completion/play clocks; every resulting clock must precede the freeze.
    """
    frozen = parse("forecast.as_of", frozen_at)
    if frozen.year < 2026:
        raise ValueError("Inference seed requires a candidate frozen in 2026 or later")
    return _observations(data, 8, max_season=2025, frozen_at=frozen)


def _observations(data, availability_hours, *, max_season, frozen_at=None):
    _scope(data, max_season)
    if data.schedule.empty or data.schedule.game_id.map(_id).duplicated().any():
        raise ValueError("Unique historical schedule identities are required")
    # This selection only reads the registered identity/minutes/flag columns.
    cohort = _repair_cohort(data.player_box, max_season)
    clocks = game_clocks(data, availability_hours)
    if any(tip.year > max_season for tip, _, _ in clocks.values()):
        raise ValueError(f"Source event clocks must stop before {max_season + 1}")
    if frozen_at is not None and any(tip >= frozen_at or available >= frozen_at
                                     for tip, available, _ in clocks.values()):
        raise ValueError("Inference seed source clocks must strictly precede candidate freeze")
    if any(tip.year != season for tip, _, season in clocks.values()):
        raise ValueError("Historical schedule season disagrees with event clock")
    retrieved = parse("source.retrieved_at", data.manifest["recorded_at_utc"])
    if any(retrieved < tip for tip, _, _ in clocks.values()):
        raise ValueError("Historical source retrieval precedes a recorded event")
    schedule = {_id(row["game_id"]): row for row in data.schedule.to_dict("records")}
    players = _normalise(data.player_box)
    teams_raw = _normalise(data.team_box)
    team_conflicts = teams_raw.duplicated(["game_id", "team_id"], keep=False)
    player_conflicts = players.duplicated(["game_id", "athlete_id"], keep=False)
    duration_fallback = players.assign(minutes=pd.to_numeric(players.minutes, errors="coerce")).groupby(
        ["game_id", "team_id"]).minutes.sum()
    roster_counts = players.groupby(["game_id", "team_id"]).athlete_id.nunique()
    result, teams, team_audit, player_audit = [], {}, [], []

    def make(entity, gid, payload, kind):
        tip, available, season = clocks[gid]
        return Observation(
            source_id="wehoop:" + SOURCE_COMMIT, record_id=f"{kind}:{gid}:{entity}",
            entity_id=entity, event_id=gid, season=season,
            kind=ObservationKind.HISTORICAL_OUTCOME, effective_at=tip, payload=payload,
            retrieved_at=retrieved, time_basis=TimeBasis.ASSUMED, assumed_available_at=available,
            time_note="Corrected historical archive; availability assumed as max(tip delay, last play+1h, reliable completion+1h).",
        )

    for ordinal, row in enumerate(teams_raw.to_dict("records")):
        gid, team, opponent = row["game_id"], row["team_id"], row["opponent_team_id"]
        audit = {"source_row": ordinal, "game_id": gid, "team_id": team, "issues": []}
        if team_conflicts.iloc[ordinal]:
            audit["issues"].append("conflicting_team_identity")
        if gid not in clocks:
            audit["issues"].append("missing_or_noncompetitive_schedule")
        fields = [_number(row.get(c)) for c in (
            "field_goals_attempted", "free_throws_attempted", "offensive_rebounds", "total_turnovers")]
        points = _number(row.get("team_score"))
        if (not all(_valid_id(value) for value in (gid, team, opponent)) or team == opponent
                or any(v is None or v < 0 or not v.is_integer() for v in fields)
                or points is None or points < 0 or not points.is_integer()):
            audit["issues"].append("invalid_team_identity_or_values")
        if gid in clocks and int(row["season"]) != clocks[gid][2]:
            audit["issues"].append("team_schedule_season_conflict")
        duration, possessions = None, None
        if not audit["issues"]:
            scheduled = {_id(schedule[gid].get("home_id")), _id(schedule[gid].get("away_id"))}
            if not all(_valid_id(value) for value in scheduled) or {team, opponent} != scheduled:
                audit["issues"].append("team_schedule_conflict")
            period = _number(schedule[gid].get("status_period"))
            if period is not None and (not period.is_integer() or period < 1):
                audit["issues"].append("invalid_schedule_period")
            elif period is None or period < 4:
                total_minutes = float(duration_fallback.get((gid, team), 0))
                if math.isfinite(total_minutes):
                    duration = float(5 * round(total_minutes / 25))
            else:
                duration = float(40 + 5 * (int(period) - 4))
            possessions = fields[0] + .44 * fields[1] - fields[2] + fields[3]
            if duration is None or duration < 40 or possessions <= 0:
                audit["issues"].append("invalid_team_exposure")
        audit["status"] = "omitted" if audit["issues"] else "included"
        team_audit.append(audit)
        if audit["issues"]:
            continue
        payload = {"record_type": "team_box", "team_id": team, "opponent_id": opponent,
                   "possessions_estimate": possessions, "possession_basis": "box_estimate",
                   "duration_minutes": duration, "points": points,
                   "roster_count": int(roster_counts.get((gid, team), 0)),
                   "roster_count_basis": "players listed in this completed game's box"}
        teams[(gid, team)] = payload
        result.append(make(team, gid, payload, "team_box"))

    for ordinal, row in enumerate(players.to_dict("records")):
        gid, player, team, opponent = row["game_id"], row["athlete_id"], row["team_id"], row["opponent_team_id"]
        normalized = normalise_player_row(row)
        payload = normalized["payload"]
        omissions = []
        if player_conflicts.iloc[ordinal]:
            omissions.append("conflicting_player_identity")
        if not all(_valid_id(value) for value in (gid, player, team, opponent)) or team == opponent:
            omissions.append("invalid_player_identity")
        if gid in clocks and int(row["season"]) != clocks[gid][2]:
            omissions.append("player_schedule_season_conflict")
        if gid not in clocks:
            omissions.append("missing_or_noncompetitive_schedule")
        tp = teams.get((gid, team))
        if tp is None:
            omissions.append("missing_or_invalid_team")
        elif opponent != tp["opponent_id"]:
            omissions.append("player_team_opponent_conflict")
        if normalized["conflict"]:
            omissions.append("explicit_dnp_conflict")
        if normalized["invalid_values"]:
            omissions.append("invalid_player_measurements")
        audit = {"source_row": ordinal, "season": int(row["season"]), "game_id": gid,
                 "player_id": player, "team_id": team, "opponent_id": opponent,
                 "status": "omitted" if omissions else "included",
                 "omissions": sorted(omissions), "issues": normalized["issues"],
                 "participation": payload["participation"], "exposure_status": payload["exposure_status"],
                 "minute_measurement_eligible": bool(payload["minute_measurement_eligible"] and not omissions),
                 "rate_measurement_eligible": bool(payload["rate_measurement_eligible"] and not omissions),
                 "source_fields": payload["source_fields"]}
        player_audit.append(audit)
        if omissions:
            continue
        scheduled = {"game_id": gid,
            "scheduled_team_ids": [_id(schedule[gid].get("home_id")), _id(schedule[gid].get("away_id"))],
            "tip_at": schedule_tip(schedule[gid]["date"], schedule[gid]["game_date_time"]).isoformat()}
        position = _raw(row.get("athlete_position_abbreviation"))
        payload.update({"record_type": "player_box", "team_id": team, "opponent_id": opponent,
            "starter": _flag(row.get("starter")), "position": None if position is None else str(position),
            "possessions_estimate": tp["possessions_estimate"], "duration_minutes": tp["duration_minutes"],
            "possession_basis": "box_estimate", "team_source_payload_hash": payload_digest(tp),
            "scheduled_team_ids": scheduled["scheduled_team_ids"],
            "schedule_source_payload_hash": payload_digest(scheduled)})
        result.append(make(player, gid, payload, "player_box"))

    data.quality["wnba_v2_source_audit"] = {
        "schema": "wnba-v2-source-audit-v1", "source_recipe": SOURCE_RECIPE,
        "repair_cohort": cohort, "repair_cohort_hash": payload_digest({"rows": cohort}),
        "player_rows": player_audit, "team_rows": team_audit,
        "summary": {"raw_player_rows": len(player_audit), "raw_team_rows": len(team_audit),
            "included_player_rows": sum(row["status"] == "included" for row in player_audit),
            "minute_measurement_eligible_rows": sum(row["minute_measurement_eligible"] for row in player_audit),
            "rate_measurement_eligible_rows": sum(row["rate_measurement_eligible"] for row in player_audit),
            "participation": dict(Counter(row["participation"] for row in player_audit)),
            "omission_reasons": dict(Counter(reason for row in player_audit for reason in row["omissions"]))},
    }
    if frozen_at is not None:
        data.quality["wnba_v2_source_audit"].update({
            "purpose": "frozen_candidate_inference_seed", "through_season": 2025,
            "candidate_frozen_at": frozen_at.isoformat(),
        })
    return sorted(result, key=lambda item: (item.available_at, item.event_id, item.record_id))
