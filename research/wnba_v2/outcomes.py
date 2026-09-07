"""Actual observed post-freeze ESPN boxes and append-only shadow settlements.

The JSON contract follows the existing ESPN collector, with strict identities
and flags. No historical 2026 archive is loaded. A final status observed at
receipt supplies an upper bound on completion, never an invented final whistle
clock. Invalid games fail their source gate; empty stats never mean DNP.
"""
from datetime import datetime, timedelta, timezone
from functools import wraps
import json
import math
from pathlib import Path
import re
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from zoneinfo import ZoneInfo

from research.engine.store import Observation, ObservationKind, ProtectedDataError, TimeBasis, payload_digest
from .future import FutureOnlyIndex, load_observations, write_observations
from .shadow import CAP, digest, encode, identifier, instant as _iso_instant, stamp, utcnow, write_once
from .sources import normalise_player_row

API = "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba"
ET = ZoneInfo("America/New_York")
MISSING = (None, "", "--", "-")


def instant(value, label="timestamp"):
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(label + ": timezone-aware clock required")
        return value.astimezone(timezone.utc)
    return _iso_instant(value, label)


def _id(value, label):
    # ESPN's stable IDs are decimal strings; never join by names/abbreviations.
    if not isinstance(value, (str, int)) or isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value)):
        raise ValueError(label + ": explicit ESPN numeric identity required")
    return str(value)


def _number(value, label, *, missing=False, integer=False):
    if value in MISSING and missing:
        return None
    if isinstance(value, bool):
        raise ValueError(label + ": invalid numeric value")
    try:
        result = float(value)
    except (ValueError, TypeError):
        raise ValueError(label + ": missing/invalid numeric value") from None
    if not math.isfinite(result) or result < 0 or (integer and result != int(result)):
        raise ValueError(label + ": invalid nonnegative value")
    return result


def _minutes(value):
    if isinstance(value, str) and ":" in value:
        if not re.fullmatch(r"[0-9]+:[0-5][0-9]", value):
            raise ValueError("invalid minutes clock")
        whole, seconds = map(int, value.split(":"))
        return whole + seconds / 60
    return _number(value, "minutes", missing=True)


def _split(value, label):
    if value in MISSING:
        return None, None
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+-[0-9]+", value):
        raise ValueError(label + ": expected made-attempted stat")
    made, attempted = map(int, value.split("-"))
    if made > attempted:
        raise ValueError(label + ": makes exceed attempts")
    return made, attempted


def _competition(container, label):
    comps = container.get("competitions")
    if not isinstance(comps, list) or len(comps) != 1:
        raise ValueError(label + ": exactly one competition required")
    return comps[0]


def _teams(comp):
    competitors = comp.get("competitors")
    if not isinstance(competitors, list) or len(competitors) != 2:
        raise ValueError("exactly two competitors required")
    result = {}
    for competitor in competitors:
        tid = _id(competitor.get("team", {}).get("id"), "competitor team")
        if tid in result or competitor.get("homeAway") not in ("home", "away"):
            raise ValueError("duplicate or ambiguous team identity")
        result[tid] = competitor
    if {c["homeAway"] for c in result.values()} != {"home", "away"}:
        raise ValueError("one home and one away team required")
    return result


def _completed(status):
    kind = status.get("type", {}) if isinstance(status, dict) else {}
    if (type(kind.get("completed")) is not bool or kind.get("state") not in ("pre", "in", "post")
            or not isinstance(kind.get("name"), str)):
        raise ValueError("explicit ESPN completion status missing or invalid")
    return kind["completed"] and kind["state"] == "post" and kind["name"] == "STATUS_FINAL"


def _schema_errors(function):
    @wraps(function)
    def checked(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (AttributeError, TypeError, KeyError, IndexError) as error:
            raise ValueError("invalid nested ESPN source schema: " + type(error).__name__) from error
    return checked


@_schema_errors
def scoreboard_events(raw, *, observed_at, frozen_at, expected_date=None):
    """Only completed post-freeze identities; no pre-freeze box is requested."""
    observed, frozen = instant(observed_at), instant(frozen_at)
    if observed < frozen:
        raise ValueError("source capture precedes freeze")
    data = json.loads(raw)
    events = data.get("events")
    if not isinstance(events, list):
        raise ValueError("ESPN scoreboard events list missing")
    result, seen = [], set()
    for row in events:
        gid = _id(row.get("id"), "scoreboard game")
        if gid in seen:
            raise ValueError("duplicate scoreboard game")
        seen.add(gid)
        tip = instant(row.get("date"), "scoreboard tip")
        if expected_date is not None and tip.astimezone(ET).date() != expected_date:
            raise ValueError("scoreboard event date differs from requested ET date")
        if tip < frozen or tip >= observed:
            continue
        comp = _competition(row, "scoreboard")
        if _id(comp.get("id"), "competition") != gid or instant(comp.get("date"), "competition date") != tip:
            raise ValueError("scoreboard game/competition clock mismatch")
        event_final, competition_final = _completed(row.get("status")), _completed(comp.get("status"))
        if event_final != competition_final:
            raise ValueError("scoreboard event and competition completion disagree")
        if not event_final:
            continue
        season = row.get("season", data.get("season", {})).get("year")
        if type(season) is not int or season != tip.year:
            raise ValueError("explicit scoreboard season disagrees with tip")
        result.append({"game_id": gid, "tip_at": stamp(tip), "season": season,
                       "team_ids": sorted(_teams(comp))})
    return result


@_schema_errors
def parse_summary(raw, *, event, observed_at, frozen_at, now=None):
    """Normalize one final response; return observations and settlement facts.

    Uses receipt as the honest final-status observation/completion upper bound.
    The raw body is retained by the caller, separately from normalized payloads.
    """
    observed, frozen = instant(observed_at), instant(frozen_at)
    now = utcnow() if now is None else instant(now)
    tip = instant(event["tip_at"])
    if tip < frozen:
        raise ProtectedDataError("pre-freeze 2026 history is not a live state update")
    if not frozen <= tip < observed <= now:
        raise ValueError("post-freeze source receipt clocks are invalid")
    gid = _id(event.get("game_id"), "expected game")
    season = event.get("season")
    if type(season) is not int or season != tip.year or season < 2026:
        raise ValueError("post-freeze explicit season is invalid")
    data = json.loads(raw)
    header = data.get("header", {})
    if _id(header.get("id"), "summary header") != gid:
        raise ValueError("summary game identity mismatch")
    comp = _competition(header, "summary")
    if _id(comp.get("id"), "summary competition") != gid or instant(comp.get("date"), "summary tip") != tip:
        raise ValueError("summary competition identity or tip mismatch")
    if header.get("season", {}).get("year") != season:
        raise ValueError("summary season mismatch")
    if not _completed(comp.get("status")):
        return {"status": "IN_PROGRESS", "observations": (), "players": {}, "game_id": gid}
    competitors = _teams(comp)
    expected_teams = [_id(t, "expected team") for t in event.get("team_ids", [])]
    if len(expected_teams) != 2 or sorted(competitors) != sorted(expected_teams):
        raise ValueError("summary team identities differ from scoreboard")
    period = _number(comp.get("status", {}).get("period"), "final period", integer=True)
    if not 4 <= period <= 8:
        raise ValueError("unsupported final duration")
    duration = 40. + 5. * (period - 4)
    boxes = data.get("boxscore", {})
    team_boxes = boxes.get("teams")
    player_boxes = boxes.get("players")
    if not isinstance(team_boxes, list) or len(team_boxes) != 2 or not isinstance(player_boxes, list) or len(player_boxes) != 2:
        raise ValueError("complete two-team boxscore required")
    team_payloads, player_payloads, players, reconciliation, minute_reconciliation = {}, {}, {}, {}, {}
    for box in team_boxes:
        tid = _id(box.get("team", {}).get("id"), "box team")
        if tid not in competitors or tid in team_payloads:
            raise ValueError("box team identity mismatch/duplicate")
        fields = box.get("statistics")
        if not isinstance(fields, list) or any(not isinstance(s.get("name"), str) for s in fields):
            raise ValueError("team statistics missing")
        stats = {s["name"]: s.get("displayValue") for s in fields}
        if len(stats) != len(fields):
            raise ValueError("duplicate team stat identity")
        _, fga = _split(stats.get("fieldGoalsMade-fieldGoalsAttempted"), "team FG")
        _, fta = _split(stats.get("freeThrowsMade-freeThrowsAttempted"), "team FT")
        orb = _number(stats.get("offensiveRebounds"), "team OREB", integer=True)
        turnovers = _number(stats.get("totalTurnovers"), "team total turnovers", integer=True)
        if fga is None or fta is None:
            raise ValueError("missing team attempts")
        possessions = fga + .44 * fta - orb + turnovers
        if possessions <= 0:
            raise ValueError("invalid team possessions")
        team_payloads[tid] = {"record_type": "team_box", "team_id": tid,
            "opponent_id": next(t for t in competitors if t != tid),
            "possessions_estimate": possessions, "possession_basis": "box_estimate",
            "duration_minutes": duration,
            "points": _number(competitors[tid].get("score"), "final score", integer=True),
            "roster_count": 0, "roster_count_basis": "players listed in observed completed box"}
    seen_boxes = set()
    for box in player_boxes:
        tid = _id(box.get("team", {}).get("id"), "player box team")
        if tid not in team_payloads or tid in seen_boxes:
            raise ValueError("player box team identity mismatch/duplicate")
        seen_boxes.add(tid)
        blocks = box.get("statistics")
        if not isinstance(blocks, list) or len(blocks) != 1:
            raise ValueError("one explicit player statistics block required")
        block = blocks[0]
        names, athletes = block.get("names"), block.get("athletes")
        if not isinstance(names, list) or len(set(names)) != len(names) or not isinstance(athletes, list) or len(athletes) < 5:
            raise ValueError("player statistic names/athletes missing or ambiguous")
        if not {"MIN", "PTS", "REB", "AST", "3PT"}.issubset(names):
            raise ValueError("required named player statistics absent")
        team_payloads[tid]["roster_count"] = len(athletes)
        for athlete in athletes:
            identity = athlete.get("athlete", {})
            pid = _id(identity.get("id"), "player")
            if pid in players:
                raise ValueError("duplicate player identity across game")
            stats = athlete.get("stats")
            if stats is None:
                stats = []
            if not isinstance(stats, list) or len(stats) not in (0, len(names)):
                raise ValueError("player stat cells differ from named fields")
            cells = dict(zip(names, stats))
            dnp = athlete.get("didNotPlay")
            if dnp is not None and type(dnp) is not bool:
                raise ValueError("DNP flag must be explicit boolean or absent")
            threes, _ = _split(cells.get("3PT"), "player threes")
            source_row = {"minutes": _minutes(cells.get("MIN")), "did_not_play": dnp,
                "points": _number(cells.get("PTS"), "points", missing=True, integer=True),
                "rebounds": _number(cells.get("REB"), "rebounds", missing=True, integer=True),
                "assists": _number(cells.get("AST"), "assists", missing=True, integer=True),
                "three_point_field_goals_made": threes}
            if source_row["minutes"] is not None and source_row["minutes"] > duration:
                raise ValueError("player minutes exceed completed game duration")
            normalized = normalise_player_row(source_row)
            if normalized["conflict"] or normalized["invalid_values"]:
                raise ValueError("invalid/conflicting player source measurements: " + pid)
            payload = normalized["payload"]
            tp = team_payloads[tid]
            payload.update({"record_type": "player_box", "team_id": tid, "opponent_id": tp["opponent_id"],
                "starter": athlete.get("starter") if type(athlete.get("starter")) is bool else None,
                "position": identity.get("position", {}).get("abbreviation"),
                "possessions_estimate": tp["possessions_estimate"], "duration_minutes": duration,
                "possession_basis": "box_estimate", "scheduled_team_ids": sorted(competitors)})
            player_payloads[pid] = payload
            players[pid] = {"team_id": tid, "opponent_id": tp["opponent_id"],
                "participation": payload["participation"],
                "actual_points": int(payload["counts"]["points"]) if payload["participation"] == "played" and payload["counts"]["points"] is not None else None,
                "display_minutes": source_row["minutes"],
                "participation_evidence": payload["participation_basis"]}
    for tid, team in team_payloads.items():
        team_players = [p for p in players.values() if p["team_id"] == tid]
        incomplete = any(p["actual_points"] is None and p["participation"] != "dnp" for p in team_players)
        if not incomplete and sum(p["actual_points"] or 0 for p in team_players) != team["points"]:
            raise ValueError("reported player points do not reconcile to final team score")
        reconciliation[tid] = "incomplete_reported_counts" if incomplete else "reconciled"
        unknown_minutes = any(p["participation"] == "unknown" or
            (p["participation"] == "played" and not p["display_minutes"]) for p in team_players)
        if not unknown_minutes:
            total = sum(p["display_minutes"] or 0 for p in team_players)
            # Displayed integer minutes can round; one minute per listed player
            # is a conservative upper allowance, never invented elapsed time.
            if abs(total - 5 * duration) > len(team_players):
                raise ValueError("reported player minutes do not reconcile to game duration")
        minute_reconciliation[tid] = "incomplete_reported_exposure" if unknown_minutes else "reconciled_with_display_rounding_allowance"
    observations = []
    for kind, payloads in (("team_box", team_payloads), ("player_box", player_payloads)):
        for entity, payload in sorted(payloads.items()):
            if kind == "player_box":
                payload["team_source_payload_hash"] = payload_digest(team_payloads[payload["team_id"]])
            observations.append(Observation(source_id="espn:wnba-summary-v2", record_id=f"{kind}:{gid}:{entity}",
                entity_id=entity, event_id=gid, season=season, kind=ObservationKind.HISTORICAL_OUTCOME,
                effective_at=tip, payload=payload, observed_at=observed, retrieved_at=observed,
                time_basis=TimeBasis.OBSERVED,
                time_note="Actual receipt of final ESPN box; receipt is completion upper bound, not exact final-whistle time."))
    FutureOnlyIndex(observations, frozen_at=frozen)
    return {"status": "OK", "observations": tuple(observations), "players": players, "game_id": gid,
            "observed_at": stamp(observed), "game_completed_at": stamp(observed),
            "completion_clock_basis": "observed_final_upper_bound", "source_sha256": digest(raw),
            "points_reconciliation": reconciliation, "minutes_reconciliation": minute_reconciliation}


def settlement_records(parsed, forecasts, previous):
    """One semantic revision per affected admitted forecast; repeats stay quiet."""
    result = []
    if parsed["status"] != "OK":
        return result
    for forecast in forecasts:
        if forecast["game_id"] != parsed["game_id"]:
            continue
        player = parsed["players"].get(forecast["player_id"])
        if player is None:
            facts = {"participation": "unknown", "actual_points": None, "display_minutes": None,
                     "participation_evidence": "Player absent from completed box; absence does not establish DNP."}
        else:
            if player["team_id"] != forecast["team_id"] or player["opponent_id"] != forecast["opponent_id"]:
                raise ValueError("settlement team identity differs from forecast")
            facts = {k: player[k] for k in ("participation", "actual_points", "display_minutes", "participation_evidence")}
            if facts["participation"] == "played" and facts["actual_points"] is None:
                # Preserve played evidence without pretending an unreported count is zero.
                facts["participation"] = "unknown"
                facts["participation_evidence"] += "; participation known played but points unreported, settlement unresolved"
        old = previous.get(forecast["forecast_id"])
        if old is not None and all(old.get(k) == v for k, v in facts.items()):
            continue
        record = {"schema": "wnba-shadow-settlement-v1", "forecast_id": forecast["forecast_id"],
            "game_id": forecast["game_id"], "player_id": forecast["player_id"], **facts,
            "observed_at": parsed["observed_at"], "game_completed_at": parsed["game_completed_at"],
            "completion_clock_basis": parsed["completion_clock_basis"],
            "source_sha256": parsed["source_sha256"], "supersedes": old["settlement_id"] if old else None}
        record["settlement_id"] = "espn-" + digest(encode(record))[:32]
        result.append(record)
    return result


def fetch_body(url, *, clock=utcnow, deadline=None):
    """Bound response bytes/time and return partial bytes on transport failure."""
    requested = instant(clock())
    deadline = time.monotonic() + 45 if deadline is None else deadline
    req = Request(url, headers={"User-Agent": "beating-the-opener/1.0 (research archive)",
                                "Accept": "application/json", "Accept-Encoding": "identity"})
    body, status, complete, error_text = bytearray(), None, False, None
    try:
        try:
            response = urlopen(req, timeout=max(.1, min(12, deadline - time.monotonic())))
        except HTTPError as error:
            response = error
        with response:
            status = response.status if hasattr(response, "status") else response.code
            while True:
                if time.monotonic() >= deadline:
                    raise ValueError("outcome response deadline exhausted")
                chunk = response.read1(65536)
                if not chunk:
                    complete = True
                    break
                body.extend(chunk)
                if len(body) > 10_000_000:
                    raise ValueError("outcome response exceeds ten megabytes")
    except (OSError, ValueError, AttributeError) as error:
        error_text = type(error).__name__ + ": " + str(error)
    received = instant(clock())
    body = bytes(body)
    return body, {"url": url, "requested_at": stamp(requested), "received_at": stamp(received),
                  "status_code": status, "sha256": digest(body), "body_complete": complete,
                  "error": error_text}


def refresh_outcomes(history_root, ledger, frozen_at, *, run_id, repo=None, clock=utcnow, fetch=None):
    """Discover, archive and append fresh boxes; no model fit or score peeking.

    Revisit the final 14 days and all unresolved forecast dates. Uncovered dates
    since freeze remain queued, so an outage cannot silently erase history.
    Requests and revisions are bounded to the registered study calendar.
    """
    del repo  # The caller commits this tree to its authorized private repository.
    identifier(run_id, "run_id")
    if "/" in run_id or "\\" in run_id or run_id in (".", ".."):
        raise ValueError("unsafe outcome run identity")
    root = Path(history_root)
    run = root / run_id
    if run.exists():
        raise FileExistsError("outcome run must append a fresh directory")
    frozen, now = instant(frozen_at), instant(clock())
    if frozen.year < 2026 or now < frozen:
        raise ValueError("outcome refresh requires an active future freeze")
    forecasts, _ = ledger.population(now)
    previous = ledger.settlements({r["forecast_id"]: r for r in forecasts}, now)
    coverage, old_versions, restored, exact_versions = set(), {}, [], {}
    monotonic_start = time.monotonic()
    for receipt_path in sorted(root.glob("*/receipt.json")):
        receipt = json.loads(receipt_path.read_bytes())
        if receipt.get("schema") != "wnba-outcomes-run-v1" or receipt.get("frozen_at") != stamp(frozen):
            raise ValueError("restored history receipt differs from frozen study")
        if not frozen <= instant(receipt["started_at"]) <= instant(receipt["completed_at"]) <= now:
            raise ValueError("restored history receipt has invalid clocks")
        for name, source in receipt["raw_sources"].items():
            if not re.fullmatch(r"(?:scoreboard-[0-9]{4}-[0-9]{2}-[0-9]{2}|summary-[0-9]+)", name):
                raise ValueError("unsafe restored source identity")
            if digest((receipt_path.parent / (name + ".body")).read_bytes()) != source["sha256"]:
                raise ValueError("restored raw outcome bytes differ from their hash")
            receipt_bytes = (receipt_path.parent / (name + ".receipt.json")).read_bytes()
            if digest(receipt_bytes) != source["receipt_sha256"]:
                raise ValueError("restored raw outcome receipt hash mismatch")
            source_receipt = json.loads(receipt_bytes)
            if source.get("validated"):
                if not instant(receipt["started_at"]) <= instant(source_receipt["requested_at"]) <= instant(source_receipt["received_at"]) <= instant(receipt["completed_at"]):
                    raise ValueError("restored outcome source receipt clocks invalid")
                if type(source_receipt.get("status_code")) is not int or source_receipt["status_code"] != 200 or source_receipt.get("body_complete") is not True:
                    raise ValueError("restored validated outcome transport was unsuccessful")
        observations_path = receipt_path.parent / "observations.jsonl.gz"
        if digest(observations_path.read_bytes()) != receipt.get("observations_sha256"):
            raise ValueError("restored outcome observations hash mismatch")
        for row in load_observations(observations_path):
            if not instant(receipt["started_at"]) <= row.available_at <= instant(receipt["completed_at"]):
                raise ValueError("restored outcome availability is outside capture run")
            source_name = "summary-" + row.event_id
            source = receipt["raw_sources"].get(source_name, {})
            if not source.get("validated") or payload_digest(row.manifest_entry()) not in source.get("observation_manifest_hashes", []):
                raise ValueError("restored outcome lacks a validated raw source binding")
            source_receipt = json.loads((receipt_path.parent / (source_name + ".receipt.json")).read_bytes())
            if row.observed_at != instant(source_receipt["received_at"]):
                raise ValueError("restored outcome observation clock differs from raw receipt")
            version_key = (row.source_id, row.record_id, row.available_at)
            if version_key in exact_versions and exact_versions[version_key] != payload_digest(row.manifest_entry()):
                raise ValueError("conflicting restored observation versions share one clock")
            exact_versions[version_key] = payload_digest(row.manifest_entry())
            restored.append(row)
            key = (row.source_id, row.record_id)
            if key not in old_versions or row.available_at > old_versions[key].available_at:
                old_versions[key] = row
        coverage.update(receipt["covered_dates"])
    FutureOnlyIndex(restored, frozen_at=frozen)
    # The registered final settlement window ends fourteen days after the cap.
    # Later recovery can still retrieve those dates; never exclude that window.
    if frozen >= CAP:
        raise ValueError("candidate freeze must precede registered calendar cap")
    start = frozen.astimezone(ET).date()
    end = min(now, CAP + timedelta(days=14)).astimezone(ET).date()
    dates = {start + timedelta(days=i) for i in range((end - start).days + 1)
             if (start + timedelta(days=i)).isoformat() not in coverage or end - (start + timedelta(days=i)) <= timedelta(days=14)}
    dates.update(instant(r["tip_at"]).astimezone(ET).date() for r in forecasts
                 if previous.get(r["forecast_id"], {}).get("participation", "unknown") == "unknown" and instant(r["tip_at"]) < now)
    run.mkdir(parents=True)
    attempts, covered, new, events, raw_sources = [], [], [], {}, {}
    # Bound an outage catch-up without acknowledging unrequested dates. Older
    # missing dates come first; subsequent runs continue the saved queue.
    ordered_dates = sorted(dates)
    pending_dates = ordered_dates[21:]
    if pending_dates:
        attempts.append({"source": "espn_scoreboard", "status": "FAILED", "cause": "catchup_backlog",
                         "pending_dates": len(pending_dates), "error": "More than 21 scoreboard dates require capture; remaining dates stay queued."})
    transport = fetch or (lambda url: fetch_body(url, clock=clock, deadline=monotonic_start + 240))

    def capture(url, name):
        if time.monotonic() - monotonic_start >= 240:
            raise ValueError("outcome capture four-minute budget exhausted; source remains pending")
        raw, receipt = transport(url)
        if not isinstance(raw, bytes) or not isinstance(receipt, dict):
            raise ValueError("raw source transport returned no archivable response")
        # Even invalid clocks or partial failure bodies remain evidence. Only a
        # separately validated receipt may back a state update or covered day.
        write_once(run / (name + ".body"), raw)
        write_once(run / (name + ".receipt.json"), encode(receipt))
        raw_sources[name] = {"sha256": digest(raw), "receipt_sha256": digest(encode(receipt)), "validated": False}
        if receipt.get("url") != url or receipt.get("sha256") != digest(raw):
            raise ValueError("raw source transport receipt mismatch")
        requested, received, current = instant(receipt["requested_at"]), instant(receipt["received_at"]), instant(clock())
        if not now <= requested <= received <= current:
            raise ValueError("source transport clock is outside current run")
        if type(receipt.get("status_code")) is not int or receipt["status_code"] != 200 or receipt.get("body_complete") is not True or receipt.get("error"):
            raise ValueError("ESPN source transport incomplete or unsuccessful")
        raw_sources[name]["validated"] = True
        return raw, received

    for date in ordered_dates[:21]:
        label = date.isoformat()
        try:
            raw, received = capture(f"{API}/scoreboard?dates={date.strftime('%Y%m%d')}", "scoreboard-" + label)
            for event in scoreboard_events(raw, observed_at=received, frozen_at=frozen, expected_date=date):
                old = events.get(event["game_id"])
                if old is not None and old != event:
                    raise ValueError("scoreboard dates disagree on game identity")
                events[event["game_id"]] = event
            document = json.loads(raw)
            pending = False
            for source_event in document["events"]:
                if instant(source_event["date"]) < frozen:
                    continue
                status = source_event.get("status", {})
                if not _completed(status) and status.get("type", {}).get("name") != "STATUS_CANCELED":
                    pending = True
            if date < received.astimezone(ET).date() and not pending:
                covered.append(label)
            attempts.append({"source": "espn_scoreboard", "date": label, "status": "OK", "received_at": stamp(received)})
        except (ValueError, OSError, KeyError, TypeError, AttributeError) as error:
            attempts.append({"source": "espn_scoreboard", "date": label, "status": "FAILED", "error": str(error)})
    # A scoreboard day is not fully covered if any of its completed boxes fail.
    failed_days = set()
    for gid, event in sorted(events.items()):
        try:
            raw, received = capture(f"{API}/summary?event={gid}", "summary-" + gid)
            parsed = parse_summary(raw, event=event, observed_at=received, frozen_at=frozen, now=clock())
            if parsed["status"] != "OK":
                raise ValueError("final scoreboard has nonfinal summary")
            revisions = settlement_records(parsed, forecasts, previous)
            raw_sources["summary-" + gid]["observation_manifest_hashes"] = [payload_digest(row.manifest_entry()) for row in parsed["observations"]]
            for row in parsed["observations"]:
                old = old_versions.get((row.source_id, row.record_id))
                if old is not None and old.payload_hash == row.payload_hash:
                    continue
                if old is not None and row.available_at <= old.available_at:
                    raise ValueError("changed observed state requires later availability")
                new.append(row)
            for record in revisions:
                ledger.settle(record, raw, clock())
                previous[record["forecast_id"]] = record
            attempts.append({"source": "espn_summary", "game_id": gid, "status": "OK", "received_at": stamp(received),
                             "new_settlement_revisions": len(revisions), "points_reconciliation": parsed["points_reconciliation"],
                             "minutes_reconciliation": parsed["minutes_reconciliation"]})
        except (ValueError, OSError, KeyError, TypeError, AttributeError) as error:
            failed_days.add(instant(event["tip_at"]).astimezone(ET).date().isoformat())
            attempts.append({"source": "espn_summary", "game_id": gid, "status": "FAILED", "error": str(error)})
    new = tuple(new)
    FutureOnlyIndex(new, frozen_at=frozen)
    write_observations(run / "observations.jsonl.gz", new)
    status = "DEGRADED" if any(a["status"] == "FAILED" for a in attempts) else "OK" if events else "NO_EVENTS"
    receipt = {"schema": "wnba-outcomes-run-v1", "run_id": run_id, "frozen_at": stamp(frozen),
        "started_at": stamp(now), "completed_at": stamp(clock()), "status": status,
        "covered_dates": sorted(set(covered) - failed_days), "attempts": attempts,
        "observations_sha256": digest((run / "observations.jsonl.gz").read_bytes()),
        "new_observations": len(new), "raw_sources": raw_sources,
        "completion_clock_basis": "observed_final_upper_bound"}
    write_once(run / "receipt.json", encode(receipt))
    return {"observations": new, "restored_observations": tuple(restored),
            "status": status, "attempts": attempts, "receipt": receipt}
