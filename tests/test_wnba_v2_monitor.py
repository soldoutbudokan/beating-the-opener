"""Operating health checks use real local Git and synthetic private evidence."""
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import timedelta
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from research.wnba_v2 import monitor, runtime, shadow
from tests.test_wnba_v2_runtime import GitFixture, command
from tests.test_wnba_v2_shadow import NOW, RAW, RESULT, forecast, refresh, study, settlement


class MonitorTests(GitFixture):
    def setUp(self):
        super().setUp()
        self.repository = "owner/private-study"
        self.bundle_path = runtime.PREFIX + "/frozen/candidate.zip"
        self.bundle = b"opaque synthetic frozen artifact; monitor does not restore it"
        self.put(self.bundle_path, self.bundle)
        bundle_commit = self.commit()
        self.study = study()
        self.study.update(bundle_sha256=shadow.digest(self.bundle), qualified=False,
                          bundle_receipt={"provider": "github", "repository_full_name": self.repository,
                                          "artifact_path": self.bundle_path, "commit_sha": bundle_commit,
                                          "stored_artifact_sha256": shadow.digest(self.bundle),
                                          "durable_at": self.study["bundle_sealed_at"]})
        self.config = {"schema": "wnba-shadow-runtime-v1", "repository_full_name": self.repository,
                       "code_commit": "c" * 40, "bundle_path": self.bundle_path,
                       "ledger_path": runtime.PREFIX + "/ledger"}
        self.put(runtime.CONFIG, self.config)
        self.put(runtime.STUDY, self.study)
        self.commit()
        self.ledger = shadow.Ledger(self.local / self.config["ledger_path"])
        self.checked_at = NOW + timedelta(minutes=3)

    def put(self, path, value):
        target = self.local / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(value if isinstance(value, bytes) else shadow.encode(value))

    def commit(self):
        command(self.local, "add", ".")
        command(self.local, "commit", "-m", "Synthetic monitor evidence")
        return command(self.local, "rev-parse", "HEAD")

    def add_run(self, run_id="run-1", *, rows=None, seal=True, terminal=True, status="OK", now=NOW):
        rows = [] if rows is None else deepcopy(rows)
        for row in rows:
            row["bundle_sha256"] = self.study["bundle_sha256"]
        staged = self.ledger.stage(run_id, rows, {"events.body": RAW}, now=now,
                                   attempts=[{"status": "OK" if rows else "NO_EVENTS"}])
        staged["capture_status"] = "OK" if rows else "NO_EVENTS"
        publication = {"repository_full_name": self.repository, "commit_sha": self.commit(),
                       "durable_at": shadow.stamp(now + timedelta(minutes=1)), "files": 4}
        sealed = None
        if seal:
            receipt = {**publication, "provider": "github", "schema": "wnba-shadow-seal-v1",
                       "batch_id": run_id, "manifest_sha256": staged["manifest_sha256"],
                       "artifact_path": self.config["ledger_path"] + "/batches/" + run_id + "/manifest.json",
                       "stored_artifact_sha256": staged["manifest_sha256"]}
            sealed = self.ledger.seal(run_id, receipt, now + timedelta(minutes=1))
        record = {"schema": "wnba-shadow-runtime-run-v1", "run_id": run_id,
                  "started_at": shadow.stamp(now), "completed_at": shadow.stamp(now + timedelta(minutes=2)),
                  "status": status, "error_code": None if status == "OK" else "SyntheticFailure",
                  "staged": staged, "seal": sealed, "batch_publication": publication,
                  "parameter_refits": 0, "wagers": 0, "automatic_evaluations": 0}
        if terminal:
            self.put(runtime.PREFIX + "/runs/" + run_id + "/runtime.json", record)
        if seal or terminal:
            self.commit()
        return record

    def inspect(self, **kwargs):
        return monitor.inspect(self.local, self.repository, now=self.checked_at, **kwargs)

    def change_run(self, record):
        self.put(runtime.PREFIX + "/runs/run-1/runtime.json", record)
        self.commit()

    def test_healthy_empty_scan_is_not_qualification_and_never_scores_or_writes_git(self):
        self.add_run()
        before = runtime.regular_tree(self.local)
        original = runtime.git
        calls = []
        def readonly(repo, *args, **kwargs):
            calls.append(args[0])
            self.assertNotIn(args[0], {"fetch", "push", "add", "commit", "update-ref", "checkout", "reset"})
            return original(repo, *args, **kwargs)
        with patch.object(runtime, "git", side_effect=readonly), \
                patch.object(runtime, "run_once", side_effect=AssertionError("runtime called")), \
                patch.object(shadow.Ledger, "evaluate", side_effect=AssertionError("evaluation called")), \
                patch.object(shadow, "evaluate_saved_rows", side_effect=AssertionError("scorer called")), \
                patch("research.wnba_v2.future.FutureOnlyModel", side_effect=AssertionError("model restored")):
            report = self.inspect()
        self.assertEqual(runtime.regular_tree(self.local), before)
        self.assertIn("merge-base", calls)
        self.assertEqual(report["status"], "HEALTHY")
        self.assertFalse(report["qualified"])
        self.assertFalse(report["performance_evaluated"])
        self.assertFalse(report["remote_freshness_verified"])
        self.assertEqual(report["progress"]["forecasts"], 0)
        self.assertEqual(report["batches"]["sealed"], 1)
        self.assertEqual(report["collection"]["latest_run"]["capture_status"], "NO_EVENTS")

    def test_stale_receipt_threshold_does_not_change_the_study(self):
        self.add_run()
        self.checked_at = NOW + timedelta(hours=4)
        report = self.inspect()
        self.assertEqual(report["issues"], ["STALE_TERMINAL_RUN"])
        self.assertEqual(self.inspect(max_age_seconds=5 * 3600)["status"], "HEALTHY")
        self.assertEqual(shadow.load(self.local / runtime.STUDY), self.study)

    def test_future_and_naive_completion_clocks_fail(self):
        record = self.add_run()
        for clock in (shadow.stamp(NOW + timedelta(days=1)), "2026-09-08T14:02:00"):
            record["completed_at"] = clock
            self.change_run(record)
            with self.subTest(clock=clock), self.assertRaises((runtime.RuntimeFailure, ValueError)):
                self.inspect()

    def test_runtime_repository_and_run_identity_mismatches_fail(self):
        record = self.add_run()
        with self.assertRaisesRegex(runtime.RuntimeFailure, "RUNTIME_CONFIGURATION_MISMATCH"):
            monitor.inspect(self.local, "other/repository", now=self.checked_at)
        record["run_id"] = "different-run"
        self.change_run(record)
        with self.assertRaisesRegex(runtime.RuntimeFailure, "RUN_IDENTITY_MISMATCH"):
            self.inspect()

    def test_unsealed_batch_and_missing_terminal_are_visible_even_when_empty(self):
        self.add_run(seal=False, terminal=False)
        report = self.inspect()
        self.assertEqual(report["status"], "UNHEALTHY")
        self.assertIn("UNSEALED_BATCHES", report["issues"])
        self.assertIn("BATCHES_WITHOUT_TERMINAL_RUN", report["issues"])
        self.assertIn("NO_TERMINAL_RUN", report["issues"])
        self.assertEqual(report["batches"]["unsealed"], ["run-1"])

    def test_interrupted_batch_directory_cannot_disappear_from_health(self):
        self.add_run()
        self.put(self.config["ledger_path"] + "/batches/interrupted/attempts.jsonl", b"{}\n")
        self.commit()
        report = self.inspect()
        self.assertIn("INTERRUPTED_BATCHES", report["issues"])
        self.assertEqual(report["progress"]["exclusions"]["interrupted_batches"], 1)

    def test_current_failure_remains_unhealthy_after_previous_success(self):
        self.add_run()
        self.add_run("run-2", status="FAILED", now=NOW + timedelta(minutes=3))
        self.checked_at = NOW + timedelta(minutes=6)
        report = self.inspect()
        self.assertIn("LATEST_RUN_FAILED", report["issues"])
        self.assertEqual(report["collection"]["latest_successful_run"]["run_id"], "run-1")
        self.assertEqual(report["collection"]["latest_unsuccessful_run"]["run_id"], "run-2")

    def test_degraded_then_recovered_retains_last_failure_without_blocking_health(self):
        self.add_run(status="DEGRADED")
        self.assertIn("LATEST_RUN_DEGRADED", self.inspect()["issues"])
        self.add_run("run-2", now=NOW + timedelta(minutes=3))
        self.checked_at = NOW + timedelta(minutes=6)
        report = self.inspect()
        self.assertEqual(report["status"], "HEALTHY")
        self.assertEqual(report["collection"]["latest_unsuccessful_run"]["run_id"], "run-1")

    def test_clean_committed_bundle_tampering_still_fails(self):
        self.add_run()
        self.put(self.bundle_path, b"different bytes")
        self.commit()
        with self.assertRaisesRegex(runtime.RuntimeFailure, "BUNDLE_HASH_MISMATCH"):
            self.inspect()

    def test_clean_committed_seal_with_unreachable_commit_fails(self):
        self.add_run()
        path = self.config["ledger_path"] + "/batches/run-1/seal.json"
        seal = shadow.load(self.local / path)
        seal["commit_sha"] = "0" * 40
        self.put(path, seal)
        self.commit()
        with self.assertRaisesRegex(runtime.RuntimeFailure, "RECEIPT_COMMIT_NOT_REACHABLE"):
            self.inspect()

    def test_dirty_and_ignored_extra_evidence_fail(self):
        self.add_run()
        self.put(runtime.PREFIX + "/unexpected.txt", b"untracked")
        with self.assertRaisesRegex(runtime.RuntimeFailure, "CHECKOUT_NOT_CLEAN"):
            self.inspect()
        (self.local / runtime.PREFIX / "unexpected.txt").unlink()
        self.put(".gitignore", b"ignored-private-evidence\n")
        self.commit()
        self.put(runtime.PREFIX + "/ignored-private-evidence", b"ignored")
        with self.assertRaisesRegex(runtime.RuntimeFailure, "CHECKOUT_EVIDENCE_PATH_MISMATCH"):
            self.inspect()

    def test_admitted_and_resolved_counts_without_performance_calculation(self):
        self.add_run(rows=[forecast()])
        row = self.ledger.population(self.checked_at)[0][0]
        grade = settlement(row)
        self.checked_at = shadow.instant(grade["observed_at"]) + timedelta(minutes=1)
        self.ledger.settle(grade, RESULT, self.checked_at)
        self.commit()
        with patch.object(shadow, "evaluate_saved_rows", side_effect=AssertionError("scorer called")):
            report = self.inspect(max_age_seconds=24 * 3600)
        self.assertEqual(report["progress"]["forecasts"], 1)
        self.assertEqual(report["progress"]["resolved_forecasts"], 1)
        self.assertEqual(report["progress"]["unresolved"], 0)
        self.assertFalse(report["performance_evaluated"])
        self.assertNotIn("profit", json.dumps(report))

    def test_push_dnp_and_unresolved_have_separate_qualification_denominators(self):
        rows = [forecast("forecast-" + str(i), "player-" + str(i)) for i in range(4)]
        rows[0]["quote"]["line"] = 10
        refresh(rows[0])
        self.add_run(rows=rows)
        admitted = self.ledger.population(self.checked_at)[0]
        self.checked_at = NOW + timedelta(hours=6)
        for row, participation, actual in zip(admitted[:3], ("played", "dnp", "played"), (10, None, 12)):
            grade = settlement(row, participation, actual, settlement_id="settled-" + row["forecast_id"])
            self.ledger.settle(grade, RESULT, self.checked_at)
        self.commit()
        with patch.object(shadow, "evaluate_saved_rows", side_effect=AssertionError("scorer called")):
            report = self.inspect(max_age_seconds=24 * 3600)
        denominators = report["settlement_denominators"]
        self.assertEqual({k: denominators[k] for k in ("played", "dnp", "unknown", "pushes",
                                                      "settled_played_nonpush", "settled_nonpush_et_dates")},
                         {"played": 2, "dnp": 1, "unknown": 1, "pushes": 1,
                          "settled_played_nonpush": 1, "settled_nonpush_et_dates": 1})
        self.assertEqual(denominators["unresolved_fraction"], .25)
        self.assertEqual(denominators["minimum_settled_played_nonpush"], 2000)
        self.assertEqual(denominators["minimum_settled_nonpush_et_dates"], 60)

    def test_resumed_existing_seal_retains_its_original_clock(self):
        record = self.add_run(terminal=False)
        record["started_at"] = shadow.stamp(NOW + timedelta(minutes=3))
        record["completed_at"] = shadow.stamp(NOW + timedelta(minutes=5))
        record["staged"]["capture_status"] = "RESUMED"
        record["seal"] = {"batch_id": "run-1", "durable_at": record["seal"]["durable_at"], "resumed": True}
        record["batch_publication"].update(commit_sha=command(self.local, "rev-parse", "HEAD"),
                                           durable_at=shadow.stamp(NOW + timedelta(minutes=4)))
        self.change_run(record)
        self.checked_at = NOW + timedelta(minutes=6)
        self.assertEqual(self.inspect()["status"], "HEALTHY")

    def test_cap_endpoint_reports_due_without_evaluating(self):
        self.add_run()
        self.checked_at = shadow.CAP + timedelta(days=14)
        with patch.object(shadow, "evaluate_saved_rows", side_effect=AssertionError("scorer called")):
            report = self.inspect(max_age_seconds=2 * 365 * 24 * 3600)
        self.assertEqual(report["progress"]["status"], "EVALUATION_DUE")
        self.assertEqual(report["evaluation_due_at"], shadow.stamp(self.checked_at))
        self.assertEqual(report["next_action"], "REGISTERED_EVALUATION_REVIEW")

    def test_cli_only_writes_new_report_outside_evidence_and_returns_exit_two_for_unhealthy(self):
        self.add_run()
        args = ["--data-repo", str(self.local), "--repository", self.repository]
        output = self.root / "private-monitor.json"
        with patch.object(shadow, "utcnow", return_value=self.checked_at), redirect_stdout(io.StringIO()):
            self.assertEqual(monitor.main(args + ["--output", str(output)]), 0)
            self.assertEqual(monitor.main(args + ["--output", str(output)]), 2)
            self.assertEqual(monitor.main(args + ["--output", str(self.local / "forbidden.json")]), 2)
        self.assertFalse((self.local / "forbidden.json").exists())
        self.assertEqual(shadow.load(output)["status"], "HEALTHY")
        with patch.object(shadow, "utcnow", return_value=NOW + timedelta(days=1)), redirect_stdout(io.StringIO()):
            self.assertEqual(monitor.main(args), 2)


if __name__ == "__main__":
    unittest.main()
