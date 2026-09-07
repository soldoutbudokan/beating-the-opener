"""Immutable resume inputs and raw-history validation for cricket collection."""
from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import json
import math
from pathlib import Path


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def cache_key(url, params):
    return json.dumps([url, params], sort_keys=True)


def validate_history_payload(data):
    if not isinstance(data, dict) or not isinstance(data.get("history"), list):
        raise ValueError("Price response must contain a history array")
    for point in data["history"]:
        if not isinstance(point, dict) or type(point.get("t")) is not int:
            raise ValueError("Price history timestamp must be integer Unix seconds")
        value = point.get("p")
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError("Price history value must be a finite probability")
    return data


def load_response_records(paths):
    records = []
    for path in paths:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            records.extend(json.loads(line) for line in stream)
    return records


def load_resume_snapshot(prior, output, expected_original_sha256):
    """Require an explicit pre-task plan; never infer a cutoff from fetched rows."""
    prior, output = Path(prior).resolve(), Path(output).resolve()
    plan_path = prior / "fetch-plan.json"
    if not plan_path.is_file():
        raise ValueError("Legacy capture has no explicit fetch plan; cutoff inference is refused")
    plan = json.loads(plan_path.read_text())
    if plan.get("schema") != "cricket-fetch-plan-v1":
        raise ValueError("Unsupported fetch plan")
    instant = dt.datetime.fromisoformat(plan["price_as_of"])
    if instant.tzinfo is None or instant.utcoffset() != dt.timedelta(0):
        raise ValueError("Fetch plan price_as_of must have an explicit UTC timezone")
    if plan["original_sha256"] != expected_original_sha256:
        raise ValueError("Original archives changed since the interrupted fetch")
    source_metadata = prior / "requested-markets.parquet"
    if sha256(source_metadata) != plan["requested_metadata_sha256"]:
        raise ValueError("Requested metadata snapshot checksum mismatch")
    output_metadata = output / "requested-markets.parquet"
    with output_metadata.open("xb") as stream:
        stream.write(source_metadata.read_bytes())
    lineage = list(plan.get("inherited_response_files", []))
    if (prior / "responses.jsonl.gz").exists():
        lineage.append("responses.jsonl.gz")
    sources = output / "response-sources"
    sources.mkdir(exist_ok=False)
    copied = []
    for number, name in enumerate(dict.fromkeys(lineage)):
        source = (prior / name).resolve()
        if not source.is_relative_to(prior):
            raise ValueError("Response lineage escaped the prior capture")
        payload = source.read_bytes()
        # Validate complete compressed members before exposing a reusable copy.
        gzip.decompress(payload)
        destination = sources / f"{number:04d}.jsonl.gz"
        with destination.open("xb") as stream:
            stream.write(payload)
        copied.append(destination)
    return {"metadata_path": output_metadata, "price_as_of": plan["price_as_of"],
            "response_paths": copied, "records": load_response_records(copied),
            "planned_market_ids": plan["planned_market_ids"]}


def captured_observations(records, token_to_market):
    """Retain probe rows, inherited rows and provider rows outside request bounds."""
    observations = set()
    for record in records:
        if not record["url"].endswith("/prices-history") or record.get("status") != 200 or "error" in record:
            continue
        data = validate_history_payload(json.loads(record["body"]))
        token = (record.get("params") or {}).get("market")
        if token not in token_to_market:
            raise ValueError("Captured history token cannot be mapped to a market")
        observations.update((str(token_to_market[token]), point["t"], point["p"])
                            for point in data["history"])
    return observations
