"""Verify source preservation and the backfill claim; never score a model."""
import argparse
import datetime as dt
import gzip
import hashlib
import json
from pathlib import Path

import pandas as pd


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(repo, capture):
    raw = repo / "cricket/data/raw/polymarket"
    manifest = json.loads((capture / "manifest.json").read_text())
    if manifest["status"] not in {"COMPLETE", "COMPLETE_WITH_PRICE_REVISIONS"}:
        raise ValueError("Capture did not complete")
    for name, expected in manifest["capture_sha256"].items():
        if digest(capture / name) != expected:
            raise ValueError(f"Capture checksum mismatch: {name}")
    for name, expected in manifest["original_sha256"].items():
        if digest(raw / name) != expected:
            raise ValueError(f"Original archive checksum mismatch: {name}")
    if digest(repo / "cricket/src/fetch_polymarket.py") != manifest["source_sha256"]:
        raise ValueError("Original fetcher source changed")
    if (capture / "capture-wrapper.py").exists() and digest(capture / "capture-wrapper.py") != manifest["wrapper_sha256"]:
        raise ValueError("Executed wrapper snapshot checksum mismatch")
    old_m = pd.read_parquet(raw / "markets.parquet", columns=["market_id", "closed_time", "closed"])
    old_p = pd.read_parquet(raw / "prices.parquet")
    new_m = pd.read_parquet(capture / "markets.parquet", columns=["market_id", "closed_time", "closed", "uma_status", "clob_token_ids"])
    new_p = pd.read_parquet(capture / "prices.parquet")
    old_rows = set(old_p.itertuples(index=False, name=None))
    new_rows = set(new_p.itertuples(index=False, name=None))
    if not old_rows <= new_rows:
        raise ValueError("Old price observations disappeared")
    if not set(old_m.market_id) <= set(new_m.market_id):
        raise ValueError("Old market identities disappeared")
    responses = []
    if (capture / "responses.jsonl.gz").exists():
        with gzip.open(capture / "responses.jsonl.gz", "rt") as stream:
            responses = [json.loads(line) for line in stream]
    if len(responses) != manifest["requests"]:
        raise ValueError("Request count mismatch")
    fresh_requests = len(responses)
    if manifest.get("resume"):
        lineage = manifest["resume"].get("response_files", ["resume-responses.jsonl.gz"])
        for name in lineage:
            with gzip.open(capture / name, "rt") as stream:
                responses += [json.loads(line) for line in stream]
    token_to_market = {token: str(row.market_id) for row in new_m.itertuples()
                       for token in json.loads(row.clob_token_ids or "[]")[:1]}
    archived_response_rows = set()
    after_end = before_start = out_of_range_requests = 0
    latest_requested_end = latest_returned = 0
    for response in responses:
        if response["url"].endswith("/prices-history") and response.get("status") == 200 and "error" not in response:
            body = json.loads(response["body"])
            if not isinstance(body.get("history"), list):
                raise ValueError("A successful price response lacks a history array")
            market = token_to_market.get((response.get("params") or {}).get("market"))
            if market is None:
                raise ValueError("A captured history cannot be mapped to a market")
            archived_response_rows.update((market, point["t"], point["p"]) for point in body["history"])
            params = response["params"]
            early = sum(point["t"] < params["startTs"] for point in body["history"])
            late = sum(point["t"] > params["endTs"] for point in body["history"])
            before_start += early
            after_end += late
            out_of_range_requests += bool(early or late)
            latest_requested_end = max(latest_requested_end, params["endTs"])
            latest_returned = max(latest_returned, max([point["t"] for point in body["history"]], default=0))
    if not archived_response_rows <= new_rows:
        raise ValueError("Captured price observations disappeared from the completed union")
    status_path = capture / "market-status.jsonl"
    task_counts = {}
    requested_ids = set(new_m.market_id.astype(str))
    if status_path.exists():
        statuses = [json.loads(line) for line in status_path.read_text().splitlines()]
        have = set(old_p.market_id.astype(str))
        still_open = set(old_m[~old_m.closed].market_id.astype(str))
        snapshot = capture / "requested-markets.parquet"
        if not snapshot.exists():
            snapshot = capture / "resume-markets.parquet"
        if not snapshot.exists():
            raise ValueError("Requested metadata snapshot is missing")
        requested_metadata = pd.read_parquet(snapshot, columns=["market_id"])
        requested_ids = set(requested_metadata.market_id.astype(str))
        expected_ids = set(requested_metadata[~requested_metadata.market_id.isin(have) | requested_metadata.market_id.isin(still_open)].market_id.astype(str))
        actual_ids = [s["market_id"] for s in statuses]
        if len(actual_ids) != len(set(actual_ids)) or set(actual_ids) != expected_ids:
            raise ValueError("History task statuses do not cover the requested market population")
        if any(s["status"] == "ERROR" for s in statuses):
            raise ValueError("A market history request failed")
        task_counts = {name: sum(s["status"] == name for s in statuses)
                       for name in sorted({s["status"] for s in statuses})}
        if task_counts != manifest["history_status_counts"] or len(statuses) != manifest["history_tasks"]:
            raise ValueError("History status count mismatch")
    probe = manifest["resolved_market_probe"]
    token, recovered = probe["token_id"], set()
    for response in responses:
        if response["url"].endswith("/prices-history") and (response.get("params") or {}).get("market") == token and response.get("status") == 200 and "error" not in response:
            recovered.update((point["t"], point["p"]) for point in json.loads(response["body"])["history"])
    known = old_p[old_p.market_id.astype(str).eq(probe["market_id"])]
    known_rows = set(known[["t", "p"]].itertuples(index=False, name=None))
    old_latest = pd.to_datetime(old_m.closed_time, utc=True, format="mixed").max()
    fresh_closed = pd.to_datetime(new_m.closed_time, utc=True, format="mixed")
    output = {"status": "PASS", "protected_arm_scores_run": 0,
              "implementation_sha256": {name: digest(repo / name) for name in [
                  "research/engine/cricket_capture.py", "cricket/research/capture_audit/verify.py",
                  "cricket/research/capture_audit/resume.py", "tests/test_cricket_capture.py", "tests/test_cricket_resume.py"]},
              "captured_wrapper_sha256": manifest.get("wrapper_sha256"),
              "original_files_verified": len(manifest["original_sha256"]),
              "capture_files_verified": len(manifest["capture_sha256"]),
              "all_captured_price_observations_retained": True,
              "captured_unique_price_observations": len(archived_response_rows),
              "history_status_counts": task_counts,
              "old_open_markets_not_relisted": sorted(set(old_m[~old_m.closed].market_id.astype(str)) - requested_ids),
              "returned_rows_before_requested_start": before_start,
              "returned_rows_after_requested_end": after_end,
              "out_of_range_history_responses": out_of_range_requests,
              "all_returned_rows_within_requested_ranges": before_start + after_end == 0,
              "latest_requested_price_end": dt.datetime.fromtimestamp(latest_requested_end, dt.timezone.utc).isoformat(),
              "latest_returned_price_timestamp": dt.datetime.fromtimestamp(latest_returned, dt.timezone.utc).isoformat(),
              "all_old_markets_retained": True, "all_old_price_observations_retained": True,
              "old_unique_price_observations": len(old_rows), "new_unique_price_observations": len(new_rows),
              "probe_archived_observations": len(known_rows),
              "probe_identical_observations_recovered": len(known_rows & recovered),
              "probe_all_archived_observations_recovered": known_rows <= recovered,
              "old_latest_closure": old_latest.isoformat(),
              "new_latest_closure": fresh_closed.max().isoformat(),
              "closed_records_after_old_latest_closure": int((new_m.closed & (fresh_closed > old_latest)).sum()),
              "resolved_records_after_old_latest_closure": int((new_m.closed & new_m.uma_status.eq("resolved") & (fresh_closed > old_latest)).sum()),
              "fresh_request_attempts": fresh_requests,
              "request_attempts_including_resumed": len(responses),
              "failed_request_attempts": sum("error" in r for r in responses)}
    if not output["probe_all_archived_observations_recovered"]:
        raise ValueError("Resolved-market backfill did not recover all archived observations")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[3]))
    parser.add_argument("--capture", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = verify(Path(args.repo).resolve(), Path(args.capture).resolve())
    with Path(args.output).open("x") as stream:
        stream.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))
