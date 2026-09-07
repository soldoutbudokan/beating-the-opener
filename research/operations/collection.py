"""Clocked, append-only source collection. No forecasting, live writes or orders.

Run with ``python -m research.operations.collection --run-id UNIQUE``.
Each run is immutable; reusing a completed ID returns its original report.
Only rebuildable state.json and health.json are atomically replaced.
"""
from __future__ import annotations

import argparse
import ast
import datetime as dt
import fcntl
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request

UTC = dt.timezone.utc
BP = "https://api.bettingpros.com/v3"
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
MARKETS = {"points": 393, "rebounds": 397, "assists": 391, "threes": 390,
           "pra": 396, "pts_ast": 394, "pts_reb": 395, "reb_ast": 398}
BOOKS = {10: "FanDuel", 14: "Fanatics"}
SAFE_HEADERS = {"date", "last-modified", "warning", "deprecation", "sunset", "content-type"}


class CollectionError(RuntimeError):
    pass


def utcnow():
    return dt.datetime.now(UTC)


def stamp(value):
    return value.astimezone(UTC).isoformat()


def parse_time(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        out = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return (out.replace(tzinfo=UTC) if out.tzinfo is None else out).astimezone(UTC)
    except ValueError:
        return None


def encode(value):
    return (json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":")) + "\n").encode()


def write_once(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)


def replace_json(path, value):
    temp = path.with_suffix(".tmp")
    with temp.open("wb") as stream:
        stream.write(encode(value))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def objects(data, key):
    if not isinstance(data, dict) or not isinstance(data.get(key), list):
        raise CollectionError("SCHEMA_" + key.upper())
    if any(not isinstance(x, dict) for x in data[key]):
        raise CollectionError("SCHEMA_" + key.upper())
    return data[key]


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class Recorder:
    """Store exact HTTP entity bytes and request/receipt clocks before parsing.

    Requests use only hard-coded public source endpoints. Credentials are never
    put in URLs, response metadata, exception text, or stdout. API response
    bodies are source evidence, stored verbatim, not printed to job logs.
    """
    def __init__(self, directory, *, opener=None, clock=utcnow, sleeper=time.sleep,
                 max_requests=500, budget_seconds=360, timeout=12, tries=3):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.opener = opener or urllib.request.urlopen
        self.clock, self.sleeper = clock, sleeper
        self.max_requests, self.timeout, self.tries = max_requests, timeout, tries
        self.deadline = time.monotonic() + budget_seconds
        self.count, self.last_received = 0, None

    def get(self, url, params, validator, headers=None):
        if not any(url.startswith(base + "/") for base in (BP, GAMMA, CLOB)):
            raise CollectionError("UNAPPROVED_SOURCE")
        if any(re.search("key|token|secret|auth", key, re.I) for key in params if key != "market"):
            raise CollectionError("CREDENTIAL_IN_QUERY")
        for attempt in range(1, self.tries + 1):
            if self.count >= self.max_requests or time.monotonic() >= self.deadline:
                raise CollectionError("REQUEST_BUDGET_EXHAUSTED")
            self.count += 1
            record = {"schema": "source-request-v1", "sequence": self.count,
                      "url": url, "params": params, "attempt": attempt,
                      "requested_at": stamp(self.clock())}
            body, status, response_headers = bytearray(), None, {}
            body_complete = False
            error_code = None
            try:
                req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params),
                    headers={"User-Agent": "beating-the-opener-research/1.0", "Accept-Encoding": "identity",
                             **(headers or {})})
                remaining = max(0.1, self.deadline - time.monotonic())
                try:
                    response = self.opener(req, timeout=min(self.timeout, remaining))
                except urllib.error.HTTPError as error:
                    response = error
                with response:
                    status = response.status if hasattr(response, "status") else response.code
                    response_headers = dict(response.headers)
                    while True:
                        if time.monotonic() >= self.deadline:
                            raise CollectionError("REQUEST_DEADLINE_EXCEEDED")
                        # read1 returns one transport read, allowing a wall-clock
                        # deadline check between chunks even on a trickling body.
                        chunk = response.read1(65536)
                        if not chunk:
                            body_complete = True
                            break
                        body.extend(chunk)
                record["received_at"] = stamp(self.clock())
                self.last_received = record["received_at"]
                record["http_status"] = status
                record["response_headers"] = {k.lower(): str(v) for k, v in response_headers.items()
                                              if k.lower() in SAFE_HEADERS}
                if status != 200:
                    raise CollectionError("HTTP_" + str(status))
                data = json.loads(body)
                validator(data)
                record["status"] = "VALIDATED"
                return data
            except Exception as error:
                error_code = str(error) if isinstance(error, CollectionError) else type(error).__name__
                record.update(status="FAILED", error_code=error_code)
                record.setdefault("received_at", stamp(self.clock()))
            finally:
                if status is not None:
                    # Includes partial bodies if a streaming deadline/read fails.
                    # Completeness is explicit; partial bytes never pass parsing.
                    filename = f"responses/{self.count:05d}.body.gz"
                    write_once(self.directory / filename, gzip.compress(bytes(body), mtime=0))
                    record.update(body_file=filename, body_bytes=len(body), body_complete=body_complete,
                                  body_sha256=hashlib.sha256(body).hexdigest(), http_status=status)
                with (self.directory / "requests.jsonl").open("ab") as stream:
                    stream.write(encode(record))
                    stream.flush()
                self.sleeper(0.15)
            if attempt < self.tries:
                self.sleeper(min(2 ** (attempt - 1), 4))
        raise CollectionError("REQUEST_FAILED_" + str(error_code))


def bp_key(repo):
    value = os.environ.get("BETTINGPROS_API_KEY")
    if value:
        return value
    # Reuse the public client key already committed by this repository; never
    # import the live pipeline, execute its code, or print the key.
    tree = ast.parse((repo / "wnba/src/live_pipeline.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "KEY" for t in node.targets):
            value = ast.literal_eval(node.value)
            if isinstance(value, str) and value:
                return value
    raise CollectionError("MISSING_BETTINGPROS_KEY")


def validate_bp_events(data):
    for event in objects(data, "events"):
        if not isinstance(event.get("id"), (str, int)) or not parse_time(event.get("scheduled")):
            raise CollectionError("SCHEMA_EVENT_ID_CLOCK")
    pagination = data.get("_pagination") or {}
    if int(pagination.get("total_pages") or 1) > 1:
        raise CollectionError("EVENTS_PAGINATION_REQUIRED")


def validate_bp_offers(data, event_id=None, market_id=None):
    offers = objects(data, "offers")
    page = data.get("_pagination")
    if not isinstance(page, dict) or not isinstance(page.get("total_pages"), int):
        raise CollectionError("SCHEMA_OFFER_PAGINATION")
    if not 0 <= page["total_pages"] <= 10:
        raise CollectionError("OFFER_PAGE_LIMIT")
    for offer in offers:
        if offer.get("id") is None or offer.get("player_id") is None:
            raise CollectionError("SCHEMA_OFFER_ID")
        if event_id is not None and str(offer.get("event_id")) != str(event_id):
            raise CollectionError("OFFER_EVENT_MISMATCH")
        if market_id is not None and offer.get("market_id") != market_id:
            raise CollectionError("OFFER_MARKET_MISMATCH")
        for selection in objects(offer, "selections"):
            if selection.get("selection") not in ("over", "under"):
                raise CollectionError("SCHEMA_OFFER_SIDE")
            for book in objects(selection, "books"):
                if not isinstance(book.get("id"), int):
                    raise CollectionError("SCHEMA_BOOK_ID")
                for line in objects(book, "lines"):
                    if line.get("active") is True and line.get("main") is True and line.get("is_off") is False:
                        if not finite(line.get("line")) or not finite(line.get("cost")) or abs(line["cost"]) < 100:
                            raise CollectionError("SCHEMA_ACTIVE_PRICE")


def implied(cost):
    return -cost / (100 - cost) if cost < 0 else 100 / (cost + 100)


def quote_coverage(offers, now):
    """Count actual coherent pairs, never cross-book or cross-line mixtures."""
    pairs, fresh, stale, missing_clock, any_ids = {}, {}, {}, {}, set()
    candidates = {}
    for offer in offers:
        if offer.get("active") is False:
            continue
        sides = {}
        for selection in offer["selections"]:
            if selection.get("active") is False:
                continue
            for book in selection["books"]:
                for line in book["lines"]:
                    if line.get("active") is True and line.get("main") is True and line.get("is_off") is False:
                        sides.setdefault(book["id"], {}).setdefault(selection["selection"], []).append(line)
        identity = (str(offer["event_id"]), str(offer["player_id"]), int(offer["market_id"]))
        for book, side in sides.items():
            # Multiple active-main lines, including different lines, are ambiguous.
            if len(side.get("over", [])) != 1 or len(side.get("under", [])) != 1:
                continue
            over, under = side["over"][0], side["under"][0]
            vig = implied(over["cost"]) + implied(under["cost"])
            if over["line"] != under["line"] or not 1.0 <= vig <= 1.15:
                continue
            candidates.setdefault((identity, book), []).append((over, under))
    for (identity, book), rows in candidates.items():
        # Duplicate provider offer identities cannot supply a unique executable pair.
        if len(rows) != 1:
            continue
        over, under = rows[0]
        any_ids.add(identity)
        pairs.setdefault(book, set()).add(identity)
        clocks = [parse_time(over.get("updated")), parse_time(under.get("updated"))]
        if any(x is None for x in clocks):
            missing_clock.setdefault(book, set()).add(identity)
        elif any((now - x).total_seconds() < -60 or (now - x).total_seconds() > 7200 for x in clocks):
            stale.setdefault(book, set()).add(identity)
        else:
            fresh.setdefault(book, set()).add(identity)
    return {"offers": len(offers), "any_book_coherent_pairs": len(any_ids),
            "books": {str(book): {"name": BOOKS.get(book, "unmapped"),
                       "coherent_pairs": len(pairs.get(book, set())),
                       "fresh_pairs": len(fresh.get(book, set())),
                       "stale_pairs": len(stale.get(book, set())),
                       "missing_quote_clock_pairs": len(missing_clock.get(book, set()))}
                      for book in sorted(set(pairs) | set(BOOKS))},
            "freshness_rule": "Both provider updated clocks within 2h; <=60s future tolerance. Receipt freshness is separate."}


def collect_bettingpros(repo, recorder, now):
    headers = {"x-api-key": bp_key(repo)}
    params = {"sport": "WNBA", "start": now.date().isoformat(),
              "end": (now.date() + dt.timedelta(days=1)).isoformat()}
    data = recorder.get(BP + "/events", params, validate_bp_events, headers)
    events = [e for e in data["events"] if e.get("status") != "closed" and parse_time(e["scheduled"]) > now]
    if len(data["events"]) >= 200 or len(events) > 20:
        raise CollectionError("EVENT_LIMIT_OR_TRUNCATION")
    coverage, missing, unfresh = {}, [], []
    for name, market_id in MARKETS.items():
        all_offers = []
        for event in events:
            params = {"sport": "WNBA", "market_id": market_id, "event_id": event["id"], "location": "ALL"}
            validate = lambda d: validate_bp_offers(d, event["id"], market_id)
            first = recorder.get(BP + "/offers", params, validate, headers)
            offers, pages = list(first["offers"]), max(1, first["_pagination"]["total_pages"])
            for page in range(2, pages + 1):
                data = recorder.get(BP + "/offers", dict(params, page=page), validate, headers)
                if max(1, data["_pagination"]["total_pages"]) != pages:
                    raise CollectionError("PAGINATION_CHANGED_DURING_CAPTURE")
                offers.extend(data["offers"])
            ids = [str(o["id"]) for o in offers]
            if len(ids) != len(set(ids)):
                raise CollectionError("DUPLICATE_OFFER_PAGINATION")
            total = first["_pagination"].get("total_items")
            if total is not None and len(offers) != total:
                raise CollectionError("INCOMPLETE_OFFER_PAGINATION")
            all_offers.extend(offers)
        coverage[name] = quote_coverage(all_offers, now)
        if events and not coverage[name]["any_book_coherent_pairs"]:
            missing.append(name)
        if events and not any(book["fresh_pairs"] for book in coverage[name]["books"].values()):
            unfresh.append(name)
    status = "NO_EVENTS" if not events else "DEGRADED" if missing or unfresh else "OK"
    if events and all(not x["offers"] for x in coverage.values()):
        status = "FAILED"
    return {"status": status, "upcoming_events": len(events), "event_window": params if not events else {
                "start": now.date().isoformat(), "end": (now.date() + dt.timedelta(days=1)).isoformat()},
            "markets": coverage, "missing_market_coverage": missing,
            "no_fresh_market_coverage": unfresh,
            "zero_data_failure": status == "FAILED", "received_at": recorder.last_received,
            "scope": "Scheduled future WNBA games today/tomorrow UTC; eight existing core and combination markets. No model or pick gates run."}


def legacy_selector(repo):
    source = repo / "cricket/src/fetch_polymarket.py"
    tree = ast.parse(source.read_text())
    nodes = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name == "is_match_winner") or
             (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "PROP_WORDS" for t in n.targets))]
    if len(nodes) != 2:
        raise CollectionError("LEGACY_SELECTOR_NOT_FOUND")
    namespace = {"json": json}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), namespace)
    return namespace["is_match_winner"], hashlib.sha256(source.read_bytes()).hexdigest()


def validate_gamma(data):
    for event in objects(data, "events"):
        if event.get("id") is None:
            raise CollectionError("SCHEMA_GAMMA_EVENT")
        for market in objects(event, "markets"):
            validate_market(market)
    if data.get("next_cursor") is not None and not isinstance(data["next_cursor"], str):
        raise CollectionError("SCHEMA_GAMMA_CURSOR")


def validate_market(market):
    if not isinstance(market, dict) or market.get("id") is None or not isinstance(market.get("closed"), bool):
        raise CollectionError("SCHEMA_GAMMA_MARKET")
    for field in ("outcomes", "clobTokenIds"):
        if not isinstance(market.get(field), str):
            raise CollectionError("SCHEMA_GAMMA_" + field)
        decoded = json.loads(market[field])
        if not isinstance(decoded, list) or any(not isinstance(x, str) for x in decoded):
            raise CollectionError("SCHEMA_GAMMA_" + field)


def validate_history(data):
    for point in objects(data, "history"):
        if not isinstance(point.get("t"), int) or isinstance(point["t"], bool) or point["t"] < 0:
            raise CollectionError("SCHEMA_HISTORY_TIME")
        if not finite(point.get("p")) or not 0 <= point["p"] <= 1:
            raise CollectionError("SCHEMA_HISTORY_PRICE")


def compact_market(market):
    result = {k: market.get(k) for k in ("id", "question", "outcomes", "clobTokenIds", "closed", "closedTime",
            "startDate", "acceptingOrdersTimestamp", "endDate", "umaResolutionStatus")}
    result["id"] = str(market["id"])
    return result


def bootstrap(repo):
    import pandas as pd
    directory = repo / "cricket/data/raw/polymarket"
    if not (directory / "markets.parquet").exists() or not (directory / "prices.parquet").exists():
        raise CollectionError("CANONICAL_BOOTSTRAP_MISSING")
    markets = pd.read_parquet(directory / "markets.parquet")
    prices = pd.read_parquet(directory / "prices.parquet", columns=["market_id", "t", "p"])
    metadata = {}
    for row in markets.to_dict("records"):
        metadata[str(row["market_id"])] = {"id": str(row["market_id"]), "question": row.get("question"),
            "outcomes": row.get("outcomes"), "clobTokenIds": row.get("clob_token_ids"), "closed": bool(row["closed"]),
            "closedTime": row.get("closed_time"), "startDate": row.get("start_date"),
            "acceptingOrdersTimestamp": row.get("accepting_orders_ts"), "endDate": row.get("end_date"),
            "umaResolutionStatus": row.get("uma_status")}
    # Provider parquet nulls must not escape into strict JSON state.
    for market in metadata.values():
        for key, value in market.items():
            if isinstance(value, float) and not math.isfinite(value):
                market[key] = None
    latest = {str(k): int(v) for k, v in prices.groupby("market_id").t.max().items()}
    recent = {}
    for key, group in prices.groupby("market_id"):
        tail = group[group.t >= latest[str(key)] - 10800]
        recent[str(key)] = [[int(t), float(p)] for t, p in tail[["t", "p"]].itertuples(index=False, name=None)]
    return {"markets": metadata, "latest": latest, "recent_observations": recent, "bootstrap": {
        name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in ("markets.parquet", "prices.parquet")}}


def resolved_sample(markets, now, limit=5):
    """One stable market in each closure-age bucket; closure is NOT resolution time."""
    edges = (7, 30, 90, 365, float("inf"))
    selected = []
    for lo, hi in zip((0,) + edges[:-1], edges):
        eligible = []
        for market in markets.values():
            closed = parse_time(market.get("closedTime"))
            if market.get("closed") is True and market.get("umaResolutionStatus") == "resolved" and closed:
                age = (now - closed).total_seconds() / 86400
                if lo <= age < hi:
                    eligible.append(market)
        if eligible:
            selected.append(min(eligible, key=lambda m: hashlib.sha256(str(m["id"]).encode()).hexdigest()))
    return selected[:min(5, limit)]


def ordered_markets(tasks, attempts, recovery):
    """Unvisited/oldest attempted markets first; price inactivity cannot starve IDs."""
    def key(market):
        identity = str(market["id"])
        observed = attempts.get(identity) or recovery.get(identity, {}).get("received_at")
        return (parse_time(observed) or dt.datetime.min.replace(tzinfo=UTC), identity)
    return sorted(tasks, key=key)


def collect_polymarket(repo, recorder, now, previous, probe=False, max_tasks=350, recovery=None, attempts=None):
    selector, selector_sha = legacy_selector(repo)
    state = json.loads(json.dumps(previous if previous else bootstrap(repo)))
    for key, checkpoint in (recovery or {}).items():
        state["markets"][key] = checkpoint["market"]
        state["latest"][key] = checkpoint["latest"]
        state.setdefault("collected_through", {})[key] = checkpoint.get("collected_through", checkpoint["latest"])
        state.setdefault("recent_observations", {})[key] = checkpoint["recent_observations"]
        state.setdefault("pending_backfills", {})[key] = checkpoint["pending_backfills"]
    metadata, latest = state["markets"], state["latest"]
    collected_through = state.setdefault("collected_through", dict(latest))
    previously_known_ids = set(metadata)
    pending = state.setdefault("pending_backfills", {})
    prior_open_ids = {str(k) for k, market in metadata.items() if not market["closed"]}
    recent = state.setdefault("recent_observations", {})
    selected, events_count = {}, 0
    for closed in ("false", "true"):
        params = {"tag_slug": "cricket", "closed": closed, "limit": 500}
        if closed == "true":
            # Discovery is a recent end-date window, not proof of closure or eligibility.
            params["end_date_min"] = stamp(now - dt.timedelta(days=14))
        cursors = set()
        for page in range(30):
            data = recorder.get(GAMMA + "/events/keyset", params, validate_gamma)
            events_count += len(data["events"])
            for event in data["events"]:
                for market in event["markets"]:
                    if selector(market):
                        selected[str(market["id"])] = compact_market(market)
            cursor = data.get("next_cursor")
            if not cursor:
                break
            if cursor in cursors:
                raise CollectionError("GAMMA_REPEATED_CURSOR")
            cursors.add(cursor)
            params = dict(params, after_cursor=cursor)
        else:
            raise CollectionError("GAMMA_PAGE_LIMIT")
    # Previously open markets may disappear from the active list when closed.
    # Fetch their exact IDs; never delete them or interpret disappearance as closure.
    not_relisted = [m for k, m in metadata.items() if not m["closed"] and k not in selected]
    if len(not_relisted) > max_tasks:
        raise CollectionError("UNRELISTED_MARKET_TASK_LIMIT")
    for old in sorted(not_relisted, key=lambda m: m["id"]):
        market = recorder.get(GAMMA + "/markets/" + old["id"], {}, validate_market)
        if str(market["id"]) != old["id"]:
            raise CollectionError("MARKET_ID_MISMATCH")
        selected[old["id"]] = compact_market(market)
    for key, intervals in pending.items():
        if intervals and key not in selected:
            selected[key] = metadata[key]
    metadata.update(selected)
    write_once(recorder.directory / "metadata.json.gz", gzip.compress(encode(selected), mtime=0))
    tasks = [m for m in selected.values() if not m["closed"] or m["id"] in prior_open_ids or pending.get(m["id"]) or
             m["id"] not in previously_known_ids or m["id"] not in latest]
    tasks = ordered_markets(tasks, attempts or {}, recovery or {})
    skipped_closed = len(selected)-len(tasks)
    backlog = [m["id"] for m in tasks[max_tasks:]]
    tasks = tasks[:max_tasks]
    observations, statuses, revisions = set(), [], []
    cutoff = int(now.timestamp())

    def checkpoint(market_id, requested_start, requested_end):
        payload = {"schema": "market-interval-checkpoint-v1", "market_id": market_id,
                   "market": metadata[market_id], "latest": latest[market_id],
                   "collected_through": collected_through.get(market_id, latest[market_id]),
                   "recent_observations": recent.get(market_id, []),
                   "pending_backfills": pending.get(market_id, []),
                   "requested_start": requested_start, "requested_end": requested_end,
                   "request_sequence": recorder.count, "received_at": recorder.last_received}
        with (recorder.directory / "checkpoints.jsonl").open("ab") as stream:
            stream.write(encode(payload))
            stream.flush()
            os.fsync(stream.fileno())

    def fetch_range(market_id, token, lower, upper, purpose):
        data = recorder.get(CLOB + "/prices-history", {"market": token, "startTs": lower,
                            "endTs": upper, "fidelity": 10}, validate_history)
        within = [p for p in data["history"] if lower <= p["t"] <= upper]
        observations.update((market_id, p["t"], p["p"]) for p in data["history"])
        known = {}
        for timestamp, price in recent.get(market_id, []):
            known.setdefault(timestamp, set()).add(price)
        for point in within:
            old_prices = known.get(point["t"], set())
            if old_prices and point["p"] not in old_prices:
                revisions.append({"market_id": market_id, "t": point["t"], "previous_prices": sorted(old_prices),
                                  "received_price": point["p"], "received_at": recorder.last_received})
            known.setdefault(point["t"], set()).add(point["p"])
        if within:
            latest[market_id] = max(latest.get(market_id, 0), max(p["t"] for p in within))
            collected_through[market_id] = max(collected_through.get(market_id, 0), upper)
            recent[market_id] = [[t, p] for t, values in sorted(known.items()) if t >= latest[market_id]-10800
                                 for p in sorted(values)]
        statuses.append({"market_id": market_id, "purpose": purpose, "requested_start": lower,
                         "requested_end": upper, "received_at": recorder.last_received,
                         "returned_rows": len(data["history"]), "in_range_rows": len(within),
                         "latest_in_range_timestamp": max((point["t"] for point in within), default=None),
                         "out_of_range_rows": len(data["history"])-len(within),
                         "status": "OK" if within else "EMPTY_HISTORY"})
        return bool(within)

    def add_gap(intervals, start, end):
        merged = []
        for a, b in sorted(intervals + [[start, end]]):
            if merged and a <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        return merged

    def collect_market(market):
        market_id = market["id"]
        tokens = json.loads(market["clobTokenIds"])
        start = parse_time(market.get("acceptingOrdersTimestamp")) or parse_time(market.get("startDate"))
        if not tokens or start is None:
            raise CollectionError("MARKET_HISTORY_INPUT_MISSING")
        prior = latest.get(market_id)
        if prior is not None and prior > cutoff:
            raise CollectionError("SAVED_PRICE_CLOCK_IN_FUTURE")
        captured_end = collected_through.get(market_id, prior)
        wanted = max(int(start.timestamp()), (captured_end - 7200) if captured_end is not None else int(start.timestamp()))
        upper = min(cutoff, int(((parse_time(market.get("closedTime")) or now) + dt.timedelta(hours=1)).timestamp()))
        lower = max(wanted, upper - 13 * 86400)
        intervals = pending.setdefault(market_id, [])
        if lower > wanted:
            # Merge overlapping gaps without forgetting already pending work.
            pending[market_id] = intervals = add_gap(intervals, wanted, lower)
        backfill_planned = bool(intervals)
        if lower < upper:
            if fetch_range(market_id, tokens[0], lower, upper, "incremental"):
                checkpoint(market_id, lower, upper)
            else:
                # A successful earlier backfill must not hide a missing terminal
                # interval when it checkpoints a now-closed market.
                pending[market_id] = intervals = add_gap(intervals, lower, upper)
        # One bounded earlier interval per market/run makes unresolved gaps
        # converge while current capture continues. Empty intervals remain pending.
        if backfill_planned:
            backfill_start, gap_end = intervals[0]
            backfill_end = min(gap_end, backfill_start + 13 * 86400)
            if fetch_range(market_id, tokens[0], backfill_start, backfill_end, "pending_backfill"):
                if backfill_end >= gap_end:
                    intervals.pop(0)
                else:
                    intervals[0][0] = backfill_end
                checkpoint(market_id, backfill_start, backfill_end)
    task_errors = []
    for position, market in enumerate(tasks):
        if time.monotonic() >= getattr(recorder, "deadline", float("inf")) or recorder.count >= getattr(recorder, "max_requests", float("inf")):
            backlog.extend(m["id"] for m in tasks[position:])
            break
        attempt = {"schema": "market-attempt-v1", "market_id": market["id"], "attempted_at": stamp(utcnow())}
        with (recorder.directory / "market-attempts.jsonl").open("ab") as stream:
            stream.write(encode(attempt))
            stream.flush()
        try:
            collect_market(market)
        except Exception as error:
            code = str(error) if isinstance(error, CollectionError) else type(error).__name__
            task_errors.append({"market_id": market["id"], "error_code": code, "status": "FAILED"})
    write_once(recorder.directory / "market-failures.json", encode(task_errors))
    gaps = [{"market_id": key, "wanted_start": start, "uncollected_before": end}
            for key, intervals in sorted(pending.items()) for start, end in intervals]
    write_once(recorder.directory / "prices.jsonl.gz", gzip.compress(b"".join(encode({"market_id": m, "t": t, "p": p})
               for m, t, p in sorted(observations)), mtime=0))
    write_once(recorder.directory / "history-tasks.json", encode(statuses))
    write_once(recorder.directory / "price-revisions.jsonl.gz", gzip.compress(b"".join(encode(row) for row in revisions), mtime=0))
    probes = []
    if probe:
        for old in resolved_sample(metadata, now):
            market = recorder.get(GAMMA + "/markets/" + old["id"], {}, validate_market)
            if str(market["id"]) != old["id"]:
                raise CollectionError("PROBE_MARKET_ID_MISMATCH")
            end = parse_time(market.get("closedTime"))
            tokens = json.loads(market["clobTokenIds"])
            if not market["closed"] or market.get("umaResolutionStatus") != "resolved" or not end or not tokens:
                probes.append({"market_id": old["id"], "status": "RESOLVED_STATUS_NOT_CONFIRMED"})
                continue
            # Fixed seven-day pre-closure probe; no full multi-year re-download.
            lower, upper = int((end-dt.timedelta(days=7)).timestamp()), min(cutoff, int(end.timestamp()) + 3600)
            data = recorder.get(CLOB + "/prices-history", {"market": tokens[0], "startTs": lower,
                                "endTs": upper, "fidelity": 10}, validate_history)
            in_range = [p for p in data["history"] if lower <= p["t"] <= upper]
            probes.append({"market_id": old["id"], "status": "RETURNED" if in_range else "EMPTY_HISTORY",
                           "closed_age_days": (now-end).total_seconds()/86400, "rows": len(in_range),
                           "out_of_range_rows": len(data["history"])-len(in_range),
                           "requested_start": lower, "requested_end": upper, "exact_archive_match_verified": False,
                           "closed_time": stamp(end), "resolution_time_verified": False,
                           "received_at": recorder.last_received})
    empty = sum(s["status"] == "EMPTY_HISTORY" for s in statuses)
    status = "NO_EVENTS" if not tasks else "FAILED" if not statuses or empty == len(statuses) else "DEGRADED" if empty or gaps else "OK"
    if task_errors:
        status = "FAILED"
    elif backlog and status != "FAILED":
        status = "DEGRADED"
    if status != "FAILED" and any(p["status"] != "RETURNED" for p in probes):
        status = "DEGRADED"
    # A completely empty API discovery over a populated archive is an outage signal.
    if not events_count and not selected and metadata:
        status = "FAILED"
    report = {"status": status, "metadata_events_returned": events_count, "selected_markets": len(selected),
              "history_tasks": len(statuses), "empty_histories": empty, "price_observations": len(observations),
              "closed_markets_with_existing_history_skipped": skipped_closed,
              "history_backlog": backlog, "failed_history_tasks": task_errors,
              "revised_price_observations": len(revisions),
              "out_of_range_rows": sum(s["out_of_range_rows"] for s in statuses),
              "latest_price_timestamp": max((s["latest_in_range_timestamp"] for s in statuses
                                             if s["latest_in_range_timestamp"] is not None), default=None),
              "backfill_gaps": gaps, "resolved_market_probes": probes, "received_at": recorder.last_received,
              "selector": "Existing is_match_winner unchanged; its textual filter admits some non-winner props.",
              "selector_source_sha256": selector_sha, "protected_arm_scores_run": 0,
              "scope": "All active cricket discovery, recent end-date discovery, previously open ID refresh; 13-day bounded histories with 2-hour overlap. Closure does not establish prospective eligibility."}
    return report, state


def verified_recovery(output, references, last_success_at):
    """Verify immutable checkpoint bytes before using partial-run progress."""
    cache, recovered = {}, {}
    for key, reference in references.items():
        path = (output / reference["file"]).resolve()
        if not path.is_relative_to(output / "runs") or path.name != "checkpoints.jsonl":
            raise CollectionError("CHECKPOINT_PATH_INVALID")
        if path not in cache:
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != reference["file_sha256"]:
                raise CollectionError("CHECKPOINT_FILE_CHANGED")
            cache[path] = {hashlib.sha256(line + b"\n").hexdigest(): json.loads(line)
                           for line in raw.splitlines()}
        payload = cache[path].get(reference["checkpoint_sha256"])
        if not payload or payload.get("market_id") != key or payload.get("schema") != "market-interval-checkpoint-v1":
            raise CollectionError("CHECKPOINT_NOT_FOUND")
        if last_success_at is None or parse_time(payload["received_at"]) > parse_time(last_success_at):
            recovered[key] = payload
    return recovered


def checkpoint_references(output, directory):
    path = directory / "checkpoints.jsonl"
    if not path.exists():
        return {}
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    result = {}
    for line in raw.splitlines():
        payload = json.loads(line)
        result[str(payload["market_id"])] = {"file": str(path.relative_to(output)),
            "file_sha256": digest, "checkpoint_sha256": hashlib.sha256(line + b"\n").hexdigest(),
            "received_at": payload["received_at"], "latest": payload["latest"]}
    return result


def attempt_references(output, directory):
    path = directory / "market-attempts.jsonl"
    if not path.exists():
        return {}
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    result = {}
    for line in raw.splitlines():
        payload = json.loads(line)
        result[str(payload["market_id"])] = {"file": str(path.relative_to(output)), "file_sha256": digest,
            "attempt_sha256": hashlib.sha256(line + b"\n").hexdigest(), "attempted_at": payload["attempted_at"]}
    return result


def verified_attempts(output, references):
    cache, result = {}, {}
    for key, reference in references.items():
        path = (output / reference["file"]).resolve()
        if not path.is_relative_to(output / "runs") or path.name != "market-attempts.jsonl":
            raise CollectionError("ATTEMPT_PATH_INVALID")
        if path not in cache:
            raw = path.read_bytes()
            cache[path] = (hashlib.sha256(raw).hexdigest(), {
                hashlib.sha256(line + b"\n").hexdigest(): json.loads(line) for line in raw.splitlines()})
        digest, rows = cache[path]
        if digest != reference["file_sha256"]:
            raise CollectionError("ATTEMPT_FILE_CHANGED")
        payload = rows.get(reference["attempt_sha256"])
        if not payload or payload.get("schema") != "market-attempt-v1" or payload.get("market_id") != key:
            raise CollectionError("ATTEMPT_NOT_FOUND")
        if payload.get("attempted_at") != reference["attempted_at"] or parse_time(payload["attempted_at"]) is None:
            raise CollectionError("ATTEMPT_CLOCK_INVALID")
        result[key] = payload["attempted_at"]
    return result


def collect(repo, output, run_id, source="all", probe=False, *, now=None, recorder_factory=Recorder):
    repo, output = Path(repo).resolve(), Path(output).resolve()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}", run_id):
        raise ValueError("Unsafe run ID")
    canonical = [repo / "cricket/data/raw/polymarket", repo / "wnba/data/raw/bp"]
    if any(output == p or output.is_relative_to(p) for p in canonical) or any(part == "live" for part in output.parts):
        raise ValueError("Collection output must be separate from canonical and live archives")
    output.mkdir(parents=True, exist_ok=True)
    directory = output / "runs" / run_id
    if (directory / "report.json").exists():
        return json.loads((directory / "report.json").read_text())
    lock = (output / ".collection.lock").open("ab")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise CollectionError("COLLECTOR_ALREADY_RUNNING") from None
    now = now or utcnow()
    try:
        directory.mkdir(parents=True, exist_ok=False)
        write_once(directory / "started.json", encode({"schema": "collection-start-v1", "run_id": run_id,
                   "started_at": stamp(now), "source": source, "probe_resolved": probe}))
        state = json.loads((output / "state.json").read_text()) if (output / "state.json").exists() else {"schema": "collection-state-v1", "sources": {}}
        sources = {}
        for name in ("bettingpros", "polymarket"):
            if source not in ("all", name):
                continue
            recorder = recorder_factory(directory / name)
            try:
                if name == "bettingpros":
                    result = collect_bettingpros(repo, recorder, now)
                else:
                    prior_source = state["sources"].get(name, {})
                    recovery = verified_recovery(output, prior_source.get("recovery", {}), prior_source.get("last_success_at"))
                    attempts = verified_attempts(output, prior_source.get("market_attempts", {}))
                    result, new_state = collect_polymarket(repo, recorder, now, prior_source.get("cursor"), probe, recovery=recovery, attempts=attempts)
                    if result["status"] in ("OK", "NO_EVENTS"):
                        state["sources"].setdefault(name, {})["cursor"] = new_state
            except Exception as error:
                result = {"status": "FAILED", "error_code": str(error) if isinstance(error, CollectionError) else type(error).__name__,
                          "received_at": recorder.last_received}
            if name == "polymarket":
                references = checkpoint_references(output, recorder.directory)
                if references:
                    state["sources"].setdefault(name, {}).setdefault("recovery", {}).update(references)
                    result["verified_market_checkpoints"] = len(references)
                attempts = attempt_references(output, recorder.directory)
                if attempts:
                    state["sources"].setdefault(name, {}).setdefault("market_attempts", {}).update(attempts)
            result.update(requests=recorder.count, checked_at=stamp(utcnow()), max_age_seconds=10800)
            if result["status"] in ("OK", "NO_EVENTS"):
                state["sources"].setdefault(name, {})["last_success_at"] = result["checked_at"]
            result["last_success_at"] = state["sources"].get(name, {}).get("last_success_at")
            result["last_success_age_seconds"] = ((utcnow()-parse_time(result["last_success_at"])).total_seconds()
                                                   if result["last_success_at"] else None)
            sources[name] = result
        report = {"schema": "collection-health-v1", "run_id": run_id, "started_at": stamp(now),
                  "completed_at": stamp(utcnow()), "created_at": stamp(utcnow()), "sources": sources,
                  "status": "FAILED" if any(x["status"] == "FAILED" for x in sources.values()) else
                            "DEGRADED" if any(x["status"] == "DEGRADED" for x in sources.values()) else "OK",
                  "model_scoring_run": False, "live_files_changed": False,
                  "files": {str(p.relative_to(directory)): {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "bytes": p.stat().st_size}
                            for p in sorted(directory.rglob("*")) if p.is_file()}}
        write_once(directory / "report.json", encode(report))
        replace_json(output / "state.json", state)
        replace_json(output / "health.json", report)
        return report
    finally:
        # Kernel releases this lock even after a killed runner; old incomplete
        # run IDs still cannot be reused or overwritten.
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path, default=Path("research/operations/data"))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--source", choices=("all", "bettingpros", "polymarket"), default="all")
    parser.add_argument("--probe-resolved", action="store_true")
    args = parser.parse_args()
    report = collect(args.repo, args.output, args.run_id, args.source, args.probe_resolved)
    print(json.dumps({"run_id": report["run_id"], "status": report["status"],
                      "sources": {k: v["status"] for k, v in report["sources"].items()}}, sort_keys=True))
    return 1 if report["status"] == "FAILED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
