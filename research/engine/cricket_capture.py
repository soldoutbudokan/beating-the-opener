"""Run the existing cricket fetcher in isolation and preserve every response.

This is data collection only. It does not run a model or change the Mac job.
The original parquet files are read-only inputs. A new capture directory is
required; API failures stop the run rather than masquerading as empty pages.
"""
from __future__ import annotations

import argparse
import contextlib
from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime as dt
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import threading
import time

RESUME_HELPER = Path(__file__).resolve().parents[2] / "cricket/research/capture_audit/resume.py"
_resume_spec = importlib.util.spec_from_file_location("cricket_capture_resume", RESUME_HELPER)
_resume_module = importlib.util.module_from_spec(_resume_spec)
_resume_spec.loader.exec_module(_resume_module)
cache_key = _resume_module.cache_key
captured_observations = _resume_module.captured_observations
load_response_records = _resume_module.load_response_records
load_resume_snapshot = _resume_module.load_resume_snapshot
validate_history_payload = _resume_module.validate_history_payload


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def preserve_prices(old_rows, fetched_rows):
    """Union exact observations; expose revisions without replacing old data."""
    observations = set(old_rows)
    observations.update(fetched_rows)
    values = {}
    for market, timestamp, value in observations:
        values.setdefault((market, timestamp), set()).add(value)
    conflicts = sum(len(v) > 1 for v in values.values())
    return sorted(observations), conflicts


class Recorder:
    def __init__(self, destination, request, headers):
        self.destination = Path(destination)
        self.request = request
        self.headers = headers
        self.count = 0
        self.lock = threading.Lock()

    def __call__(self, url, params=None, tries=3):
        last_error = None
        for attempt in range(1, tries + 1):
            record = {"url": url, "params": params,
                      "requested_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                      "attempt": attempt}
            try:
                response = self.request(url, params=params,
                                        headers=self.headers, timeout=30)
                record["observed_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
                record.update(status=response.status_code, body=response.text,
                              response_headers={k: v for k, v in response.headers.items()
                                                if k.lower() in {"date", "last-modified", "warning", "deprecation", "sunset"}})
                record["body_sha256"] = hashlib.sha256(response.content).hexdigest()
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, (dict, list)):
                    raise ValueError("API returned a non-container JSON value")
                if url.endswith("/prices-history"):
                    validate_history_payload(data)
                if url.endswith("/events") and (not isinstance(data, list) or not all(isinstance(event, dict) for event in data)):
                    raise ValueError("Events response must be a list of event objects")
                return data
            except Exception as error:
                last_error = error
                record["error"] = f"{type(error).__name__}: {error}"
                record.setdefault("observed_at", dt.datetime.now(dt.timezone.utc).isoformat())
            finally:
                with self.lock:
                    with gzip.open(self.destination, "at", encoding="utf-8") as stream:
                        stream.write(json.dumps(record, sort_keys=True) + "\n")
                    self.count += 1
            if attempt < tries:
                time.sleep(attempt)
        raise RuntimeError(f"API request failed after {tries} attempts: {url}: {last_error}")


def check_resolved(fetcher, recorder, market_id, archived_prices):
    market = recorder(f"{fetcher.GAMMA}/markets/{market_id}")
    result = {"market_id": str(market_id), "question": market.get("question"),
              "closed": market.get("closed"),
              "resolution_status": market.get("umaResolutionStatus"),
              "closed_time": market.get("closedTime"), "status": "UNVERIFIED"}
    if str(market.get("id")) != str(market_id):
        raise ValueError("Requested market ID did not match API response")
    if market.get("closed") is not True or market.get("umaResolutionStatus") != "resolved":
        return result
    token = json.loads(market["clobTokenIds"])[0]
    start = fetcher.parse_ts(market.get("acceptingOrdersTimestamp") or market.get("startDate"))
    end = fetcher.parse_ts(market["closedTime"]) + dt.timedelta(hours=1)
    history = fetcher.price_history(token, start.to_pydatetime(), end.to_pydatetime())
    known = archived_prices[archived_prices.market_id.astype(str).eq(str(market_id))]
    old_timestamps = set(known.t.astype(int))
    fetched_timestamps = {int(t) for t, _ in history}
    result.update(token_id=token, requested_start=start.isoformat(), requested_end=end.isoformat(),
                  returned_rows=len(history), archived_rows=len(known),
                  archived_timestamps_recovered=len(old_timestamps & fetched_timestamps),
                  first_timestamp=min(fetched_timestamps, default=None),
                  last_timestamp=max(fetched_timestamps, default=None),
                  status="BACKFILL_AVAILABLE" if history else "EMPTY_HISTORY")
    return result


def parallel_histories(fetcher, rows, now, workers, status_path):
    """Bound independent market requests; retain a status for every requested ID."""
    if not 1 <= workers <= 4:
        raise ValueError("workers must be between 1 and 4")

    def one(row):
        status = {"market_id": str(row.market_id)}
        tokens = json.loads(row.clob_token_ids or "[]")
        if not tokens:
            return [], dict(status, status="SKIPPED_MISSING_TOKEN", rows=0)
        start = fetcher.parse_ts(row.accepting_orders_ts) or fetcher.parse_ts(row.start_date)
        if start is None:
            return [], dict(status, status="SKIPPED_MISSING_START", rows=0)
        end = min((fetcher.parse_ts(row.closed_time) or now) + dt.timedelta(hours=1), now)
        status.update(requested_start=start.isoformat(), requested_end=end.isoformat())
        # The original fetcher's 13-day windows, fidelity and timestamp rules remain.
        points = fetcher.price_history(tokens[0], start, end)
        return [(str(row.market_id), t, p) for t, p in points], dict(
            status, status="RETURNED" if points else "EMPTY_HISTORY", rows=len(points))

    collected, failures, statuses = [], [], []
    with Path(status_path).open("x") as stream, ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(one, row): str(row.market_id) for row in rows}
        for future in as_completed(futures):
            try:
                observations, status = future.result()
                collected.extend(observations)
            except Exception as error:
                status = {"market_id": futures[future], "status": "ERROR", "rows": 0,
                          "error": f"{type(error).__name__}: {error}"}
                failures.append(status)
            statuses.append(status)
            stream.write(json.dumps(status, sort_keys=True) + "\n")
            stream.flush()
    if failures:
        raise RuntimeError(f"{len(failures)} market history tasks failed; see {status_path}")
    return collected, statuses


def capture(repo, output, market_id="3857264", since="2024-05-01", probe_only=False, workers=1, resume_from=None):
    import pandas as pd
    import requests

    repo, output = Path(repo).resolve(), Path(output).resolve()
    raw = repo / "cricket/data/raw/polymarket"
    output.mkdir(parents=True, exist_ok=False)
    source = repo / "cricket/src/fetch_polymarket.py"
    before = {name: sha256(raw / name) for name in ["markets.parquet", "prices.parquet"]}
    manifest = {"schema": "cricket-capture-v1", "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "status": "STARTED", "source_sha256": sha256(source), "original_sha256": before,
                "protected_arm_scores_run": 0, "since": since, "probe_only": probe_only, "workers": workers,
                "wrapper_sha256": sha256(Path(__file__)),
                "resume_helper_sha256": sha256(RESUME_HELPER),
                "runtime": {"python": sys.version.split()[0], "pandas": pd.__version__,
                            "requests": requests.__version__}}
    for origin, name in [(Path(__file__), "capture-wrapper.py"), (RESUME_HELPER, "resume-helper.py"),
                         (source, "source-fetcher.py")]:
        with (output / name).open("xb") as stream:
            stream.write(origin.read_bytes())
    spec = importlib.util.spec_from_file_location("existing_cricket_fetcher", source)
    fetcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fetcher)
    recorder = Recorder(output / "responses.jsonl.gz", requests.get, fetcher.UA)
    fetcher.get = recorder
    resumed_metadata, resumed_as_of, resumed_ids = None, None, None
    inherited_paths = []
    if resume_from:
        prior = Path(resume_from).resolve()
        resumed = load_resume_snapshot(prior, output, before)
        records, inherited_paths = resumed["records"], resumed["response_paths"]
        resumed_metadata = pd.read_parquet(resumed["metadata_path"])
        resumed_ids = resumed["planned_market_ids"]
        resumed_as_of = pd.Timestamp(resumed["price_as_of"])
        cache = {}
        for record in records:
            if record.get("status") == 200 and "error" not in record:
                if record["url"].endswith("/prices-history"):
                    validate_history_payload(json.loads(record["body"]))
                cache.setdefault(cache_key(record["url"], record.get("params")), record)
        if resumed_as_of > pd.Timestamp.now(tz="UTC"):
            raise ValueError("Saved price request ends in the future")
        reuse_lock = threading.Lock()
        def cached_get(url, params=None, tries=3):
            hit = cache.get(cache_key(url, params))
            if hit is None:
                return recorder(url, params, tries)
            with reuse_lock, (output / "reused-requests.jsonl").open("a") as stream:
                stream.write(json.dumps({"url": url, "params": params,
                                         "original_observed_at": hit["observed_at"],
                                         "body_sha256": hit.get("body_sha256")}, sort_keys=True) + "\n")
            return json.loads(hit["body"])
        fetcher.get = cached_get
        manifest["resume"] = {"source_directory": str(prior.relative_to(repo)),
                              "response_files": [str(p.relative_to(output)) for p in inherited_paths],
                              "metadata_sha256": sha256(output / "requested-markets.parquet"),
                              "requested_price_as_of": resumed_as_of.isoformat()}
    old_m, old_p = pd.read_parquet(raw / "markets.parquet"), pd.read_parquet(raw / "prices.parquet")
    (output / "started.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    try:
        manifest["resolved_market_probe"] = check_resolved(fetcher, fetcher.get, market_id, old_p)
        if probe_only:
            manifest["status"] = "PROBE_COMPLETE"
            return manifest
        staging = output / "staging"
        staging.mkdir()
        for name in before:
            shutil.copyfile(raw / name, staging / name)
        fetcher.OUT = str(staging)
        with (output / "fetch.log").open("x") as log, contextlib.redirect_stdout(log):
            if not 1 <= workers <= 4:
                raise ValueError("workers must be between 1 and 4")
            metadata = resumed_metadata if resumed_metadata is not None else fetcher.list_markets(dt.date.fromisoformat(since))
            have = set(old_p.market_id.astype(str))
            still_open = set(old_m[~old_m.closed].market_id.astype(str))
            todo = metadata[~metadata.market_id.isin(have) | metadata.market_id.isin(still_open)]
            planned_ids = list(todo.market_id.astype(str))
            if resumed_ids is not None and planned_ids != resumed_ids:
                raise ValueError("Requested market population changed during resume")
            # These immutable inputs must survive interruption before any worker starts.
            if resumed_metadata is None:
                metadata.to_parquet(output / "requested-markets.parquet", index=False)
            as_of = resumed_as_of if resumed_as_of is not None else pd.Timestamp.now(tz="UTC")
            plan = {"schema": "cricket-fetch-plan-v1", "price_as_of": as_of.isoformat(),
                    "original_sha256": before, "planned_market_ids": planned_ids,
                    "requested_metadata_sha256": sha256(output / "requested-markets.parquet"),
                    "inherited_response_files": [str(p.relative_to(output)) for p in inherited_paths]}
            with (output / "fetch-plan.json").open("x") as stream:
                stream.write(json.dumps(plan, indent=2, sort_keys=True) + "\n")
            metadata.to_parquet(staging / "markets.parquet", index=False)
            observations, statuses = parallel_histories(
                fetcher, list(todo.itertuples()), as_of, workers, output / "market-status.jsonl")
            pd.DataFrame(observations, columns=["market_id", "t", "p"]).to_parquet(
                staging / "prices.parquet", index=False)
            manifest["requested_price_as_of"] = as_of.isoformat()
            manifest["history_tasks"] = len(statuses)
            manifest["history_status_counts"] = {
                name: sum(s["status"] == name for s in statuses)
                for name in sorted({s["status"] for s in statuses})}
        fresh_m = pd.read_parquet(staging / "markets.parquet")
        fresh_p = pd.read_parquet(staging / "prices.parquet")
        old_ids, fresh_ids = set(old_m.market_id.astype(str)), set(fresh_m.market_id.astype(str))
        # Keep old-only market records even if a provider stops listing them.
        markets = pd.concat([old_m[~old_m.market_id.astype(str).isin(fresh_ids)], fresh_m], ignore_index=True)
        token_to_market = {token: str(row.market_id) for row in markets.itertuples()
                           for token in json.loads(row.clob_token_ids or "[]")[:1]}
        response_paths = inherited_paths + ([output / "responses.jsonl.gz"] if (output / "responses.jsonl.gz").exists() else [])
        captured_rows = captured_observations(load_response_records(response_paths), token_to_market)
        captured_rows.update(fresh_p[["market_id", "t", "p"]].itertuples(index=False, name=None))
        rows, conflicts = preserve_prices(
            old_p[["market_id", "t", "p"]].itertuples(index=False, name=None),
            captured_rows)
        prices = pd.DataFrame(rows, columns=["market_id", "t", "p"])
        markets.to_parquet(output / "markets.parquet", index=False)
        prices.to_parquet(output / "prices.parquet", index=False)
        manifest.update(status="COMPLETE_WITH_PRICE_REVISIONS" if conflicts else "COMPLETE",
                        markets_before=len(old_m), markets_after=len(markets),
                        markets_new=len(fresh_ids - old_ids), markets_not_relisted=len(old_ids - fresh_ids),
                        prices_before=len(old_p), prices_after=len(prices),
                        conflicting_price_keys=conflicts,
                        latest_closed_time=str(pd.to_datetime(markets.closed_time, utc=True, format="mixed").max()),
                        latest_price_timestamp=int(prices.t.max()))
        # Staging is disposable; the immutable inputs and completed union survive.
        shutil.rmtree(staging)
        return manifest
    except Exception as error:
        manifest.update(status="FAILED", error=f"{type(error).__name__}: {error}")
        raise
    finally:
        after = {name: sha256(raw / name) for name in before}
        manifest["original_archives_unchanged"] = before == after
        manifest["requests"] = recorder.count
        manifest["completed_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        manifest["capture_sha256"] = {str(p.relative_to(output)): sha256(p) for p in output.rglob("*") if p.is_file()}
        (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        if before != after:
            raise RuntimeError("Original archives changed during capture")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--output", required=True)
    parser.add_argument("--market-id", default="3857264")
    parser.add_argument("--since", default="2024-05-01")
    parser.add_argument("--probe-only", action="store_true")
    parser.add_argument("--workers", type=int, choices=range(1, 5), default=1)
    parser.add_argument("--resume-from", help="Prior interrupted capture; reuse its metadata and saved responses")
    args = parser.parse_args()
    print(json.dumps(capture(args.repo, args.output, args.market_id, args.since, args.probe_only, args.workers, args.resume_from), indent=2))
