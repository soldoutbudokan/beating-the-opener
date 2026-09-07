"""Private prospective points capture. No fitting, wagers, or durable-seal claims.

Capture current BettingPros quotes and ESPN identity evidence, call an already
frozen future-only model, then stage an append-only shadow batch. Exact response
bytes and failed requests are written before parsing. A separate runtime must
save the batch durably and attach its verified provider receipt.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import timedelta
import json
from pathlib import Path
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

from research.clocks import parse
from research.engine.store import ForecastRequest
from research.operations.collection import (
    BP, CollectionError, SAFE_HEADERS, bp_key, finite, implied, objects,
    validate_bp_events, validate_bp_offers,
)
from . import shadow
from .model import price_forecast

ESPN = "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba"
BP_TEAMS = {"ATL": "ATL", "CHI": "CHI", "CON": "CON", "DAL": "DAL", "GSV": "GS",
            "IND": "IND", "LAS": "LA", "LVA": "LV", "MIN": "MIN", "NYL": "NY",
            "PHO": "PHX", "SEA": "SEA", "WAS": "WSH", "TOR": "TOR", "PDX": "POR"}


def exact_name(value):
    if not isinstance(value, str):
        return None
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split()) or None


def stable_id(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("MISSING_STABLE_ID")
    value = str(value)
    if not re.fullmatch(r"[0-9]+", value):
        raise ValueError("INVALID_STABLE_ID")
    return value


def american_decimal(value):
    if not finite(value) or abs(value) < 100:
        raise ValueError("INVALID_AMERICAN_PRICE")
    return 1 + (value / 100 if value > 0 else 100 / -value)


class MappingError(ValueError):
    def __init__(self, reason, evidence):
        super().__init__(reason)
        self.evidence = evidence


class Recorder:
    """Bounded official-source requests with immutable raw receipt evidence."""
    def __init__(self, directory, *, clock=shadow.utcnow, opener=None, sleeper=time.sleep,
                 max_requests=100, tries=3, timeout=12, budget_seconds=600):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.clock, self.opener, self.sleeper = clock, opener or urllib.request.urlopen, sleeper
        self.max_requests, self.tries, self.timeout = max_requests, tries, timeout
        self.deadline = time.monotonic() + budget_seconds
        self.attempts, self.snapshots = [], {}
        self.last = None

    def get(self, url, params, validator, headers=None):
        allowed = url in (BP + "/events", BP + "/offers", ESPN + "/scoreboard", ESPN + "/summary") or (
            url.startswith(ESPN + "/teams/") and re.fullmatch(r"[0-9]+/roster", url[len(ESPN + "/teams/"):]))
        if not allowed or any(re.search("key|secret|auth|token", key, re.I) for key in params):
            raise CollectionError("UNAPPROVED_CAPTURE_REQUEST")
        for retry in range(self.tries):
            if len(self.attempts) >= self.max_requests or time.monotonic() >= self.deadline:
                raise CollectionError("CAPTURE_REQUEST_BUDGET")
            sequence = len(self.attempts) + 1
            entry = {"kind": "source_request", "sequence": sequence, "url": url,
                     "params": params, "attempt": retry + 1,
                     "requested_at": shadow.stamp(self.clock())}
            body, status, complete, response_headers = bytearray(), None, False, {}
            parsed = None
            try:
                request = urllib.request.Request(url + "?" + urllib.parse.urlencode(params), headers={
                    "User-Agent": "beating-the-opener-research/2.0", "Accept-Encoding": "identity", **(headers or {})})
                remaining = max(.1, self.deadline - time.monotonic())
                try:
                    response = self.opener(request, timeout=min(self.timeout, remaining))
                except urllib.error.HTTPError as error:
                    response = error
                with response:
                    status = response.status if hasattr(response, "status") else response.code
                    response_headers = {str(k).lower(): str(v) for k, v in response.headers.items()
                                        if str(k).lower() in SAFE_HEADERS}
                    while True:
                        if time.monotonic() >= self.deadline:
                            raise CollectionError("CAPTURE_RESPONSE_DEADLINE")
                        chunk = response.read1(65536)
                        if not chunk:
                            complete = True
                            break
                        body.extend(chunk)
                        if len(body) > 10_000_000:
                            raise CollectionError("CAPTURE_RESPONSE_SIZE")
                entry["received_at"] = shadow.stamp(self.clock())
                if shadow.instant(entry["received_at"]) < shadow.instant(entry["requested_at"]):
                    raise CollectionError("CAPTURE_CLOCK_MOVED_BACKWARD")
                if status != 200:
                    raise CollectionError("HTTP_" + str(status))
                parsed = json.loads(body)
                validator(parsed)
                entry["status"] = "VALIDATED"
            except Exception as error:
                entry.update(status="FAILED", reason=str(error) if isinstance(error, CollectionError) else type(error).__name__)
            finally:
                entry.setdefault("received_at", shadow.stamp(self.clock()))
                entry.update(http_status=status, response_headers=response_headers, body_complete=complete)
                if status is not None:
                    name = f"response-{sequence:04d}.json"
                    raw = bytes(body)
                    shadow.write_once(self.directory / name, raw)
                    self.snapshots[name] = raw
                    entry.update(snapshot=name, snapshot_sha256=shadow.digest(raw), body_bytes=len(raw))
                self.attempts.append(entry)
                shadow.write_once(self.directory / f"request-{sequence:04d}.json", shadow.encode(entry))
                self.sleeper(.15)
            if entry["status"] == "VALIDATED":
                self.last = entry
                return parsed, entry
            if retry + 1 < self.tries:
                self.sleeper(min(2 ** retry, 4))
        raise CollectionError("CAPTURE_REQUEST_FAILED")


def map_event(event, scoreboard_events):
    """Match unique current team identities and exact tip, without invented IDs."""
    tip = parse("bp.scheduled", event.get("scheduled"))
    home, away = BP_TEAMS.get(event.get("home")), BP_TEAMS.get(event.get("visitor"))
    if not home or not away or home == away:
        raise ValueError("UNMAPPED_BP_TEAM_CODE")
    matches = {}
    for source in scoreboard_events:
        competition = source.get("competitions", [])
        if len(competition) != 1:
            continue
        comp = competition[0]
        sides = comp.get("competitors", [])
        if len(sides) != 2:
            continue
        by_side = {side.get("homeAway"): side.get("team", {}) for side in sides}
        if set(by_side) != {"home", "away"}:
            continue
        if by_side["home"].get("abbreviation") != home or by_side["away"].get("abbreviation") != away:
            continue
        source_tip = parse("espn.event.date", source.get("date"))
        if source_tip != tip:
            continue
        if comp.get("date") is not None and parse("espn.event.date", comp["date"]) != source_tip:
            raise ValueError("ESPN_EVENT_COMPETITION_CLOCK_CONFLICT")
        source_status = source.get("status", {}).get("type", {}).get("state")
        competition_status = comp.get("status", {}).get("type", {}).get("state")
        if source_status is not None and competition_status is not None and source_status != competition_status:
            raise ValueError("ESPN_EVENT_COMPETITION_STATUS_CONFLICT")
        status = source_status if source_status is not None else competition_status
        if status != "pre":
            continue
        game = stable_id(source.get("id"))
        if stable_id(comp.get("id")) != game:
            raise ValueError("ESPN_COMPETITION_ID_CONFLICT")
        mapped = {"game_id": game, "tip_at": shadow.stamp(tip),
                  "home_id": stable_id(by_side["home"].get("id")),
                  "away_id": stable_id(by_side["away"].get("id")),
                  "home_abbreviation": home, "away_abbreviation": away,
                  "bp_event_id": stable_id(event.get("id")),
                  "source_snapshot_sha256": source.get("_snapshot_sha256"),
                  "observed_at": source.get("_received_at")}
        if game in matches:
            keys = set(mapped) - {"source_snapshot_sha256", "observed_at"}
            if any(matches[game][key] != mapped[key] for key in keys):
                raise ValueError("CONFLICTING_ESPN_EVENT_REVISIONS")
        else:
            matches[game] = mapped
    if len(matches) != 1:
        raise ValueError("MISSING_OR_AMBIGUOUS_ESPN_MATCHUP")
    return next(iter(matches.values()))


def map_player(offer, event, rosters):
    participants = offer.get("participants", [])
    if len(participants) != 1 or not isinstance(participants[0].get("player"), dict):
        raise ValueError("MISSING_OR_AMBIGUOUS_BP_PLAYER")
    player = participants[0]["player"]
    bp_id = stable_id(offer.get("player_id"))
    if player.get("id") is not None and stable_id(player["id"]) != bp_id:
        raise ValueError("BP_PLAYER_ID_CONFLICT")
    abbreviation = BP_TEAMS.get(player.get("team"))
    if abbreviation not in (event["home_abbreviation"], event["away_abbreviation"]):
        raise ValueError("BP_PLAYER_TEAM_CONFLICT")
    is_home = abbreviation == event["home_abbreviation"]
    team, opponent = (event["home_id"], event["away_id"]) if is_home else (event["away_id"], event["home_id"])
    roster = rosters.get(team)
    if roster is None or stable_id(roster.get("team", {}).get("id")) != team:
        raise ValueError("MISSING_OR_CONFLICTING_ESPN_ROSTER")
    athletes = objects(roster, "athletes")
    athlete_ids = [stable_id(a.get("id")) for a in athletes]
    if len(athlete_ids) != len(set(athlete_ids)):
        raise ValueError("DUPLICATE_ESPN_ROSTER_ID")
    bp_name = " ".join(str(player.get(k) or "").strip() for k in ("first_name", "last_name")).strip()
    crosswalk = player.get("espn_id")
    if crosswalk is not None:
        espn_id = stable_id(crosswalk)
        matches = [a for a in athletes if stable_id(a["id"]) == espn_id]
        method = "explicit_bp_espn_id_and_current_roster_team"
    else:
        if not player.get("first_name") or not player.get("last_name"):
            raise ValueError("INCOMPLETE_BP_PLAYER_NAME")
        name = exact_name(bp_name)
        matches = [a for a in athletes if exact_name(a.get("fullName", a.get("displayName"))) == name]
        method = "unique_exact_full_name_and_current_roster_team"
    candidates = [{"player_id": stable_id(a["id"]), "name": a.get("fullName", a.get("displayName"))} for a in matches]
    if len(matches) != 1:
        raise MappingError("MISSING_OR_AMBIGUOUS_ESPN_PLAYER", {
            "rule": method, "bp_player_id": bp_id, "bp_name": bp_name,
            "explicit_espn_id": crosswalk, "espn_team_id": team, "candidate_matches": candidates,
            "roster_snapshot_sha256": roster.get("_snapshot_sha256"),
            "roster_received_at": roster.get("_received_at")})
    athlete = matches[0]
    return {"player_id": stable_id(athlete["id"]), "team_id": team, "opponent_id": opponent,
            "mapping_evidence": {"rule": method, "bp_player_id": bp_id,
                "bp_name": bp_name, "espn_name": athlete.get("fullName", athlete.get("displayName")),
                "bp_team_code": player.get("team"), "espn_team_id": team,
                "roster_snapshot_sha256": roster.get("_snapshot_sha256"),
                "roster_received_at": roster.get("_received_at"),
                "scoreboard_snapshot_sha256": event["source_snapshot_sha256"],
                "scoreboard_received_at": event["observed_at"], "candidate_matches": candidates}}


def quote_pairs(offer, receipt):
    """Return both fixed-book requests, including exclusions and bad pairs."""
    sides = {book: {"over": [], "under": []} for book in shadow.BOOKS}
    for selection in offer["selections"]:
        if selection.get("active") is not True:
            continue
        for book in selection["books"]:
            if book["id"] not in sides:
                continue
            for line in book["lines"]:
                if line.get("active") is True and line.get("main") is True and line.get("is_off") is False:
                    sides[book["id"]][selection["selection"]].append(line)
    result = []
    received = shadow.instant(receipt["received_at"])
    for book_id, side in sides.items():
        request = {"book_id": book_id, "bp_offer_id": stable_id(offer.get("id")),
                   "bp_player_id": stable_id(offer.get("player_id"))}
        try:
            if offer.get("active") is not True:
                raise ValueError("INACTIVE_OFFER")
            if any(len(side[s]) != 1 for s in ("over", "under")):
                raise ValueError("MISSING_OR_AMBIGUOUS_PAIRED_MAIN_LINE")
            over, under = side["over"][0], side["under"][0]
            if over["line"] != under["line"] or over["line"] < 0 or over["line"] * 2 != int(over["line"] * 2):
                raise ValueError("INCOHERENT_PAIRED_LINE")
            if not 1 <= implied(over["cost"]) + implied(under["cost"]) <= 1.15:
                raise ValueError("INCOHERENT_PAIRED_PRICE")
            updated = [parse("bp.updated", line.get("updated")) for line in (over, under)]
            if any(not received - timedelta(hours=2) <= clock <= received for clock in updated):
                raise ValueError("STALE_OR_FUTURE_QUOTE_CLOCK")
            request["quote"] = {"quote_id": f"bp-{request['bp_offer_id']}-{book_id}", "book_id": book_id,
                "line": float(over["line"]), "over_decimal": american_decimal(over["cost"]),
                "under_decimal": american_decimal(under["cost"]), "market": "points",
                "active": True, "main": True, "is_off": False,
                "received_at": receipt["received_at"], "over_updated_at": shadow.stamp(updated[0]),
                "under_updated_at": shadow.stamp(updated[1]), "snapshot_sha256": receipt["snapshot_sha256"]}
        except (ValueError, TypeError, KeyError) as error:
            request["reason"] = str(error)
        result.append(request)
    return result


def scan(model, ledger, batch_id, *, repo, clock=shadow.utcnow, opener=None, sleeper=time.sleep):
    """Stage one scan from the runtime's verified frozen bundle and seed model.

    The caller must verify seed bytes against the immutable bundle manifest;
    a model object does not by itself prove that its history came from that
    bundle. The standalone CLI additionally checks ``study.seed_sha256``.
    Only the external runtime may seal durability.
    """
    shadow.identifier(batch_id, "batch_id")
    started = clock()
    study = ledger.study(started)
    if model.frozen_at != shadow.instant(study["frozen_at"]):
        raise ValueError("Model and study freeze disagree")
    if study.get("model_recipe_hash") != model.recipe_hash:
        raise ValueError("Model differs from study recipe identity")
    recorder = Recorder(shadow._safe_path(ledger.root, "captures", batch_id),
                        clock=clock, opener=opener, sleeper=sleeper)
    attempts, forecasts, raw_offers, scoreboard, rosters = [], [], [], [], {}
    headers = None
    events = []
    try:
        headers = {"x-api-key": bp_key(Path(repo))}
        payload, receipt = recorder.get(BP + "/events", {
            "sport": "WNBA", "start": started.date().isoformat(),
            "end": (started.date() + timedelta(days=1)).isoformat()}, validate_bp_events, headers)
        if len(payload["events"]) >= 200:
            raise CollectionError("EVENT_LIMIT_OR_TRUNCATION")
        event_ids = [stable_id(e.get("id")) for e in payload["events"]]
        if len(event_ids) != len(set(event_ids)):
            raise CollectionError("DUPLICATE_BP_EVENT_ID")
        for event in payload["events"]:
            tip = parse("bp.scheduled", event["scheduled"])
            eligible = event.get("status") == "scheduled" and timedelta(minutes=15) <= tip - started <= timedelta(hours=24)
            attempts.append({"kind": "event_request", "bp_event_id": str(event["id"]),
                "status": "ELIGIBLE" if eligible else "EXCLUDED", "tip_at": shadow.stamp(tip),
                "reason": None if eligible else "OUTSIDE_PREGAME_WINDOW_OR_NOT_EXPLICITLY_SCHEDULED",
                "provider_status": event.get("status")})
            if eligible:
                events.append(event)
        if len(events) > 20:
            raise CollectionError("FUTURE_EVENT_BUDGET")
    except Exception as error:
        attempts.append({"kind": "capture_failure", "source": "bettingpros_events", "reason": type(error).__name__ + ":" + str(error)})
    if events:
        local = started.astimezone(shadow.EASTERN).date()
        for date in (local, local + timedelta(days=1)):
            try:
                payload, receipt = recorder.get(ESPN + "/scoreboard", {"dates": date.strftime("%Y%m%d")}, lambda d: objects(d, "events"))
                scoreboard.extend(dict(event, _snapshot_sha256=receipt["snapshot_sha256"], _received_at=receipt["received_at"]) for event in payload["events"])
            except Exception as error:
                attempts.append({"kind": "capture_failure", "source": "espn_scoreboard", "date": date.isoformat(), "reason": type(error).__name__})
    for event in events:
        mapped = None
        try:
            mapped = map_event(event, scoreboard)
            for team in (mapped["home_id"], mapped["away_id"]):
                if team not in rosters:
                    payload, receipt = recorder.get(ESPN + f"/teams/{team}/roster", {}, lambda d: objects(d, "athletes"))
                    rosters[team] = dict(payload, _snapshot_sha256=receipt["snapshot_sha256"], _received_at=receipt["received_at"])
        except Exception as error:
            attempts.append({"kind": "mapping_failure", "bp_event_id": str(event["id"]), "reason": str(error)})
        try:
            params = {"sport": "WNBA", "market_id": 393, "event_id": event["id"], "location": "ALL"}
            validator = lambda d: validate_bp_offers(d, event["id"], 393)
            payload, receipt = recorder.get(BP + "/offers", params, validator, headers)
            pages = max(1, payload["_pagination"]["total_pages"])
            offers = [(offer, receipt) for offer in payload["offers"]]
            total = payload["_pagination"].get("total_items")
            for page in range(2, pages + 1):
                payload, receipt = recorder.get(BP + "/offers", dict(params, page=page), validator, headers)
                if max(1, payload["_pagination"]["total_pages"]) != pages:
                    raise CollectionError("PAGINATION_CHANGED_DURING_CAPTURE")
                offers.extend((offer, receipt) for offer in payload["offers"])
            ids = [str(offer["id"]) for offer, _ in offers]
            if len(ids) != len(set(ids)) or (total is not None and len(offers) != total):
                raise CollectionError("INCOMPLETE_OR_DUPLICATE_OFFER_PAGINATION")
            raw_offers.extend((event, mapped, offer, receipt) for offer, receipt in offers)
            if not offers:
                attempts.append({"kind": "offer_request", "bp_event_id": str(event["id"]), "status": "NO_OFFERS"})
        except Exception as error:
            attempts.append({"kind": "capture_failure", "source": "bettingpros_offers", "bp_event_id": str(event["id"]), "reason": type(error).__name__ + ":" + str(error)})
    requests = []
    for event, mapped, offer, receipt in raw_offers:
        try:
            candidates = quote_pairs(offer, receipt)
        except Exception as error:
            candidates = [{"book_id": book, "bp_offer_id": str(offer.get("id")),
                           "bp_player_id": str(offer.get("player_id")),
                           "reason": "INVALID_QUOTE_IDENTITY:" + type(error).__name__}
                          for book in shadow.BOOKS]
        requests.extend((event, mapped, offer, candidate) for candidate in candidates)
    duplicate = Counter((str(event["id"]), str(offer["player_id"]), candidate["book_id"])
                        for event, _, offer, candidate in requests if "quote" in candidate)
    for event, mapped, offer, candidate in requests:
        audit = {"kind": "quote_request", **{key: value for key, value in candidate.items() if key != "quote"},
                 "bp_event_id": str(event["id"]), "status": "EXCLUDED"}
        try:
            if candidate.get("reason"):
                raise ValueError(candidate["reason"])
            if mapped is None:
                raise ValueError("UNRESOLVED_ESPN_EVENT")
            if duplicate[(str(event["id"]), str(offer["player_id"]), candidate["book_id"])] != 1:
                raise ValueError("DUPLICATE_PLAYER_EVENT_OFFER")
            identity = map_player(offer, mapped, rosters)
            cutoff = clock()
            if any(shadow.instant(identity["mapping_evidence"][key]) >= cutoff
                   for key in ("roster_received_at", "scoreboard_received_at")):
                raise ValueError("IDENTITY_EVIDENCE_NOT_BEFORE_FORECAST_CUTOFF")
            tip = shadow.instant(mapped["tip_at"])
            if not timedelta(minutes=15) <= tip - cutoff <= timedelta(hours=24):
                raise ValueError("QUOTE_CAPTURE_NO_LONGER_IN_PREGAME_WINDOW")
            request = ForecastRequest(identity["player_id"], mapped["game_id"], identity["team_id"],
                identity["opponent_id"], tip.year, tip, cutoff)
            output = model.predict(request)
            priced = price_forecast(output, "points", candidate["quote"]["line"])
            generated = clock()
            quote = dict(candidate["quote"], game_id=mapped["game_id"], player_id=identity["player_id"])
            forecast_id = "f-" + shadow.digest(shadow.encode({"batch": batch_id,
                "game": mapped["game_id"], "player": identity["player_id"], "quote": quote}))[:32]
            row = {"schema": "wnba-shadow-forecast-v1", "forecast_id": forecast_id,
                "candidate_id": study["candidate_id"], "game_id": mapped["game_id"],
                "player_id": identity["player_id"], "team_id": identity["team_id"],
                "opponent_id": identity["opponent_id"], "market": "points", "tip_at": mapped["tip_at"],
                "input_cutoff_at": shadow.stamp(cutoff), "generated_at": shadow.stamp(generated),
                "input_max_available_at": output.get("input_max_available_at"),
                **{key: study[key] for key in ("bundle_sha256", "recipe_sha256", "implementation_sha256")},
                "input_manifest_sha256": output.get("input_manifest_hash"),
                "model_output": output, "model_output_sha256": shadow.digest(shadow.encode(output)),
                "quote": quote, "mapping_evidence": identity["mapping_evidence"],
                **{key: priced[key] for key in ("p_over", "p_push", "p_under")}, "p_dnp": output["p_dnp"]}
            shadow.validate_forecast(row, study, {shadow.digest(raw) for raw in recorder.snapshots.values()}, generated)
            forecasts.append(row)
            audit.update(status="FORECAST_STAGED", forecast_id=forecast_id, reason=None)
        except Exception as error:
            audit["reason"] = type(error).__name__ + ":" + str(error)
            if isinstance(error, MappingError):
                audit["mapping_evidence"] = error.evidence
        attempts.append(audit)
    failures = sum(a.get("kind") in ("capture_failure", "mapping_failure") for a in attempts)
    status = "DEGRADED" if failures or (events and not forecasts) else "NO_EVENTS" if not events else "OK"
    attempts.append({"kind": "scan_summary", "status": status, "started_at": shadow.stamp(started),
                     "completed_at": shadow.stamp(clock()), "eligible_events": len(events), "forecasts": len(forecasts)})
    result = ledger.stage(batch_id, forecasts, recorder.snapshots, attempts=recorder.attempts + attempts, now=clock())
    result.update(capture_status=status, excluded_quote_requests=sum(a.get("kind") == "quote_request" and a.get("status") == "EXCLUDED" for a in attempts))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--recipe", type=Path, required=True)
    parser.add_argument("--seed", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    from .future import FutureOnlyModel, load_observations
    ledger = shadow.Ledger(args.ledger)
    study = ledger.study()
    recipe_bytes = args.recipe.read_bytes()
    if shadow.digest(recipe_bytes) != study["recipe_sha256"]:
        raise ValueError("Recipe file differs from frozen study")
    if shadow.digest(args.seed.read_bytes()) != study.get("seed_sha256"):
        raise ValueError("Seed file differs from frozen study or lacks a frozen hash")
    model = FutureOnlyModel(load_observations(args.seed), json.loads(recipe_bytes), frozen_at=shadow.instant(study["frozen_at"]))
    print(json.dumps(scan(model, ledger, args.run_id, repo=args.repo), sort_keys=True))


if __name__ == "__main__":
    main()
