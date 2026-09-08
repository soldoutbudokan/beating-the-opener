"""Private, append-only prospective points forecasts and settlement evidence.

This module neither fetches a provider nor fits or calls a model. A runtime stages
exact source bytes and forecasts, saves that batch in an authorized durable store,
then attaches the returned receipt. Only batches attested as durable before tip
enter the trial. A caller-supplied receipt is an attestation, not an independently
verified storage API response; the runtime must verify its provider response.

Outcomes stay separate from forecasts. Confirmed participation, including rounded
zero-minute appearances, is distinct from confirmed DNP and an unknown result.
The original historical model and scorer are never imported or weakened here.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
from zoneinfo import ZoneInfo


UTC = timezone.utc
EASTERN = ZoneInfo("America/New_York")
CAP = datetime(2027, 10, 1, tzinfo=UTC)
TARGET = 3000
MIN_DATES = 60
MIN_SETTLED = 2000
MAX_UNKNOWN_FRACTION = .01
SETTLEMENT_DAYS = 14
EV_THRESHOLD = .05
COST_STRESS = .01
RESAMPLES = 10000
BOOTSTRAP_SEED = 20260908
PROBABILITY_FLOOR = 1e-12
BOOKS = {10: "FanDuel", 14: "Fanatics"}
SAFE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}\Z")
HEX = re.compile(r"[0-9a-f]{64}\Z")


def utcnow():
    return datetime.now(UTC)


def instant(value, label="timestamp"):
    if not isinstance(value, str):
        raise ValueError(f"{label}: expected a timezone-aware ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{label}: invalid timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label}: timezone required")
    return parsed.astimezone(UTC)


def stamp(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone-aware datetime required")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def encode(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False, allow_nan=False) + "\n").encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def identifier(value, label):
    if not isinstance(value, str) or not SAFE.fullmatch(value) or value in {".", ".."}:
        raise ValueError(f"{label}: unsafe or missing identity")
    return value


def sha(value, label):
    if not isinstance(value, str) or not HEX.fullmatch(value):
        raise ValueError(f"{label}: expected SHA-256")
    return value


def number(value, label, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label}: finite number required")
    if minimum is not None and value < minimum or maximum is not None and value > maximum:
        raise ValueError(f"{label}: outside allowed range")
    return float(value)


def _safe_path(root, *parts):
    root = Path(root)
    path = root.joinpath(*parts)
    if any(p.is_symlink() for p in [root, *root.parents, path, *path.parents]):
        raise ValueError("symlinks cannot hold prospective evidence")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("evidence path escapes the ledger")
    return path


def write_once(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as handle:
        written = handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
        if written != len(data):
            raise OSError("EVIDENCE_SHORT_WRITE")
    # Verify the stored file, not only the bytes supplied to the writer. Keep
    # incomplete evidence in place on failure; never overwrite it or retry it
    # as though the first write had not happened.
    if path.read_bytes() != data:
        raise OSError("EVIDENCE_READBACK_MISMATCH")


def load(path):
    value = json.loads(Path(path).read_bytes())
    if not isinstance(value, dict):
        raise ValueError("evidence record must be a JSON object")
    return value


def _aware_now(now):
    return instant(stamp(utcnow() if now is None else now))


def validate_storage_identity(receipt):
    if not isinstance(receipt, dict):
        raise ValueError("durable storage receipt required")
    provider = receipt.get("provider")
    if provider == "chatgpt-library":
        for key in ("library_file_id", "library_version_id"):
            if not isinstance(receipt.get(key), str) or not receipt[key].strip():
                raise ValueError(f"durable receipt missing {key}")
    elif provider == "github":
        repository = receipt.get("repository_full_name")
        if not isinstance(repository, str) or len(repository.split("/")) != 2:
            raise ValueError("Git receipt needs exact owner/repository identity")
        for part in repository.split("/"):
            identifier(part, "repository identity")
        if not isinstance(receipt.get("commit_sha"), str) or not re.fullmatch(r"[0-9a-f]{40}", receipt["commit_sha"]):
            raise ValueError("Git receipt needs an exact remotely verified commit SHA")
        path = receipt.get("artifact_path")
        if not isinstance(path, str) or not path or path.startswith("/") or "\\" in path:
            raise ValueError("Git artifact path must be repository-relative")
        for part in path.split("/"):
            identifier(part, "Git artifact path")
    else:
        raise ValueError("unsupported durable receipt provider")
    sha(receipt.get("stored_artifact_sha256"), "stored artifact hash")
    return receipt


def validate_study(study, now=None):
    now = _aware_now(now)
    if not isinstance(study, dict) or study.get("schema") != "wnba-shadow-study-v1":
        raise ValueError("unsupported shadow study schema")
    for key in ("study_id", "candidate_id"):
        identifier(study.get(key), key)
    for key in ("bundle_sha256", "recipe_sha256", "model_recipe_hash", "implementation_sha256"):
        sha(study.get(key), key)
    commit = study.get("registration_commit")
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("registration_commit: exact public code/plan commit required")
    if study.get("market") != "points" or study.get("mode") != "shadow":
        raise ValueError("the single registered population is shadow points")
    frozen = instant(study.get("frozen_at"), "frozen_at")
    sealed = instant(study.get("bundle_sealed_at"), "bundle_sealed_at")
    starts = instant(study.get("starts_at"), "starts_at")
    if not frozen <= sealed <= starts <= now or starts >= CAP:
        raise ValueError("study freeze/seal/start clocks are invalid or future-dated")
    if "bundle_receipt" in study:
        receipt = validate_storage_identity(study["bundle_receipt"])
        if receipt["stored_artifact_sha256"] != study["bundle_sha256"] or instant(receipt.get("durable_at")) != sealed:
            raise ValueError("bundle receipt differs from the frozen artifact/hash clock")
    else:
        for key in ("bundle_library_file_id", "bundle_library_version_id"):
            if not isinstance(study.get(key), str) or not study[key].strip():
                raise ValueError(f"{key}: private durable bundle receipt required")
    return study


def validate_observation_manifest(observation, row, study, cutoff):
    expected = {"source_id", "record_id", "entity_id", "event_id", "season", "kind",
                "observed_at", "published_at", "effective_at", "available_at", "required_at",
                "time_basis", "assumed_available_at", "time_note", "payload_hash"}
    if not isinstance(observation, dict) or set(observation) != expected:
        raise ValueError("complete immutable observation manifest metadata required")
    for key in ("source_id", "record_id", "entity_id", "event_id"):
        value = observation[key]
        if not isinstance(value, str) or not value or value != value.strip():
            raise ValueError("consumed observation identity is invalid")
    sha(observation["payload_hash"], "observation payload_hash")
    if observation["kind"] != "historical_outcome":
        raise ValueError("this frozen future state accepts historical box inputs only")
    if observation["entity_id"] not in {row["player_id"], row["team_id"], row["opponent_id"]}:
        raise ValueError("consumed observation belongs to an unrelated entity")
    effective = instant(observation["effective_at"], "observation effective_at")
    available = instant(observation["available_at"], "observation available_at")
    if (observation["event_id"] == row["game_id"] or effective >= cutoff or available >= cutoff or
            type(observation["season"]) is not int or observation["season"] != effective.year):
        raise ValueError("observation target/season/effective/availability boundary failed")
    if not isinstance(observation["required_at"], list) or not isinstance(observation["time_note"], str):
        raise ValueError("observation timing metadata is malformed")
    clocks = [instant(value, "observation required_at") for value in observation["required_at"]]
    parsed = {}
    for key in ("observed_at", "published_at", "assumed_available_at"):
        value = observation[key]
        parsed[key] = None if value is None else instant(value, key)
        if value is not None:
            clocks.append(parsed[key])
    basis = observation["time_basis"]
    if basis == "observed":
        if parsed["observed_at"] is None or parsed["assumed_available_at"] is not None:
            raise ValueError("observed input lacks its real observation clock")
    elif basis == "assumed":
        if parsed["assumed_available_at"] is None or not observation["time_note"].strip():
            raise ValueError("assumed history requires its clock and disclosed assumption")
    else:
        raise ValueError("unknown observation time basis")
    if not clocks or max(clocks) != available:
        raise ValueError("consumed availability does not reproduce required source clocks")
    freeze = instant(study["frozen_at"])
    if observation["season"] <= 2025:
        if available >= freeze:
            raise ValueError("seed input was not available before candidate freeze")
    elif (effective < freeze or basis != "observed" or parsed["observed_at"] is None or
          parsed["observed_at"] < freeze or available <= effective):
        raise ValueError("protected pre-freeze outcomes cannot enter future state")
    return available


def validate_forecast(row, study, snapshots, now=None):
    now = _aware_now(now)
    if not isinstance(row, dict) or row.get("schema") != "wnba-shadow-forecast-v1":
        raise ValueError("unsupported forecast schema")
    for key in ("forecast_id", "game_id", "player_id", "team_id", "opponent_id"):
        identifier(row.get(key), key)
    if row["team_id"] == row["opponent_id"]:
        raise ValueError("team and opponent must differ")
    if row.get("candidate_id") != study["candidate_id"] or row.get("market") != "points":
        raise ValueError("forecast differs from the frozen candidate/population")
    for key in ("bundle_sha256", "recipe_sha256", "implementation_sha256"):
        if row.get(key) != study[key]:
            raise ValueError(f"forecast {key} differs from the private freeze")
    sha(row.get("input_manifest_sha256"), "input_manifest_sha256")
    # Preserve the full model output alongside the probabilities so an independent
    # verifier can regenerate line pricing. A hash without its bytes is insufficient.
    output = row.get("model_output")
    if not isinstance(output, dict) or output.get("schema") != "wnba-v2-forecast-v1":
        raise ValueError("complete unpriced model output required")
    if digest(encode(output)) != row.get("model_output_sha256"):
        raise ValueError("model output hash mismatch")
    generated = instant(row.get("generated_at"), "generated_at")
    cutoff = instant(row.get("input_cutoff_at"), "input_cutoff_at")
    tip = instant(row.get("tip_at"), "tip_at")
    starts = instant(study["starts_at"])
    if not starts <= generated <= now or not instant(study["frozen_at"]) <= cutoff <= generated < tip < CAP:
        raise ValueError("forecast is not genuinely future-only or inputs cross its cutoff")
    if tip - generated > timedelta(hours=24):
        raise ValueError("forecast is outside the registered final 24-hour window")
    if tip - generated < timedelta(minutes=15):
        raise ValueError("forecast must be generated at least 15 minutes before tip")
    if any(k in row for k in ("actual", "participation", "settlement", "result", "profit")):
        raise ValueError("outcomes cannot be stored in the pregame forecast record")
    quote = row.get("quote")
    if not isinstance(quote, dict):
        raise ValueError("quote object required")
    identifier(quote.get("quote_id"), "quote_id")
    if type(quote.get("book_id")) is not int or quote["book_id"] not in BOOKS:
        raise ValueError("quote book is outside the fixed FanDuel/Fanatics population")
    if quote.get("active") is not True or quote.get("main") is not True or quote.get("is_off") is not False:
        raise ValueError("a live main paired quote is required")
    for key in ("game_id", "player_id"):
        if quote.get(key) != row[key]:
            raise ValueError("quote identity differs from forecast")
    if quote.get("market") != "points":
        raise ValueError("quote market differs from the points study")
    line = number(quote.get("line"), "line", 0)
    if line * 2 != int(line * 2):
        raise ValueError("only integer or half-integer points lines are eligible")
    over = number(quote.get("over_decimal"), "over_decimal", 1.000001)
    under = number(quote.get("under_decimal"), "under_decimal", 1.000001)
    if not 1 <= 1 / over + 1 / under <= 1.15:
        raise ValueError("paired quote fails the registered coherence bound")
    received = instant(quote.get("received_at"), "quote received_at")
    for key in ("over_updated_at", "under_updated_at"):
        updated = instant(quote.get(key), key)
        if not received - timedelta(hours=2) <= updated <= received:
            raise ValueError("quote provider clock is stale or future-dated")
    if not starts <= received <= generated or generated - received > timedelta(minutes=5):
        raise ValueError("quote must be observed after freeze and within five minutes of forecast")
    raw_hash = sha(quote.get("snapshot_sha256"), "quote snapshot_sha256")
    if raw_hash not in snapshots:
        raise ValueError("quote raw bytes are missing from the batch")
    probabilities = [number(row.get(key), key, 0, 1) for key in ("p_over", "p_push", "p_under")]
    if abs(sum(probabilities) - 1) > 1e-9:
        raise ValueError("conditional count probabilities do not sum to one")
    if line != int(line) and probabilities[1] != 0:
        raise ValueError("half-integer count lines cannot push")
    if probabilities[0] + probabilities[2] <= 0:
        raise ValueError("conditional nonpush probability is undefined")
    number(row.get("p_dnp"), "p_dnp", 0, 1)
    if output.get("recipe_hash") != study["model_recipe_hash"] or output.get("p_dnp") != row["p_dnp"]:
        raise ValueError("full forecast recipe/participation differs from the saved row")
    manifest = output.get("input_manifest")
    request = output.get("request")
    if not isinstance(manifest, dict) or manifest.get("schema") != "wnba-v2-inputs-v1" or not isinstance(request, dict):
        raise ValueError("complete input manifest and request are required")
    if manifest.get("request") != request:
        raise ValueError("input manifest request differs from model request")
    if digest(encode(manifest)[:-1]) != row["input_manifest_sha256"] or output.get("input_manifest_hash") != row["input_manifest_sha256"]:
        raise ValueError("input manifest does not reproduce its frozen hash")
    for inner, outer in (("entity_id", "player_id"), ("event_id", "game_id"),
                         ("team_id", "team_id"), ("opponent_id", "opponent_id")):
        if request.get(inner) != row[outer]:
            raise ValueError("model request identity differs from the quote/forecast")
    if instant(request.get("as_of")) != cutoff or instant(request.get("tip_at")) != tip:
        raise ValueError("model request cutoff/tip differs from the saved forecast")
    if type(request.get("season")) is not int or request["season"] != tip.year:
        raise ValueError("model request season differs from the event year")
    boundary = output.get("future_only_boundary")
    if (not isinstance(boundary, dict) or instant(boundary.get("frozen_at")) != instant(study["frozen_at"]) or
            boundary.get("seed_last_season") != 2025 or boundary.get("pre_freeze_2026_history_allowed") is not False or
            boundary.get("parameter_refits") != 0 or boundary.get("independent_performance_claim") is not False):
        raise ValueError("model output does not attest the registered future-only inference boundary")
    observations = manifest.get("observations")
    if not isinstance(observations, list) or any(not isinstance(o, dict) for o in observations):
        raise ValueError("input manifest must preserve every consumed observation")
    available = [validate_observation_manifest(o, row, study, cutoff) for o in observations]
    if available and max(available) >= cutoff:
        raise ValueError("source availability crosses the forecast cutoff")
    latest = None if not available else max(available)
    for value in (row.get("input_max_available_at"), output.get("input_max_available_at")):
        if (None if value is None else instant(value)) != latest:
            raise ValueError("saved latest source clock differs from actual input manifest")
    from .model import price_forecast
    priced = price_forecast(output, "points", line)
    if any(not math.isclose(row[key], priced[key], rel_tol=1e-10, abs_tol=1e-12)
           for key in ("p_over", "p_push", "p_under")):
        raise ValueError("saved probabilities do not reproduce the unpriced model distribution")
    return row


class Ledger:
    def __init__(self, root):
        self.root = _safe_path(root)

    def initialize(self, study, now=None):
        validate_study(study, now)
        write_once(_safe_path(self.root, "study.json"), encode(study))
        return study

    def study(self, now=None):
        return validate_study(load(_safe_path(self.root, "study.json")), now)

    def stage(self, batch_id, forecasts, snapshots, *, attempts=(), now=None):
        now = _aware_now(now)
        study = self.study(now)
        identifier(batch_id, "batch_id")
        if not isinstance(forecasts, list) or not isinstance(snapshots, dict):
            raise ValueError("forecasts must be a list and snapshots a name/bytes mapping")
        for name, data in snapshots.items():
            identifier(name, "snapshot name")
            if not isinstance(data, bytes):
                raise ValueError("source snapshots must contain exact bytes")
        hashes = {digest(data) for data in snapshots.values()}
        seen = set()
        for row in forecasts:
            validate_forecast(row, study, hashes, now)
            if row["forecast_id"] in seen:
                raise ValueError("duplicate forecast identity within batch")
            seen.add(row["forecast_id"])
        if not isinstance(attempts, (list, tuple)) or any(not isinstance(x, dict) for x in attempts):
            raise ValueError("every attempted request/failure must be an object")
        directory = _safe_path(self.root, "batches", batch_id)
        directory.mkdir(parents=True, exist_ok=False)
        files = {"forecasts.jsonl": b"".join(encode(row) for row in forecasts),
                 "attempts.jsonl": b"".join(encode(row) for row in attempts)}
        files.update({f"raw/{name}": data for name, data in snapshots.items()})
        for name, data in files.items():
            write_once(_safe_path(directory, name), data)
        manifest = {"schema": "wnba-shadow-batch-v1", "batch_id": batch_id,
                    "study_id": study["study_id"], "candidate_id": study["candidate_id"],
                    "created_at": stamp(now), "forecasts": len(forecasts), "attempts": len(attempts),
                    "attempt_statuses": dict(Counter(str(a.get("status", "UNSPECIFIED")) for a in attempts)),
                    "files": {name: {"sha256": digest(data), "bytes": len(data)}
                              for name, data in sorted(files.items())}}
        write_once(directory / "manifest.json", encode(manifest))
        return {"batch_id": batch_id, "manifest_sha256": digest(encode(manifest)),
                "status": "AWAITING_DURABLE_RECEIPT", "forecasts": len(forecasts)}

    def batch(self, batch_id, now=None):
        now = _aware_now(now)
        identifier(batch_id, "batch_id")
        directory = _safe_path(self.root, "batches", batch_id)
        manifest = load(directory / "manifest.json")
        study = self.study(now)
        if (manifest.get("schema") != "wnba-shadow-batch-v1" or manifest.get("batch_id") != batch_id
                or manifest.get("study_id") != study["study_id"]
                or manifest.get("candidate_id") != study["candidate_id"]):
            raise ValueError("batch manifest identity mismatch")
        files = manifest.get("files")
        if not isinstance(files, dict) or not {"forecasts.jsonl", "attempts.jsonl"} <= files.keys():
            raise ValueError("batch manifest omits required files")
        actual = {str(p.relative_to(directory)) for p in directory.rglob("*") if p.is_file()}
        if actual - {"manifest.json", "seal.json"} != set(files):
            raise ValueError("batch manifest does not exactly cover immutable files")
        for name, metadata in files.items():
            if name not in {"forecasts.jsonl", "attempts.jsonl"}:
                if not isinstance(name, str) or not name.startswith("raw/"):
                    raise ValueError("unknown immutable batch path")
                identifier(name[4:], "raw snapshot name")
            data = _safe_path(directory, name).read_bytes()
            if not isinstance(metadata, dict) or metadata != {"sha256": digest(data), "bytes": len(data)}:
                raise ValueError("immutable batch bytes changed")
        rows = [json.loads(line) for line in (directory / "forecasts.jsonl").read_bytes().splitlines()]
        attempts = [json.loads(line) for line in (directory / "attempts.jsonl").read_bytes().splitlines()]
        if len(rows) != manifest.get("forecasts") or len(attempts) != manifest.get("attempts"):
            raise ValueError("manifest row counts disagree")
        if (any(not isinstance(a, dict) for a in attempts) or
                manifest.get("attempt_statuses") != dict(Counter(str(a.get("status", "UNSPECIFIED")) for a in attempts))):
            raise ValueError("manifest attempt statuses disagree")
        hashes = {x["sha256"] for name, x in files.items() if name.startswith("raw/")}
        created = instant(manifest.get("created_at"), "batch creation")
        if created > now:
            raise ValueError("batch creation is future-dated")
        for row in rows:
            validate_forecast(row, study, hashes, created)
        return manifest, rows

    def seal(self, batch_id, receipt, now=None):
        now = _aware_now(now)
        manifest, rows = self.batch(batch_id, now)
        self._validate_seal(manifest, receipt, now)
        write_once(_safe_path(self.root, "batches", batch_id, "seal.json"), encode(receipt))
        sealed_at = instant(receipt["durable_at"])
        return {"batch_id": batch_id, "durable_at": stamp(sealed_at),
                "eligible_before_tip": sum(sealed_at <= instant(row["tip_at"]) - timedelta(minutes=15) for row in rows),
                "late_seals": sum(sealed_at > instant(row["tip_at"]) - timedelta(minutes=15) for row in rows)}

    @staticmethod
    def _validate_seal(manifest, receipt, now):
        if not isinstance(receipt, dict) or receipt.get("schema") != "wnba-shadow-seal-v1":
            raise ValueError("durable receipt schema required")
        if receipt.get("batch_id") != manifest["batch_id"]:
            raise ValueError("durable receipt batch mismatch")
        if receipt.get("manifest_sha256") != digest(encode(manifest)):
            raise ValueError("durable receipt does not attest the exact batch manifest")
        validate_storage_identity(receipt)
        if not instant(manifest["created_at"]) <= instant(receipt.get("durable_at")) <= now:
            raise ValueError("durable seal predates its bytes or is future-dated")

    def population(self, now=None):
        now = _aware_now(now)
        self.study(now)
        rows, exclusions = [], Counter()
        parent = _safe_path(self.root, "batches")
        if not parent.exists():
            return [], dict(exclusions)
        identities = {}
        for directory in sorted(parent.iterdir()):
            if directory.is_symlink() or not directory.is_dir():
                raise ValueError("unexpected batch directory entry")
            if not (directory / "manifest.json").exists():
                exclusions["interrupted_batches"] += 1
                continue
            manifest, batch_rows = self.batch(directory.name, now)
            for row in batch_rows:
                original = identities.setdefault(row["forecast_id"], encode(row))
                if original != encode(row):
                    raise ValueError("conflicting reused forecast identity")
            if not (directory / "seal.json").exists():
                exclusions["unsealed_forecasts"] += len(batch_rows)
                continue
            receipt = load(directory / "seal.json")
            self._validate_seal(manifest, receipt, now)
            sealed_at = instant(receipt["durable_at"])
            for row in batch_rows:
                if sealed_at > instant(row["tip_at"]) - timedelta(minutes=15):
                    exclusions["sealed_later_than_15_minutes_before_tip"] += 1
                    continue
                rows.append(row)
        # Book preference only breaks an equal observed-receipt clock. Model edge,
        # settled outcomes, and later more favorable prices never choose the row.
        rows.sort(key=lambda r: (instant(r["quote"]["received_at"]), r["quote"]["book_id"],
                                 instant(r["generated_at"]), r["forecast_id"]))
        selected, player_games = [], set()
        for row in rows:
            key = (row["game_id"], row["player_id"])
            if key in player_games:
                exclusions["later_player_game_forecasts"] += 1
                continue
            player_games.add(key)
            selected.append(row)
        selected.sort(key=lambda r: (instant(r["tip_at"]).astimezone(EASTERN).date(),
                                      instant(r["tip_at"]), r["game_id"], r["player_id"]))
        if len(selected) >= TARGET:
            crossing = instant(selected[TARGET - 1]["tip_at"]).astimezone(EASTERN).date()
            kept = [r for r in selected if instant(r["tip_at"]).astimezone(EASTERN).date() <= crossing]
            exclusions["post_endpoint_forecasts"] += len(selected) - len(kept)
            selected = kept
        return selected, dict(exclusions)

    def settle(self, settlement, source_bytes, now=None):
        now = _aware_now(now)
        rows, _ = self.population(now)
        by_id = {r["forecast_id"]: r for r in rows}
        validate_settlement(settlement, by_id, source_bytes, now)
        previous = self.settlements(by_id, now)
        old = previous.get(settlement["forecast_id"])
        if settlement.get("supersedes") != (None if old is None else old["settlement_id"]):
            raise ValueError("settlement revision must supersede the current explicit record")
        if old is not None and instant(settlement["observed_at"]) < instant(old["observed_at"]):
            raise ValueError("settlement revision observation clock regresses")
        directory = _safe_path(self.root, "settlements", settlement["settlement_id"])
        directory.mkdir(parents=True, exist_ok=False)
        write_once(directory / "source.body", source_bytes)
        write_once(directory / "record.json", encode(settlement))
        return settlement

    def settlements(self, forecasts, now=None):
        now = _aware_now(now)
        directory = _safe_path(self.root, "settlements")
        all_records = {}
        if not directory.exists():
            return {}
        for child in sorted(directory.iterdir()):
            if child.is_symlink() or not child.is_dir():
                raise ValueError("unexpected settlement directory entry")
            if not (child / "record.json").exists():
                continue  # An interrupted append never becomes a confirmed outcome.
            record = load(child / "record.json")
            if record.get("settlement_id") != child.name:
                raise ValueError("settlement identity differs from its directory")
            # Saved corrections for subsequently post-endpoint rows remain evidence.
            if record.get("forecast_id") not in forecasts:
                continue
            validate_settlement(record, forecasts, (child / "source.body").read_bytes(), now)
            all_records[record["settlement_id"]] = record
        latest, children, roots = {}, {}, {}
        for record in all_records.values():
            parent = record.get("supersedes")
            if parent is None:
                if record["forecast_id"] in roots:
                    raise ValueError("settlement revision chain has multiple roots")
                roots[record["forecast_id"]] = record
            else:
                previous = all_records.get(parent)
                if previous is None or previous["forecast_id"] != record["forecast_id"]:
                    raise ValueError("settlement revision parent is missing or crosses forecasts")
                if parent in children:
                    raise ValueError("settlement revision chain branches")
                if instant(record["observed_at"]) < instant(previous["observed_at"]):
                    raise ValueError("settlement revision observation clock regresses")
                children[parent] = record
        visited = set()
        for forecast_id, record in roots.items():
            while True:
                if record["settlement_id"] in visited:
                    raise ValueError("settlement revision cycle")
                visited.add(record["settlement_id"])
                child = children.get(record["settlement_id"])
                if child is None:
                    latest[forecast_id] = record
                    break
                record = child
        if len(visited) != len(all_records):
            raise ValueError("settlement revision chain has no reachable root")
        return latest

    def status(self, now=None):
        now = _aware_now(now)
        rows, excluded = self.population(now)
        grades = self.settlements({r["forecast_id"]: r for r in rows}, now)
        end = None
        if len(rows) >= TARGET:
            day = instant(rows[TARGET - 1]["tip_at"]).astimezone(EASTERN).date()
            end = min(CAP, datetime.combine(day + timedelta(days=1), datetime.min.time(), EASTERN).astimezone(UTC))
        elif now >= CAP:
            end = CAP
        unknown = sum(grades.get(r["forecast_id"], {}).get("participation", "unknown") == "unknown" for r in rows)
        state = "COLLECTING"
        if end is not None and now >= end:
            state = "WAITING_FOR_SETTLEMENT" if now < end + timedelta(days=SETTLEMENT_DAYS) else "EVALUATION_DUE"
        evaluation = None
        evaluation_path = _safe_path(self.root, "evaluation.json")
        if evaluation_path.exists():
            saved = load(evaluation_path)
            if (saved.get("schema") != "wnba-shadow-evaluation-v1" or
                    saved.get("study_id") != self.study(now)["study_id"] or
                    saved.get("study_sha256") != digest(_safe_path(self.root, "study.json").read_bytes()) or
                    end is None):
                raise ValueError("saved primary evaluation does not bind this frozen study")
            evaluated_at = instant(saved.get("evaluated_at"), "evaluated_at")
            if not end + timedelta(days=SETTLEMENT_DAYS) <= evaluated_at <= now:
                raise ValueError("saved evaluation clock violates the registered endpoint wait")
            sha(saved.get("input_sha256"), "evaluation inputs hash")
            if saved.get("decision") not in {"STOP", "INSUFFICIENT_EVIDENCE", "QUALIFICATION_CHECKS_PENDING"}:
                raise ValueError("saved evaluation decision is invalid")
            self.evaluation_inputs(saved, now)
            evaluation = {key: saved[key] for key in ("evaluated_at", "decision", "input_sha256")}
            state = "EVALUATED"
        elif _safe_path(self.root, "evaluation-inputs.json").exists():
            # An interrupted first calculation is visible and cannot silently
            # start again with revised grades or become another primary attempt.
            state = "EVALUATION_INTERRUPTED"
        latest_capture = None
        batches = _safe_path(self.root, "batches")
        if batches.exists():
            manifests = [load(p / "manifest.json") for p in batches.iterdir() if (p / "manifest.json").is_file()]
            if manifests:
                last = max(manifests, key=lambda m: (instant(m["created_at"]), m["batch_id"]))
                latest_capture = {k: last[k] for k in ("batch_id", "created_at", "forecasts", "attempts", "attempt_statuses")}
                latest_capture["durable_receipt_attached"] = (batches / last["batch_id"] / "seal.json").is_file()
        return {"schema": "wnba-shadow-status-v1", "generated_at": stamp(now), "status": state,
                "forecasts": len(rows), "target_forecasts": TARGET, "cap_at": stamp(CAP),
                "endpoint_at": None if end is None else stamp(end),
                "et_dates": len({instant(r["tip_at"]).astimezone(EASTERN).date() for r in rows}),
                "unresolved": unknown, "exclusions": excluded, "latest_capture": latest_capture,
                "evaluation": evaluation,
                "basis": "Private shadow research; no wager or performance qualification."}

    def evaluate(self, now=None):
        now = _aware_now(now)
        status = self.status(now)
        if status["status"] == "EVALUATED":
            raise FileExistsError("the primary evaluation has already been recorded")
        if status["status"] != "EVALUATION_DUE":
            raise ValueError("primary performance remains sealed until the fixed endpoint and settlement wait")
        path = _safe_path(self.root, "evaluation.json")
        if path.exists():
            raise FileExistsError("the primary evaluation has already been recorded")
        rows, _ = self.population(now)
        grades = self.settlements({r["forecast_id"]: r for r in rows}, now)
        frozen_inputs = {"schema": "wnba-shadow-evaluation-inputs-v1",
                         "study_sha256": digest(_safe_path(self.root, "study.json").read_bytes()),
                         "frozen_at": stamp(now),
                         "forecasts": [{"forecast_id": row["forecast_id"], "forecast_sha256": digest(encode(row)),
                                        "settlement_id": grades.get(row["forecast_id"], {}).get("settlement_id"),
                                        "settlement_sha256": (digest(encode(grades[row["forecast_id"]]))
                                                              if row["forecast_id"] in grades else None)}
                                       for row in rows]}
        write_once(_safe_path(self.root, "evaluation-inputs.json"), encode(frozen_inputs))
        result = evaluate_saved_rows(rows, grades)
        result.update(study_id=self.study(now)["study_id"], evaluated_at=stamp(now), endpoint=status,
                      study_sha256=digest(_safe_path(self.root, "study.json").read_bytes()),
                      evaluation_inputs_sha256=digest(encode(frozen_inputs)),
                      input_sha256=digest(encode({"forecasts": rows, "settlements": grades})))
        write_once(path, encode(result))
        return result

    def evaluation_inputs(self, result=None, now=None):
        """Restore exactly the frozen evaluation rows, ignoring later corrections.

        This validates immutable references and hashes; it does not recompute
        performance. An independent verifier may pass the returned rows to the
        pure saved-row scorer and compare every substantive result.
        """
        now = _aware_now(now)
        result = load(_safe_path(self.root, "evaluation.json")) if result is None else result
        data = _safe_path(self.root, "evaluation-inputs.json").read_bytes()
        if digest(data) != result.get("evaluation_inputs_sha256"):
            raise ValueError("frozen evaluation input manifest changed")
        frozen = json.loads(data)
        if (not isinstance(frozen, dict) or frozen.get("schema") != "wnba-shadow-evaluation-inputs-v1" or
                frozen.get("study_sha256") != result.get("study_sha256") or
                frozen.get("frozen_at") != result.get("evaluated_at")):
            raise ValueError("evaluation input freeze identity/clock mismatch")
        available, _ = self.population(now)
        by_id = {row["forecast_id"]: row for row in available}
        refs = frozen.get("forecasts")
        if not isinstance(refs, list) or any(not isinstance(ref, dict) for ref in refs):
            raise ValueError("evaluation population references missing")
        rows, grades, seen = [], {}, set()
        for ref in refs:
            forecast_id = ref.get("forecast_id")
            row = by_id.get(forecast_id)
            if row is None or forecast_id in seen or digest(encode(row)) != ref.get("forecast_sha256"):
                raise ValueError("frozen evaluation forecast changed, is missing or duplicated")
            seen.add(forecast_id)
            rows.append(row)
            settlement_id = ref.get("settlement_id")
            if settlement_id is None:
                if ref.get("settlement_sha256") is not None:
                    raise ValueError("unresolved evaluation row invents a settlement hash")
                continue
            identifier(settlement_id, "frozen settlement_id")
            directory = _safe_path(self.root, "settlements", settlement_id)
            raw = (directory / "record.json").read_bytes()
            if digest(raw) != ref.get("settlement_sha256"):
                raise ValueError("frozen evaluation settlement bytes changed")
            record = json.loads(raw)
            if record.get("settlement_id") != settlement_id or record.get("forecast_id") != forecast_id:
                raise ValueError("frozen settlement identity differs from its reference")
            validate_settlement(record, {forecast_id: row}, (directory / "source.body").read_bytes(), now)
            if instant(record["observed_at"]) > instant(frozen["frozen_at"]):
                raise ValueError("frozen evaluation consumed a later outcome revision")
            grades[forecast_id] = record
        if digest(encode({"forecasts": rows, "settlements": grades})) != result.get("input_sha256"):
            raise ValueError("exact saved evaluation input hash does not reproduce")
        return rows, grades


def validate_settlement(record, forecasts, source_bytes, now):
    if not isinstance(record, dict) or record.get("schema") != "wnba-shadow-settlement-v1":
        raise ValueError("unsupported settlement schema")
    identifier(record.get("settlement_id"), "settlement_id")
    forecast = forecasts.get(record.get("forecast_id"))
    if forecast is None:
        raise ValueError("settlement has no eligible, pre-tip sealed forecast")
    if record.get("source_sha256") != digest(source_bytes):
        raise ValueError("settlement raw bytes differ from their hash")
    for key in ("game_id", "player_id"):
        if record.get(key) != forecast[key]:
            raise ValueError("settlement identity differs from forecast")
    observed = instant(record.get("observed_at"), "settlement observed_at")
    completed = instant(record.get("game_completed_at"), "game_completed_at")
    if not instant(forecast["tip_at"]) < completed <= observed <= now:
        raise ValueError("settlement is premature or future-dated")
    participation = record.get("participation")
    if participation not in {"played", "dnp", "unknown"}:
        raise ValueError("explicit played/dnp/unknown participation required")
    if participation == "played":
        value = record.get("actual_points")
        if type(value) is not int or value < 0:
            raise ValueError("played appearance requires its nonnegative integer points")
    elif record.get("actual_points") is not None:
        raise ValueError("DNP/unknown does not invent an observed zero-count outcome")
    minutes = record.get("display_minutes")
    if minutes is not None:
        number(minutes, "display_minutes", 0)
    if participation == "dnp" and minutes not in (None, 0):
        raise ValueError("DNP conflicts with positive displayed minutes")
    if not isinstance(record.get("participation_evidence"), str) or not record["participation_evidence"].strip():
        raise ValueError("participation decision needs an explicit source basis")
    if record.get("supersedes") is not None:
        identifier(record["supersedes"], "supersedes")
    return record


def expected_profit(row, side, stress=0.):
    opposite = "under" if side == "over" else "over"
    if side not in {"over", "under"}:
        raise ValueError("unknown side")
    return (1 - row["p_dnp"]) * (row[f"p_{side}"] * (row["quote"][f"{side}_decimal"] - 1)
                                  - row[f"p_{opposite}"] - stress)


def grade(row, settlement, side):
    if side not in {"over", "under"}:
        raise ValueError("unknown side")
    participation = settlement.get("participation", "unknown")
    if participation == "unknown":
        return {"result": "unresolved", "stake": None, "profit": None, "stressed_profit": None}
    if participation == "dnp":
        return {"result": "void", "stake": 0., "profit": 0., "stressed_profit": 0.}
    actual, line = settlement["actual_points"], row["quote"]["line"]
    win = actual > line if side == "over" else actual < line
    result = "push" if actual == line else "win" if win else "loss"
    profit = 0. if result == "push" else row["quote"][f"{side}_decimal"] - 1 if win else -1.
    return {"result": result, "stake": 1., "profit": profit, "stressed_profit": profit - COST_STRESS}


def cluster_interval(records, numerator, denominator, *, samples=RESAMPLES, seed=BOOTSTRAP_SEED):
    """Percentile interval of ratio-of-sums, resampling whole ET game dates."""
    import numpy as np

    totals = {}
    for row in records:
        a, b = totals.setdefault(row["date"], [0., 0.])
        totals[row["date"]] = [a + row[numerator], b + row[denominator]]
    if not totals or sum(x[1] for x in totals.values()) <= 0:
        return None
    values = np.array([totals[day] for day in sorted(totals)], dtype=float)
    if len(values) < 2:
        return None
    rng = np.random.default_rng(seed)
    estimates = []
    # Small batches bound memory on a year-long trial.
    for start in range(0, samples, 200):
        picks = rng.integers(0, len(values), (min(200, samples - start), len(values)))
        sums = values[picks].sum(axis=1)
        estimates.extend((sums[sums[:, 1] > 0, 0] / sums[sums[:, 1] > 0, 1]).tolist())
    if len(estimates) != samples:
        return None  # A denominator-free resample cannot silently disappear.
    return [float(x) for x in np.percentile(estimates, [2.5, 97.5])]


def evaluate_saved_rows(rows, settlements):
    """Pure saved-row arithmetic for endpoint evaluation and independent replay.

    The caller must validate the freeze, source receipts, population and endpoint.
    This function does not grant early evaluation permission or establish privacy.
    """
    paired, selected, controls, counts = [], [], [], Counter()
    for row in rows:
        date = instant(row["tip_at"]).astimezone(EASTERN).date().isoformat()
        settlement = settlements.get(row["forecast_id"], {"participation": "unknown"})
        participation = settlement["participation"]
        counts[participation] += 1
        over_ev, under_ev = expected_profit(row, "over"), expected_profit(row, "under")
        side = "over" if over_ev >= under_ev else "under"
        controls.append({"date": date, "forecast_id": row["forecast_id"], "side": "under",
                         **grade(row, settlement, "under")})
        if max(over_ev, under_ev) >= EV_THRESHOLD:
            counts["selected_forecasts"] += 1
            result = grade(row, settlement, side)
            selected.append({"date": date, "forecast_id": row["forecast_id"], "side": side, **result})
        if participation != "played":
            continue
        actual, line = settlement["actual_points"], row["quote"]["line"]
        if actual == line:
            counts["push"] += 1
            continue
        outcome = actual > line
        p = row["p_over"] / (row["p_over"] + row["p_under"])
        qover, qunder = 1 / row["quote"]["over_decimal"], 1 / row["quote"]["under_decimal"]
        baseline = qover / (qover + qunder)
        loss = lambda x: -math.log(max(PROBABILITY_FLOOR, x if outcome else 1 - x))
        paired.append({"date": date, "forecast_id": row["forecast_id"], "count": 1.,
                       "model_loss": loss(p), "market_loss": loss(baseline),
                       "difference": loss(p) - loss(baseline)})
    def economics(records):
        resolved = [r for r in records if r["stake"] is not None]
        stake = sum(r["stake"] for r in resolved)
        profit = sum(r["profit"] for r in resolved)
        stressed = sum(r["stressed_profit"] for r in resolved)
        return {"selected": len(records), "results": dict(Counter(r["result"] for r in records)),
                "stake": stake, "profit": profit, "stressed_profit": stressed,
                "roi": None if not stake else profit / stake,
                "stressed_roi": None if not stake else stressed / stake,
                "stressed_roi_ci95": cluster_interval(resolved, "stressed_profit", "stake")}
    strategy, under = economics(selected), economics(controls)
    difference = None if not paired else sum(r["difference"] for r in paired) / len(paired)
    interval = cluster_interval(paired, "difference", "count")
    dates = len({r["date"] for r in paired})
    enough = (len(paired) >= MIN_SETTLED and dates >= MIN_DATES and bool(rows)
              and counts["unknown"] / len(rows) <= MAX_UNKNOWN_FRACTION)
    statistical = interval is not None and interval[1] < 0
    economic = strategy["stressed_roi_ci95"] is not None and strategy["stressed_roi_ci95"][0] > 0
    decision = "INSUFFICIENT_EVIDENCE" if not enough else "QUALIFICATION_CHECKS_PENDING" if statistical and economic else "STOP"
    return {"schema": "wnba-shadow-evaluation-v1", "decision": decision,
            "counts": {"forecasts": len(rows), "nonpush_played": len(paired), "settled_et_dates": dates, **dict(counts)},
            "primary": {"measure": "conditional nonpush log loss minus proportional no-vig quoted price",
                        "difference": difference, "ci95": interval,
                        "model_log_loss": None if not paired else sum(r["model_loss"] for r in paired) / len(paired),
                        "market_log_loss": None if not paired else sum(r["market_loss"] for r in paired) / len(paired)},
            "shadow_economics": strategy, "always_under_all_admitted_forecasts": under,
            "gates": {"sample_sufficient": enough, "log_loss_ci_upper_below_zero": statistical,
                      "stressed_roi_ci_lower_above_zero": economic,
                      "independent_reproduction": "REQUIRED_SEPARATELY"},
            "rules": {"net_ev_threshold": EV_THRESHOLD, "cost_stress_per_nonvoid_unit": COST_STRESS,
                      "bootstrap_resamples": RESAMPLES, "bootstrap_seed": BOOTSTRAP_SEED,
                      "cost_note": "Quoted payouts already include vig. The extra 1% is a declared cost/slippage stress, not an observed fee."},
            "limitation": "Shadow results do not establish fill availability, real returns or future profits."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("initialize", "stage", "seal", "settle", "status", "evaluate"))
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--batch-id")
    parser.add_argument("--snapshot", action="append", default=[], help="NAME=PATH; exact private source bytes")
    parser.add_argument("--source", type=Path, help="raw settlement evidence")
    args = parser.parse_args()
    ledger = Ledger(args.ledger)
    data = load(args.input) if args.input else None
    if args.command == "initialize":
        result = ledger.initialize(data)
    elif args.command == "stage":
        snapshots = {}
        for spec in args.snapshot:
            name, separator, path = spec.partition("=")
            if not separator or name in snapshots:
                parser.error("snapshots require unique NAME=PATH arguments")
            snapshots[name] = Path(path).read_bytes()
        result = ledger.stage(args.batch_id, data["forecasts"], snapshots, attempts=data.get("attempts", []))
    elif args.command == "seal":
        result = ledger.seal(args.batch_id, data)
    elif args.command == "settle":
        if args.source is None:
            parser.error("settle requires --source")
        result = ledger.settle(data, args.source.read_bytes())
    elif args.command == "status":
        result = ledger.status()
    else:
        result = ledger.evaluate()
    # Summaries remain in the private execution environment. A public runner must
    # never invoke this CLI or copy its output without separate disclosure consent.
    print(json.dumps(result, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
