"""Append one finished collection to main without checking out the whole archive.

The temporary Git index starts from the latest remote tree. Only the selected
immutable run and three named derived files can change. The checkout, live
archives, remote credentials and unrelated tree entries are never modified.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tempfile

ROOT = PurePosixPath("data/operations")
DERIVED = ("health.json", "state.json", "status.json")


class PublishError(RuntimeError):
    """Safe error code: subprocess output can contain credentials, so omit it."""


def _git(repo, *args, data=None, env=None, check=True):
    result = subprocess.run(["git", "-C", str(repo), *args], input=data,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env={**os.environ, **(env or {})})
    if check and result.returncode:
        raise PublishError("GIT_" + args[0].upper().replace("-", "_") + "_FAILED")
    return result


def _json(raw):
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, TypeError, UnicodeDecodeError):
        raise PublishError("INVALID_JSON_DOCUMENT") from None


def _time(value):
    try:
        result = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError()
        return result.astimezone(dt.timezone.utc)
    except (ValueError, TypeError, AttributeError):
        raise PublishError("INVALID_COMPLETION_CLOCK") from None


def _regular(path, boundary):
    """Reject symlinks at every component, including nonexistent destinations."""
    if not path.is_relative_to(boundary):
        raise PublishError("PATH_OUTSIDE_ALLOWED_OUTPUT")
    for item in [path, *path.parents]:
        if item == boundary:
            break
        try:
            mode = item.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise PublishError("SYMLINK_NOT_ALLOWED")
        if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
            raise PublishError("SPECIAL_FILE_NOT_ALLOWED")
    if path.exists() and not path.is_file():
        raise PublishError("EXPECTED_REGULAR_FILE")


def snapshot(repo, output, run_id):
    """Read and hash the exact completed payload once, before any push attempt."""
    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}", run_id):
        raise PublishError("UNSAFE_RUN_ID")
    output = Path(output)
    if ".." in output.parts:
        raise PublishError("PATH_TRAVERSAL_NOT_ALLOWED")
    if not output.is_absolute():
        output = repo / output
    if output != repo / str(ROOT):
        raise PublishError("ONLY_DATA_OPERATIONS_ALLOWED")
    directory = output / "runs" / run_id
    _regular(directory / "report.json", repo)
    if not (directory / "report.json").is_file():
        raise PublishError("RUN_NOT_FINISHED")
    payload = {}
    for parent, dirs, files in os.walk(directory, followlinks=False):
        for name in dirs:
            _regular(Path(parent) / name / "__path_check__", repo)
        for name in files:
            path = Path(parent) / name
            _regular(path, repo)
            payload[path.relative_to(repo).as_posix()] = path.read_bytes()
    prefix = f"{ROOT}/runs/{run_id}/"
    report = _json(payload[prefix + "report.json"])
    if (report.get("schema") != "collection-health-v1" or report.get("run_id") != run_id
            or report.get("status") not in ("OK", "DEGRADED", "FAILED")):
        raise PublishError("INVALID_RUN_REPORT")
    _time(report.get("completed_at"))
    terminal = {"control/status.json", "control/decisions.jsonl"}
    actual = {path[len(prefix):]: {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
              for path, raw in payload.items()
              if path != prefix + "report.json" and path[len(prefix):] not in terminal}
    if report.get("files") != actual:
        raise PublishError("RUN_MANIFEST_MISMATCH")
    control_status = payload.get(prefix + "control/status.json")
    control_decisions = payload.get(prefix + "control/decisions.jsonl")
    control_complete = False
    if control_status is not None:
        status = _json(control_status)
        if (status.get("schema") != "operations-status-v1" or status.get("run_id") != run_id
                or _time(status.get("generated_at")) < _time(report["completed_at"])):
            raise PublishError("STATUS_DOES_NOT_MATCH_RUN")
        if control_decisions is not None:
            events = [_json(row) for row in control_decisions.splitlines() if row.strip()]
            if events != status.get("new_events"):
                raise PublishError("CONTROL_DECISIONS_MISMATCH")
            control_complete = True
    elif control_decisions is not None:
        raise PublishError("CONTROL_DECISIONS_WITHOUT_STATUS")
    for name in DERIVED:
        path = output / name
        _regular(path, repo)
        if path.exists():
            raw = path.read_bytes()
            # A control crash must not lose already completed source capture.
            # Publish latest status only after both control files are complete
            # and the latest pointer is exactly the run's immutable status.
            if name != "status.json" or (control_complete and raw == control_status):
                payload[f"{ROOT}/{name}"] = raw
    if f"{ROOT}/health.json" not in payload or f"{ROOT}/state.json" not in payload:
        raise PublishError("DERIVED_HEALTH_AND_STATE_REQUIRED")
    if _json(payload[f"{ROOT}/health.json"]) != report:
        raise PublishError("HEALTH_DOES_NOT_MATCH_RUN")
    state = _json(payload[f"{ROOT}/state.json"])
    if state.get("schema") != "collection-state-v1" or not isinstance(state.get("sources"), dict):
        raise PublishError("INVALID_STATE_SOURCES")
    return payload


def _remote_files(repo, base):
    # Include the ancestors themselves: a tracked `data` or `operations` symlink
    # must be rejected before any tree construction could replace it.
    raw = _git(repo, "ls-tree", "-r", "-z", base, "--", "data").stdout
    entries = {}
    for row in raw.split(b"\0"):
        if not row:
            continue
        meta, name = row.split(b"\t", 1)
        mode, kind, oid = meta.decode().split()
        path = name.decode("utf-8")
        if path in ("data", str(ROOT)) or path.startswith(str(ROOT) + "/"):
            if mode not in ("100644", "100755") or kind != "blob":
                raise PublishError("REMOTE_SYMLINK_OR_SPECIAL_FILE")
            entries[path] = oid
    return entries


def _preserves_state(new, old):
    """Keep stale concurrent snapshots from dropping retained source coverage."""
    if not isinstance(new.get("sources"), dict) or not isinstance(old.get("sources"), dict):
        raise PublishError("INVALID_STATE_SOURCES")
    for name, prior in old["sources"].items():
        candidate = new["sources"].get(name)
        if not isinstance(prior, dict) or not isinstance(candidate, dict):
            return False
        if prior.get("last_success_at"):
            if not candidate.get("last_success_at") or _time(candidate["last_success_at"]) < _time(prior["last_success_at"]):
                return False
        old_cursor, new_cursor = prior.get("cursor", {}), candidate.get("cursor", {})
        if not isinstance(old_cursor, dict) or not isinstance(new_cursor, dict):
            return False
        if not set(old_cursor.get("markets", {})).issubset(new_cursor.get("markets", {})):
            return False
        for market, latest in old_cursor.get("latest", {}).items():
            current = new_cursor.get("latest", {}).get(market)
            if not isinstance(latest, (int, float)) or not isinstance(current, (int, float)) or current < latest:
                return False
        for market, through in old_cursor.get("collected_through", old_cursor.get("latest", {})).items():
            current = new_cursor.get("collected_through", {}).get(market, new_cursor.get("latest", {}).get(market))
            if not isinstance(through, int) or not isinstance(current, int) or current < through:
                return False
    return True


def _intervals(value):
    if not isinstance(value, list):
        raise PublishError("INVALID_RECOVERY_INTERVALS")
    for interval in value:
        if (not isinstance(interval, list) or len(interval) != 2
                or any(not isinstance(x, int) or isinstance(x, bool) or x < 0 for x in interval)
                or interval[0] >= interval[1]):
            raise PublishError("INVALID_RECOVERY_INTERVALS")
    return value


def _covered(interval, covering):
    start, end = interval
    for lower, upper in sorted(covering):
        if lower > start:
            return False
        if upper > start:
            start = upper
        if start >= end:
            return True
    return False


def _checkpoint(repo, payload, entries, market, reference, cache):
    """Resolve references only against immutable bytes in this push or main."""
    if not isinstance(reference, dict):
        raise PublishError("INVALID_RECOVERY_REFERENCE")
    filename = reference.get("file", "")
    if not isinstance(filename, str) or not re.fullmatch(
            r"runs/[A-Za-z0-9][A-Za-z0-9_.-]{0,100}/polymarket/checkpoints\.jsonl", filename):
        raise PublishError("INVALID_RECOVERY_REFERENCE")
    path = f"{ROOT}/{filename}"
    if path not in cache:
        raw = payload.get(path)
        if raw is None and path in entries:
            raw = _git(repo, "cat-file", "blob", entries[path]).stdout
        if raw is None or not raw.endswith(b"\n"):
            raise PublishError("RECOVERY_BYTES_MISSING")
        cache[path] = (raw, [(_json(line), hashlib.sha256(line + b"\n").hexdigest())
                             for line in raw.splitlines()])
    raw, lines = cache[path]
    if hashlib.sha256(raw).hexdigest() != reference.get("file_sha256"):
        raise PublishError("RECOVERY_FILE_HASH_MISMATCH")
    successful = []
    for checkpoint, digest in lines:
        if checkpoint.get("market_id") != market:
            continue
        if checkpoint.get("schema") != "market-interval-checkpoint-v1":
            raise PublishError("INVALID_RECOVERY_CHECKPOINT")
        successful += _intervals([[checkpoint.get("requested_start"), checkpoint.get("requested_end")]])
        if digest != reference.get("checkpoint_sha256"):
            continue
        latest = checkpoint.get("latest")
        if not isinstance(latest, int) or isinstance(latest, bool) or latest < 0:
            raise PublishError("INVALID_RECOVERY_HIGHWATER")
        through = checkpoint.get("collected_through", latest)
        if not isinstance(through, int) or isinstance(through, bool) or through < latest:
            raise PublishError("INVALID_RECOVERY_INTERVAL_HIGHWATER")
        _time(checkpoint.get("received_at"))
        _intervals(checkpoint.get("pending_backfills"))
        for key in ("received_at", "latest"):
            if key in reference and reference[key] != checkpoint[key]:
                raise PublishError("RECOVERY_REFERENCE_METADATA_MISMATCH")
        return checkpoint, successful
    raise PublishError("RECOVERY_CHECKPOINT_HASH_MISSING")


def _preserves_recovery(repo, payload, entries, new, old):
    """A later completion clock alone cannot certify partial-run progress.

    Missing gaps must be backed by completed request intervals. Reintroduced
    old gaps mean a stale snapshot; retain main's entire derived set instead.
    The current overlap window may legitimately become a pending new gap.
    """
    cache = {}
    report = _json(payload[f"{ROOT}/health.json"])
    cutoff = int(_time(report.get("started_at", report["completed_at"])).timestamp())
    previous = old.get("sources", {}).get("polymarket", {}).get("recovery", {})
    candidate = new.get("sources", {}).get("polymarket", {}).get("recovery", {})
    if not isinstance(previous, dict) or not isinstance(candidate, dict):
        raise PublishError("INVALID_RECOVERY_REFERENCE")
    if not set(previous).issubset(candidate):
        return False
    for market, reference in candidate.items():
        after, requests = _checkpoint(repo, payload, entries, market, reference, cache)
        if market not in previous:
            continue
        before, _ = _checkpoint(repo, payload, entries, market, previous[market], cache)
        if (after["latest"] < before["latest"]
                or after.get("collected_through", after["latest"]) < before.get("collected_through", before["latest"])
                or _time(after["received_at"]) < _time(before["received_at"])):
            return False
        old_gaps, new_gaps = before["pending_backfills"], after["pending_backfills"]
        if any(not _covered(gap, new_gaps + requests) for gap in old_gaps):
            return False
        # A newer stale run must not resurrect already completed old backfills.
        overlap_start = max(0, before.get("collected_through", before["latest"]) - 7200)
        # An empty current request can leave a new terminal gap even when an
        # earlier backfill succeeds and emits this checkpoint. That gap reaches
        # the run's request cutoff, not necessarily its last observed price.
        allowable = old_gaps + [[overlap_start, max(overlap_start, cutoff)]]
        if any(not _covered(gap, allowable) for gap in new_gaps):
            return False
        old_points = {tuple(row) for row in before.get("recent_observations", [])
                      if row[0] >= after["latest"] - 10800}
        if not old_points.issubset({tuple(row) for row in after.get("recent_observations", [])}):
            return False
    return True


def _attempt_clock(repo, payload, entries, market, reference, cache):
    """An attempt is scheduling evidence, never evidence of source success."""
    if not isinstance(reference, dict):
        raise PublishError("INVALID_ATTEMPT_REFERENCE")
    filename = reference.get("file", "")
    if not isinstance(filename, str) or not re.fullmatch(
            r"runs/[A-Za-z0-9][A-Za-z0-9_.-]{0,100}/polymarket/market-attempts\.jsonl", filename):
        raise PublishError("INVALID_ATTEMPT_REFERENCE")
    path = f"{ROOT}/{filename}"
    if path not in cache:
        raw = payload.get(path)
        if raw is None and path in entries:
            raw = _git(repo, "cat-file", "blob", entries[path]).stdout
        if raw is None or not raw.endswith(b"\n"):
            raise PublishError("ATTEMPT_BYTES_MISSING")
        cache[path] = (raw, {hashlib.sha256(line + b"\n").hexdigest(): _json(line)
                             for line in raw.splitlines()})
    raw, records = cache[path]
    record = records.get(reference.get("attempt_sha256"))
    if (hashlib.sha256(raw).hexdigest() != reference.get("file_sha256") or not record
            or record.get("schema") != "market-attempt-v1" or record.get("market_id") != market
            or record.get("attempted_at") != reference.get("attempted_at")):
        raise PublishError("ATTEMPT_REFERENCE_MISMATCH")
    return _time(record["attempted_at"])


def _merge_attempts(repo, payload, entries, new, old, base, completed_at):
    """Union verified scheduling refs by clock; retain all other base fields.

    A stale run can still record previously unattempted markets. Merging these
    clocks cannot advance success, erase recovery gaps, or report healthy data.
    """
    candidate = new.get("sources", {}).get("polymarket", {}).get("market_attempts", {})
    previous = old.get("sources", {}).get("polymarket", {}).get("market_attempts", {})
    if not isinstance(candidate, dict) or not isinstance(previous, dict):
        raise PublishError("INVALID_ATTEMPT_REFERENCE")
    cache, clocks, merged = {}, {}, dict(previous)
    for market, reference in previous.items():
        clocks[market] = _attempt_clock(repo, payload, entries, market, reference, cache)
    for market, reference in candidate.items():
        attempted = _attempt_clock(repo, payload, entries, market, reference, cache)
        if attempted > completed_at:
            raise PublishError("ATTEMPT_AFTER_RUN_COMPLETION")
        if market not in clocks or attempted > clocks[market]:
            merged[market], clocks[market] = reference, attempted
    result = json.loads(json.dumps(base))
    if merged:
        result.setdefault("sources", {}).setdefault("polymarket", {})["market_attempts"] = merged
    return result, sum(previous.get(key) != value for key, value in merged.items())


def _select(repo, payload, entries, run_id):
    prefix = f"{ROOT}/runs/{run_id}/"
    current = {path: _git(repo, "cat-file", "blob", oid).stdout
               for path, oid in entries.items()
               if path.startswith(prefix) or path in {f"{ROOT}/{n}" for n in DERIVED}}
    for path, raw in current.items():
        if path.startswith(prefix) and payload.get(path) != raw:
            raise PublishError("IMMUTABLE_RUN_COLLISION")
    selected = dict(payload)
    health_path, state_path = f"{ROOT}/health.json", f"{ROOT}/state.json"
    reason = None
    if health_path in current:
        old, new = _json(current[health_path]), _json(payload[health_path])
        if _time(old.get("completed_at")) > _time(new.get("completed_at")):
            reason = "REMOTE_COMPLETION_NEWER"
        elif _time(old.get("completed_at")) == _time(new.get("completed_at")) and old != new:
            reason = "REMOTE_COMPLETION_TIED"
    if not reason and state_path in current:
        if not _preserves_state(_json(payload[state_path]), _json(current[state_path])):
            reason = "CANDIDATE_STATE_WOULD_SHRINK"
    if not reason:
        try:
            if not _preserves_recovery(repo, payload, entries, _json(payload[state_path]),
                                       _json(current[state_path]) if state_path in current else {}):
                reason = "CANDIDATE_RECOVERY_WOULD_REGRESS"
        except (PublishError, TypeError, KeyError, IndexError):
            # Keep the immutable run even when a recovery pointer cannot be
            # proved safe; never fabricate a healthy source or advance success.
            reason = "RECOVERY_REFERENCE_NOT_VERIFIED"
    status_path = f"{ROOT}/status.json"
    if not reason and status_path in current and status_path in payload:
        if _time(_json(current[status_path]).get("generated_at")) > _time(_json(payload[status_path]).get("generated_at")):
            reason = "REMOTE_STATUS_NEWER"
    new_state = _json(payload[state_path])
    old_state = _json(current[state_path]) if state_path in current else {"schema": "collection-state-v1", "sources": {}}
    base_state = old_state if reason else new_state
    merged_state, attempts_merged = None, 0
    try:
        merged_state, attempts_merged = _merge_attempts(repo, payload, entries, new_state, old_state,
                                                      base_state, _time(_json(payload[health_path])["completed_at"]))
    except (PublishError, TypeError, KeyError, IndexError):
        reason = "ATTEMPT_REFERENCE_NOT_VERIFIED"
    if reason:
        for name in DERIVED:
            selected.pop(f"{ROOT}/{name}", None)
    if merged_state is not None and merged_state != base_state and (not reason or state_path in current):
        selected[state_path] = (json.dumps(merged_state, sort_keys=True, allow_nan=False, separators=(",", ":")) + "\n").encode()
    if reason and state_path not in selected:
        attempts_merged = 0
    return {path: raw for path, raw in selected.items() if current.get(path) != raw}, reason, attempts_merged


def publish(repo, output="data/operations", run_id=None, *, branch="main", dry_run=False, attempts=3):
    repo = Path(repo).resolve()
    if branch != "main" or not 1 <= attempts <= 3:
        raise PublishError("UNSUPPORTED_BRANCH_OR_RETRY_LIMIT")
    top = Path(_git(repo, "rev-parse", "--show-toplevel").stdout.decode().strip()).resolve()
    if repo != top:
        raise PublishError("REPOSITORY_ROOT_REQUIRED")
    payload = snapshot(repo, output, run_id)
    for attempt in range(1, attempts + 1):
        _git(repo, "fetch", "--no-tags", "origin", "refs/heads/main")
        base = _git(repo, "rev-parse", "FETCH_HEAD").stdout.decode().strip()
        selected, skipped, attempts_merged = _select(repo, payload, _remote_files(repo, base), run_id)
        result = {"run_id": run_id, "attempts": attempt, "files_changed": len(selected),
                  "derived_skipped": skipped, "attempt_references_merged": attempts_merged, "base_commit": base}
        if not selected:
            return {**result, "status": "UNCHANGED", "commit": base}
        if dry_run:
            return {**result, "status": "DRY_RUN", "paths": sorted(selected)}
        with tempfile.TemporaryDirectory(prefix="operations-index-") as temp:
            env = {"GIT_INDEX_FILE": str(Path(temp) / "index"),
                   "GIT_AUTHOR_NAME": "Source collection", "GIT_AUTHOR_EMAIL": "collection@users.noreply.github.com",
                   "GIT_COMMITTER_NAME": "Source collection", "GIT_COMMITTER_EMAIL": "collection@users.noreply.github.com"}
            _git(repo, "read-tree", base, env=env)
            for path, raw in sorted(selected.items()):
                oid = _git(repo, "hash-object", "-w", "--stdin", data=raw, env=env).stdout.decode().strip()
                _git(repo, "update-index", "--add", "--cacheinfo", "100644", oid, path, env=env)
            tree = _git(repo, "write-tree", env=env).stdout.decode().strip()
            commit = _git(repo, "commit-tree", tree, "-p", base,
                          data=f"Archive source collection {run_id}\n".encode(), env=env).stdout.decode().strip()
        pushed = _git(repo, "push", "--porcelain", "origin", f"{commit}:refs/heads/main", check=False)
        if not pushed.returncode:
            return {**result, "status": "PUBLISHED", "commit": commit}
        # Never echo raw remote output: authenticated URLs can appear in errors.
        rejection = pushed.stdout + pushed.stderr
        if b"[rejected]" not in rejection or not any(s in rejection for s in (b"non-fast-forward", b"fetch first")):
            raise PublishError("PUSH_FAILED_NO_RETRY")
    raise PublishError("CONCURRENT_UPDATES_RETRY_LIMIT")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, default=Path("data/operations"))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--branch", choices=["main"], default="main")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        result = publish(args.repo, args.output, args.run_id, branch=args.branch, dry_run=args.dry_run)
    except PublishError as error:
        print(json.dumps({"status": "FAILED", "error_code": str(error)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
