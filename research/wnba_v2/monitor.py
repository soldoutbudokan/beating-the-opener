"""Read-only operating health for a locally restored private shadow ledger.

This command checks a clean Git snapshot; it cannot establish that the snapshot
is the latest remote head or that GitHub's scheduler is enabled. It never fetches,
publishes, restores a model, or computes prospective performance. Stored forecast
validation may reproduce prices from their saved distributions.
"""
from __future__ import annotations

import argparse
from datetime import timedelta
import json
from pathlib import Path

from . import runtime, shadow


DEFAULT_MAX_AGE_SECONDS = 3 * 60 * 60


def _require(condition, code):
    if not condition:
        raise runtime.RuntimeFailure(code)


def _count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _checkout(repo):
    """Check HEAD, index, ignored evidence, and working bytes without Git writes."""
    repo = Path(repo)
    shadow._safe_path(repo)
    top = runtime.git(repo, "rev-parse", "--show-toplevel").stdout.decode().strip()
    _require(Path(top).resolve() == repo.resolve(), "CHECKOUT_ROOT_REQUIRED")
    head = runtime._oid(runtime.git(repo, "rev-parse", "HEAD").stdout)
    dirty = runtime.git(repo, "status", "--porcelain=v1", "--untracked-files=all",
                        env={"GIT_OPTIONAL_LOCKS": "0"}).stdout
    _require(not dirty, "CHECKOUT_NOT_CLEAN")
    files = {runtime.PREFIX + "/" + path: raw
             for path, raw in runtime.regular_tree(repo / runtime.PREFIX).items()}
    archive = runtime.GitArchive(repo, "unused/local-identity")
    _require(set(files) == set(archive.entries(head)), "CHECKOUT_EVIDENCE_PATH_MISMATCH")
    archive.verify(head, files)
    return head, files


def _run(record, run_id, *, study, batches, repository, now):
    _require(record.get("schema") == "wnba-shadow-runtime-run-v1"
             and record.get("run_id") == run_id, "RUN_IDENTITY_MISMATCH")
    shadow.identifier(run_id, "run_id")
    started = shadow.instant(record.get("started_at"), "run started_at")
    completed = shadow.instant(record.get("completed_at"), "run completed_at")
    _require(shadow.instant(study["starts_at"]) <= started <= completed <= now,
             "RUN_CLOCK_INVALID")
    _require(record.get("status") in {"OK", "FAILED", "DEGRADED"}, "RUN_STATUS_INVALID")
    _require(all(type(record.get(key)) is int and record[key] == 0
                 for key in ("parameter_refits", "wagers", "automatic_evaluations")),
             "RUN_ACTIVITY_CONTRACT_VIOLATION")
    staged, seal, publication = (record.get(key) for key in ("staged", "seal", "batch_publication"))
    batch = batches.get(run_id)
    if staged is not None:
        _require(isinstance(staged, dict) and staged.get("batch_id") == run_id
                 and _count(staged.get("forecasts")), "RUN_STAGE_IDENTITY_MISMATCH")
        shadow.sha(staged.get("manifest_sha256"), "staged manifest hash")
        if batch is not None:
            manifest, receipt = batch
            _require(staged["manifest_sha256"] == shadow.digest(shadow.encode(manifest))
                     and staged["forecasts"] == manifest["forecasts"], "RUN_STAGE_BYTES_MISMATCH")
            created = shadow.instant(manifest["created_at"])
            _require(created <= completed
                     and (started <= created or staged.get("capture_status") == "RESUMED"),
                     "RUN_BATCH_CLOCK_INVALID")
    if publication is not None:
        _require(isinstance(publication, dict)
                 and publication.get("repository_full_name") == repository
                 and runtime.SHA1.fullmatch(publication.get("commit_sha", "")) is not None,
                 "RUN_PUBLICATION_IDENTITY_MISMATCH")
        durable = shadow.instant(publication.get("durable_at"), "publication durable_at")
        _require(started <= durable <= completed, "RUN_PUBLICATION_CLOCK_INVALID")
    if seal is not None:
        _require(isinstance(seal, dict) and seal.get("batch_id") == run_id,
                 "RUN_SEAL_IDENTITY_MISMATCH")
        durable = shadow.instant(seal.get("durable_at"), "run seal durable_at")
        _require(durable <= completed and (started <= durable or seal.get("resumed") is True),
                 "RUN_SEAL_CLOCK_INVALID")
        if batch is not None and batch[1] is not None:
            receipt = batch[1]
            _require(durable == shadow.instant(receipt["durable_at"]), "RUN_SEAL_CLOCK_MISMATCH")
            if seal.get("resumed") is not True:
                _require(publication is not None
                         and publication["commit_sha"] == receipt["commit_sha"]
                         and publication["durable_at"] == receipt["durable_at"],
                         "RUN_PUBLICATION_SEAL_MISMATCH")
                _require(_count(seal.get("eligible_before_tip")) and _count(seal.get("late_seals"))
                         and seal["eligible_before_tip"] + seal["late_seals"] == batch[0]["forecasts"],
                         "RUN_SEAL_COUNTS_INVALID")
    if record["status"] == "OK":
        _require(batch is not None and batch[1] is not None
                 and staged is not None and seal is not None and publication is not None,
                 "SUCCESSFUL_RUN_EVIDENCE_MISSING")
        _require(record.get("error_code") is None
                 and staged.get("capture_status") in {"OK", "NO_EVENTS", "RESUMED", "ENDPOINT_REACHED"},
                 "SUCCESSFUL_RUN_STATUS_CONFLICT")
        if staged["capture_status"] in {"NO_EVENTS", "ENDPOINT_REACHED"}:
            _require(staged["forecasts"] == 0, "EMPTY_CAPTURE_HAS_FORECASTS")
    return {"run_id": run_id, "started_at": shadow.stamp(started),
            "completed_at": shadow.stamp(completed), "status": record["status"],
            "capture_status": None if staged is None else staged.get("capture_status")}


def inspect(data_repo, repository, *, now=None, max_age_seconds=DEFAULT_MAX_AGE_SECONDS):
    """Return a verified snapshot report, raising on invalid or altered evidence."""
    now = shadow._aware_now(now)
    shadow.number(max_age_seconds, "max_age_seconds", minimum=1)
    _require(isinstance(repository, str) and runtime.REPOSITORY.fullmatch(repository) is not None,
             "REPOSITORY_IDENTITY_INVALID")
    repo = Path(data_repo)
    head, files = _checkout(repo)
    _require(runtime.CONFIG in files and runtime.STUDY in files, "DEPLOYMENT_NOT_INITIALIZED")
    config = runtime.document(files[runtime.CONFIG])
    _require(config.get("schema") == "wnba-shadow-runtime-v1"
             and config.get("repository_full_name") == repository
             and config.get("ledger_path") == runtime.PREFIX + "/ledger"
             and isinstance(config.get("code_commit"), str)
             and runtime.SHA1.fullmatch(config["code_commit"]) is not None,
             "RUNTIME_CONFIGURATION_MISMATCH")
    bundle_path = runtime.safe_relative(config.get("bundle_path"))
    _require(bundle_path.startswith(runtime.PREFIX + "/frozen/") and bundle_path in files,
             "FROZEN_BUNDLE_MISSING")
    ledger = shadow.Ledger(repo / config["ledger_path"])
    study = ledger.study(now)
    _require(shadow.digest(files[bundle_path]) == study["bundle_sha256"], "BUNDLE_HASH_MISMATCH")
    archive = runtime.GitArchive(repo, repository)
    bundle_receipt = study.get("bundle_receipt")
    _require(isinstance(bundle_receipt, dict) and bundle_receipt.get("artifact_path") == bundle_path,
             "GIT_BUNDLE_RECEIPT_REQUIRED")
    archive.verify_receipt(bundle_receipt, {bundle_path: files[bundle_path]}, head=head)
    archive.verify_ledger(ledger, head=head, now=now)

    batches, unsealed, interrupted = {}, [], []
    for directory in sorted((ledger.root / "batches").glob("*")):
        _require(directory.is_dir(), "BATCH_DIRECTORY_INVALID")
        shadow.identifier(directory.name, "batch_id")
        if not (directory / "manifest.json").exists():
            interrupted.append(directory.name)
            continue
        manifest, _ = ledger.batch(directory.name, now)
        seal_path = directory / "seal.json"
        receipt = shadow.load(seal_path) if seal_path.exists() else None
        batches[directory.name] = (manifest, receipt)
        if receipt is None:
            unsealed.append(directory.name)

    runs, incomplete_runs = [], []
    for directory in sorted((repo / runtime.PREFIX / "runs").glob("*")):
        _require(directory.is_dir(), "RUN_DIRECTORY_INVALID")
        shadow.identifier(directory.name, "run_id")
        path = directory / "runtime.json"
        if not path.exists():
            incomplete_runs.append(directory.name)
            continue
        record = shadow.load(path)
        runs.append(_run(record, directory.name, study=study, batches=batches,
                         repository=repository, now=now))
        publication = record.get("batch_publication")
        if publication is not None and directory.name in batches:
            prefix = config["ledger_path"] + "/batches/" + directory.name + "/"
            payload = {path: raw for path, raw in files.items()
                       if path.startswith(prefix) and path != prefix + "seal.json"}
            archive.verify_receipt({**publication, "provider": "github",
                                    "artifact_path": prefix + "manifest.json",
                                    "stored_artifact_sha256": shadow.digest(payload[prefix + "manifest.json"])},
                                   payload, head=head)
    runs.sort(key=lambda r: (shadow.instant(r["completed_at"]), r["run_id"]))
    latest = runs[-1] if runs else None
    failures = [r for r in runs if r["status"] != "OK"]
    last_ok = next((r for r in reversed(runs) if r["status"] == "OK"), None)
    age = None if latest is None else (now - shadow.instant(latest["completed_at"])).total_seconds()
    run_ids = {r["run_id"] for r in runs}
    orphan_batches = sorted((set(batches) | set(interrupted)) - run_ids)

    # Only lifecycle and denominator accounting: never Ledger.evaluate or the
    # endpoint scorer. Forecast validation checks its saved distribution bytes.
    progress = ledger.status(now)
    progress["resolved_forecasts"] = progress["forecasts"] - progress["unresolved"]
    rows, _ = ledger.population(now)
    settlements = ledger.settlements({row["forecast_id"]: row for row in rows}, now)
    denominator = {"played": 0, "dnp": 0, "unknown": 0, "pushes": 0,
                   "settled_played_nonpush": 0}
    settled_dates = set()
    for row in rows:
        settlement = settlements.get(row["forecast_id"], {})
        participation = settlement.get("participation", "unknown")
        denominator[participation] += 1
        if participation == "played":
            if settlement["actual_points"] == row["quote"]["line"]:
                denominator["pushes"] += 1
            else:
                denominator["settled_played_nonpush"] += 1
                settled_dates.add(shadow.instant(row["tip_at"]).astimezone(shadow.EASTERN).date())
    denominator.update(settled_nonpush_et_dates=len(settled_dates),
                       minimum_settled_played_nonpush=shadow.MIN_SETTLED,
                       minimum_settled_nonpush_et_dates=shadow.MIN_DATES,
                       maximum_unresolved_fraction=shadow.MAX_UNKNOWN_FRACTION,
                       unresolved_fraction=None if not rows else denominator["unknown"] / len(rows))
    endpoint = progress["endpoint_at"]
    evaluation_due = None if endpoint is None else shadow.stamp(
        shadow.instant(endpoint) + timedelta(days=shadow.SETTLEMENT_DAYS))
    issues = []
    if latest is None:
        issues.append("NO_TERMINAL_RUN")
    elif age > max_age_seconds:
        issues.append("STALE_TERMINAL_RUN")
    if latest is not None and latest["status"] != "OK":
        issues.append("LATEST_RUN_" + latest["status"])
    for code, values in (("UNSEALED_BATCHES", unsealed), ("INTERRUPTED_BATCHES", interrupted),
                         ("BATCHES_WITHOUT_TERMINAL_RUN", orphan_batches),
                         ("INCOMPLETE_RUN_DIRECTORIES", incomplete_runs)):
        if values:
            issues.append(code)
    if progress["status"] == "EVALUATION_INTERRUPTED":
        issues.append("EVALUATION_INTERRUPTED")
    # Detect concurrent changes before returning evidence from a mixed snapshot.
    final_head, final_files = _checkout(repo)
    _require(final_head == head and final_files == files, "CHECKOUT_CHANGED_DURING_INSPECTION")
    return {"schema": "wnba-shadow-monitor-v1", "generated_at": shadow.stamp(now),
            "repository_full_name": repository, "snapshot_commit": head,
            "verification_scope": "local_clean_git_snapshot_and_reachable_receipts",
            "remote_freshness_verified": False, "scheduler_enabled_verified": False,
            "status": "HEALTHY" if not issues else "UNHEALTHY", "issues": issues,
            "qualified": False, "performance_evaluated": False,
            "study_id": study["study_id"], "code_commit": config["code_commit"],
            "collection": {"terminal_runs": len(runs), "latest_run": latest,
                           "latest_successful_run": last_ok,
                           "latest_unsuccessful_run": failures[-1] if failures else None,
                           "latest_run_age_seconds": age,
                           "max_age_seconds": max_age_seconds,
                           "threshold_basis": "operational_freshness_only"},
            "batches": {"complete": len(batches),
                        "sealed": sum(receipt is not None for _, receipt in batches.values()),
                        "unsealed": unsealed, "interrupted": interrupted,
                        "without_terminal_run": orphan_batches,
                        "incomplete_run_directories": incomplete_runs},
            "progress": {key: progress[key] for key in
                         ("status", "forecasts", "target_forecasts", "cap_at", "endpoint_at",
                          "et_dates", "resolved_forecasts", "unresolved", "exclusions")},
            "settlement_denominators": denominator,
            "evaluation_due_at": evaluation_due,
            "next_action": "REVIEW_OPERATING_ISSUES" if issues
                           else "REGISTERED_EVALUATION_REVIEW" if progress["status"] == "EVALUATION_DUE"
                           else "REVIEW_RECORDED_DECISION" if progress["status"] == "EVALUATED"
                           else "WAIT_FOR_REGISTERED_SETTLEMENT" if progress["status"] == "WAITING_FOR_SETTLEMENT"
                           else "CONTINUE_REGISTERED_COLLECTION",
            "basis": "Collection health and counts only; no market advantage or qualification inferred."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-repo", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--max-age-seconds", type=float, default=DEFAULT_MAX_AGE_SECONDS,
                        help="Operational receipt freshness limit; does not change any study gate")
    parser.add_argument("--output", type=Path,
                        help="Optional new private report file outside the evidence checkout")
    args = parser.parse_args(argv)
    try:
        if args.output is not None:
            shadow._safe_path(args.output.parent)
            _require(not args.output.resolve().is_relative_to(args.data_repo.resolve()),
                     "REPORT_MUST_BE_OUTSIDE_EVIDENCE_CHECKOUT")
        report = inspect(args.data_repo, args.repository, max_age_seconds=args.max_age_seconds)
        if args.output is not None:
            shadow.write_once(args.output, shadow.encode(report))
        print(json.dumps(report, sort_keys=True, allow_nan=False))
        return 0 if report["status"] == "HEALTHY" else 2
    except (runtime.RuntimeFailure, ValueError, TypeError, KeyError, OSError) as error:
        code = str(error) if isinstance(error, runtime.RuntimeFailure) else type(error).__name__
        print(json.dumps({"schema": "wnba-shadow-monitor-v1", "status": "INVALID_EVIDENCE",
                          "error_code": code, "qualified": False, "performance_evaluated": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
