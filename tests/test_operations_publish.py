"""Real local Git remotes exercise archive publication without network access."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from research.operations import publish as publisher


class OperationsPublishTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.remote, self.repo, self.other = [self.root / n for n in ("remote.git", "checkout", "other")]
        self.git(self.root, "init", "--bare", "--initial-branch=main", str(self.remote))
        self.git(self.root, "clone", str(self.remote), str(self.repo))
        for name, value in (("user.name", "Fixture"), ("user.email", "fixture@example.invalid")):
            self.git(self.repo, "config", name, value)
        self.put(self.repo / "README.md", b"original documentation\n")
        self.put(self.repo / "cricket/data/raw/polymarket/prices.parquet", b"canonical fixture\n")
        self.git(self.repo, "add", ".")
        self.git(self.repo, "commit", "-m", "Initial fixture")
        self.git(self.repo, "push", "origin", "main")
        self.initial = self.git(self.remote, "rev-parse", "main").strip()
        self.git(self.root, "clone", str(self.remote), str(self.other))
        for name, value in (("user.name", "Fixture"), ("user.email", "fixture@example.invalid")):
            self.git(self.other, "config", name, value)

    @staticmethod
    def git(repo, *args):
        result = subprocess.run(["git", "-C", str(repo), *args], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, check=True)
        return result.stdout.decode()

    @staticmethod
    def put(path, raw):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

    @staticmethod
    def encoded(value):
        return (json.dumps(value, sort_keys=True) + "\n").encode()

    def run_payload(self, run_id="run-1", completed="2026-09-07T12:00:00Z", *, status="OK", state=None):
        output = self.repo / "data/operations"
        directory = output / "runs" / run_id
        files = {"started.json": self.encoded({"run_id": run_id}),
                 "polymarket/responses/00001.body.gz": b"exact raw response fixture"}
        for path, raw in files.items():
            self.put(directory / path, raw)
        report = {"schema": "collection-health-v1", "run_id": run_id, "completed_at": completed,
                  "status": status, "files": {name: {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
                                               for name, raw in files.items()}}
        self.put(directory / "report.json", self.encoded(report))
        self.put(output / "health.json", self.encoded(report))
        self.put(output / "state.json", self.encoded({"schema": "collection-state-v1", **(state or {"sources": {}})}))
        control = self.encoded({"schema": "operations-status-v1", "run_id": run_id,
                                "generated_at": completed, "new_events": []})
        self.put(directory / "control/status.json", control)
        self.put(directory / "control/decisions.jsonl", b"")
        self.put(output / "status.json", control)
        return output

    def remote_read(self, path):
        return self.git(self.remote, "show", "main:" + path)

    def checkpoint_run(self, run_id, completed, latest, gaps, request, *, received=None, market="m1", prior_refs=None, through=None):
        output = self.run_payload(run_id, completed, status="FAILED")
        checkpoint = {"schema": "market-interval-checkpoint-v1", "market_id": market,
                      "market": {"id": market}, "latest": latest, "pending_backfills": gaps,
                      "recent_observations": [], "requested_start": request[0], "requested_end": request[1],
                      "request_sequence": 1, "received_at": received or completed}
        if through is not None:
            checkpoint["collected_through"] = through
        raw = self.encoded(checkpoint)
        filename = f"runs/{run_id}/polymarket/checkpoints.jsonl"
        self.put(output / filename, raw)
        reference = {"file": filename, "file_sha256": hashlib.sha256(raw).hexdigest(),
                     "checkpoint_sha256": hashlib.sha256(raw).hexdigest(),
                     "received_at": checkpoint["received_at"], "latest": latest}
        state = {"schema": "collection-state-v1", "sources": {"polymarket": {
            "recovery": {**(prior_refs or {}), market: reference}}}}
        self.put(output / "state.json", self.encoded(state))
        report_path = output / "runs" / run_id / "report.json"
        report = json.loads(report_path.read_text())
        report["files"]["polymarket/checkpoints.jsonl"] = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        self.put(report_path, self.encoded(report))
        self.put(output / "health.json", self.encoded(report))
        return reference

    def add_attempts(self, run_id, attempts, inherited=None):
        output = self.repo / "data/operations"
        lines = {market: self.encoded({"schema": "market-attempt-v1", "market_id": market,
                                       "attempted_at": attempted}) for market, attempted in attempts.items()}
        raw = b"".join(lines.values())
        filename = f"runs/{run_id}/polymarket/market-attempts.jsonl"
        self.put(output / filename, raw)
        references = {market: {"file": filename, "file_sha256": hashlib.sha256(raw).hexdigest(),
                              "attempt_sha256": hashlib.sha256(line).hexdigest(), "attempted_at": attempts[market]}
                      for market, line in lines.items()}
        state_path = output / "state.json"
        state = json.loads(state_path.read_text())
        state["sources"].setdefault("polymarket", {})["market_attempts"] = {**(inherited or {}), **references}
        self.put(state_path, self.encoded(state))
        report_path = output / "runs" / run_id / "report.json"
        report = json.loads(report_path.read_text())
        report["files"]["polymarket/market-attempts.jsonl"] = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        self.put(report_path, self.encoded(report))
        self.put(output / "health.json", self.encoded(report))
        return references

    def test_append_failed_run_preserves_unrelated_tree_and_dirty_checkout(self):
        self.run_payload(status="FAILED")
        self.put(self.repo / "README.md", b"unrelated local change\n")
        self.put(self.repo / "data/operations/private-model.json", b"do not publish")
        result = publisher.publish(self.repo, run_id="run-1")
        self.assertEqual(result["status"], "PUBLISHED")
        self.assertEqual(json.loads(self.remote_read("data/operations/health.json"))["status"], "FAILED")
        self.assertEqual(self.remote_read("README.md"), "original documentation\n")
        self.assertEqual((self.repo / "README.md").read_text(), "unrelated local change\n")
        self.assertEqual(self.remote_read("cricket/data/raw/polymarket/prices.parquet"), "canonical fixture\n")
        self.assertNotIn("private-model", self.git(self.remote, "ls-tree", "-r", "main"))
        self.assertEqual(self.git(self.remote, "rev-parse", "main^").strip(), self.initial)

    def test_same_completed_run_is_noop(self):
        self.run_payload()
        first = publisher.publish(self.repo, run_id="run-1")
        second = publisher.publish(self.repo, run_id="run-1")
        self.assertEqual(second["status"], "UNCHANGED")
        self.assertEqual(first["commit"], second["commit"])

    def test_same_id_different_valid_payload_rejected(self):
        self.run_payload()
        first = publisher.publish(self.repo, run_id="run-1")
        self.run_payload(completed="2026-09-07T13:00:00Z")
        with self.assertRaisesRegex(publisher.PublishError, "IMMUTABLE_RUN_COLLISION"):
            publisher.publish(self.repo, run_id="run-1")
        self.assertEqual(self.git(self.remote, "rev-parse", "main").strip(), first["commit"])

    def test_symlink_source_file_and_parent_rejected(self):
        output = self.run_payload()
        file = output / "runs/run-1/started.json"
        file.unlink()
        file.symlink_to(self.repo / "README.md")
        with self.assertRaisesRegex(publisher.PublishError, "SYMLINK"):
            publisher.publish(self.repo, run_id="run-1")
        file.unlink()
        self.run_payload()
        moved = output.with_name("operations-real")
        output.rename(moved)
        output.symlink_to(moved, target_is_directory=True)
        with self.assertRaisesRegex(publisher.PublishError, "SYMLINK"):
            publisher.publish(self.repo, run_id="run-1")

    def test_path_allowlist_and_manifest_enforced(self):
        self.run_payload()
        for output, run_id in (("cricket/data/raw/polymarket", "run-1"),
                               ("data/elsewhere/../operations", "run-1"),
                               ("data/operations", "../run-1")):
            with self.subTest(output=output, run_id=run_id):
                with self.assertRaises(publisher.PublishError):
                    publisher.publish(self.repo, output, run_id)
        self.put(self.repo / "data/operations/runs/run-1/unlisted.txt", b"incomplete")
        with self.assertRaisesRegex(publisher.PublishError, "RUN_MANIFEST_MISMATCH"):
            publisher.publish(self.repo, run_id="run-1")

    def test_race_retries_on_new_main_and_preserves_concurrent_changes(self):
        self.run_payload()
        original_git, raced = publisher._git, []

        def race(repo, *args, **kwargs):
            if args[0] == "push" and not raced:
                self.put(self.other / "concurrent.txt", b"another contributor\n")
                self.git(self.other, "add", "concurrent.txt")
                self.git(self.other, "commit", "-m", "Concurrent work")
                self.git(self.other, "push", "origin", "main")
                raced.append(True)
            return original_git(repo, *args, **kwargs)

        with patch.object(publisher, "_git", side_effect=race):
            result = publisher.publish(self.repo, run_id="run-1")
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(self.remote_read("concurrent.txt"), "another contributor\n")
        self.assertEqual(self.git(self.remote, "rev-list", "--count", "main").strip(), "3")

    def test_older_run_persists_raw_without_replacing_newer_derived_files(self):
        self.run_payload("new", "2026-09-07T14:00:00Z")
        publisher.publish(self.repo, run_id="new")
        self.run_payload("old", "2026-09-07T12:00:00Z")
        result = publisher.publish(self.repo, run_id="old")
        self.assertEqual(result["derived_skipped"], "REMOTE_COMPLETION_NEWER")
        self.assertEqual(json.loads(self.remote_read("data/operations/health.json"))["run_id"], "new")
        self.assertEqual(json.loads(self.remote_read("data/operations/runs/old/report.json"))["run_id"], "old")

    def test_newer_stale_snapshot_cannot_drop_markets_or_move_cursor_backward(self):
        state = {"sources": {"polymarket": {"last_success_at": "2026-09-07T10:00:00Z",
                 "cursor": {"markets": {"market-1": {}}, "latest": {"market-1": 100}}}}}
        self.run_payload("first", state=state)
        publisher.publish(self.repo, run_id="first")
        for i, candidate in enumerate(({"sources": {}},
            {"sources": {"polymarket": {"last_success_at": "2026-09-07T11:00:00Z",
             "cursor": {"markets": {"market-1": {}}, "latest": {"market-1": 99}}}}})):
            run_id = f"stale-{i}"
            self.run_payload(run_id, "2026-09-07T14:00:00Z", state=candidate)
            result = publisher.publish(self.repo, run_id=run_id)
            self.assertEqual(result["derived_skipped"], "CANDIDATE_STATE_WOULD_SHRINK")
            self.assertEqual(json.loads(self.remote_read("data/operations/state.json")), {"schema": "collection-state-v1", **state})

    def test_dry_run_and_non_fast_forward_push_failure_handling(self):
        self.run_payload()
        result = publisher.publish(self.repo, run_id="run-1", dry_run=True)
        self.assertEqual(result["status"], "DRY_RUN")
        self.assertEqual(self.git(self.remote, "rev-parse", "main").strip(), self.initial)
        original_git, pushes = publisher._git, []

        def denied(repo, *args, **kwargs):
            if args[0] == "push":
                pushes.append(args)
                return subprocess.CompletedProcess(args, 1, b"", b"credential fixture must not be printed")
            return original_git(repo, *args, **kwargs)

        with patch.object(publisher, "_git", side_effect=denied):
            with self.assertRaisesRegex(publisher.PublishError, "^PUSH_FAILED_NO_RETRY$"):
                publisher.publish(self.repo, run_id="run-1")
        self.assertEqual(len(pushes), 1)

    def test_remote_symlink_rejected(self):
        self.run_payload()
        target = self.other / "data/operations"
        target.parent.mkdir()
        target.symlink_to("../README.md")
        self.git(self.other, "add", "data/operations")
        self.git(self.other, "commit", "-m", "Unsafe fixture")
        self.git(self.other, "push", "origin", "main")
        with self.assertRaisesRegex(publisher.PublishError, "REMOTE_SYMLINK"):
            publisher.publish(self.repo, run_id="run-1")

    def test_missing_control_keeps_new_raw_health_state_and_existing_status(self):
        self.run_payload("first")
        publisher.publish(self.repo, run_id="first")
        prior = self.remote_read("data/operations/status.json")
        output = self.run_payload("second", "2026-09-07T15:00:00Z")
        for path in (output / "runs/second/control").iterdir():
            path.unlink()
        publisher.publish(self.repo, run_id="second")
        self.assertEqual(json.loads(self.remote_read("data/operations/health.json"))["run_id"], "second")
        self.assertEqual(self.remote_read("data/operations/status.json"), prior)

    def test_control_decisions_and_run_clock_are_bound(self):
        output = self.run_payload()
        path = output / "runs/run-1/control/decisions.jsonl"
        path.write_text('{"event_id":"unmatched"}\n')
        with self.assertRaisesRegex(publisher.PublishError, "CONTROL_DECISIONS_MISMATCH"):
            publisher.publish(self.repo, run_id="run-1")
        path.write_text("")
        path = output / "runs/run-1/control/status.json"
        status = json.loads(path.read_text())
        status["generated_at"] = "2026-09-07T10:00:00Z"
        path.write_bytes(self.encoded(status))
        with self.assertRaisesRegex(publisher.PublishError, "STATUS_DOES_NOT_MATCH_RUN"):
            publisher.publish(self.repo, run_id="run-1")

    def test_sparse_checkout_preserves_unmaterialized_archive_and_main_index(self):
        self.git(self.repo, "sparse-checkout", "set", "--no-cone", "/README.md", "/data/operations/")
        self.assertFalse((self.repo / "cricket").exists())
        original_index = (self.repo / ".git/index").read_bytes()
        self.run_payload()
        result = publisher.publish(self.repo, run_id="run-1")
        self.assertEqual(result["status"], "PUBLISHED")
        self.assertFalse((self.repo / "cricket").exists())
        self.assertEqual((self.repo / ".git/index").read_bytes(), original_index)
        self.assertEqual(self.remote_read("cricket/data/raw/polymarket/prices.parquet"), "canonical fixture\n")

    def test_non_fast_forward_conflicts_stop_after_three_attempts(self):
        self.run_payload()
        original_git, pushes = publisher._git, []

        def conflict(repo, *args, **kwargs):
            if args[0] == "push":
                pushes.append(args)
                return subprocess.CompletedProcess(args, 1, b"! [rejected] (non-fast-forward)", b"")
            return original_git(repo, *args, **kwargs)

        with patch.object(publisher, "_git", side_effect=conflict):
            with self.assertRaisesRegex(publisher.PublishError, "CONCURRENT_UPDATES_RETRY_LIMIT"):
                publisher.publish(self.repo, run_id="run-1")
        self.assertEqual(len(pushes), 3)
        self.assertEqual(self.git(self.remote, "rev-parse", "main").strip(), self.initial)

    def test_verified_partial_progress_can_finish_gap_without_marking_source_successful(self):
        self.checkpoint_run("partial1", "2026-09-07T12:00:00Z", 60000, [[100, 200]], [59000, 60000])
        publisher.publish(self.repo, run_id="partial1")
        self.checkpoint_run("partial2", "2026-09-07T13:00:00Z", 60100, [[150, 200]], [100, 150])
        result = publisher.publish(self.repo, run_id="partial2")
        self.assertIsNone(result["derived_skipped"])
        state = json.loads(self.remote_read("data/operations/state.json"))
        source = state["sources"]["polymarket"]
        self.assertNotIn("last_success_at", source)
        self.assertNotIn("cursor", source)
        self.assertEqual(source["recovery"]["m1"]["latest"], 60100)
        self.assertEqual(json.loads(self.remote_read("data/operations/health.json"))["status"], "FAILED")

    def test_newer_stale_partial_cannot_resurrect_completed_backfill_or_drop_pending_gap(self):
        self.checkpoint_run("current", "2026-09-07T12:00:00Z", 60000, [[150, 200]], [100, 150])
        publisher.publish(self.repo, run_id="current")
        original = self.remote_read("data/operations/state.json")
        for run_id, gaps in (("stale", [[100, 200]]), ("lost-gap", [])):
            self.checkpoint_run(run_id, "2026-09-07T14:00:00Z", 61000, gaps, [60000, 61000])
            result = publisher.publish(self.repo, run_id=run_id)
            self.assertEqual(result["derived_skipped"], "CANDIDATE_RECOVERY_WOULD_REGRESS")
            self.assertEqual(self.remote_read("data/operations/state.json"), original)
            self.assertIn(run_id, self.remote_read(f"data/operations/runs/{run_id}/report.json"))

    def test_missing_or_older_market_recovery_does_not_replace_latest_progress(self):
        self.checkpoint_run("current", "2026-09-07T12:00:00Z", 60000, [[150, 200]], [100, 150])
        publisher.publish(self.repo, run_id="current")
        original = self.remote_read("data/operations/state.json")
        self.run_payload("missing", "2026-09-07T14:00:00Z", state={"sources": {"polymarket": {}}})
        result = publisher.publish(self.repo, run_id="missing")
        self.assertEqual(result["derived_skipped"], "CANDIDATE_RECOVERY_WOULD_REGRESS")
        self.checkpoint_run("older", "2026-09-07T15:00:00Z", 59000, [[150, 200]], [100, 150],
                            received="2026-09-07T11:00:00Z")
        result = publisher.publish(self.repo, run_id="older")
        self.assertEqual(result["derived_skipped"], "CANDIDATE_RECOVERY_WOULD_REGRESS")
        self.assertEqual(self.remote_read("data/operations/state.json"), original)

    def test_inherited_reference_resolves_from_remote_and_new_market_is_retained(self):
        previous = self.checkpoint_run("current", "2026-09-07T12:00:00Z", 60000, [[150, 200]], [100, 150])
        publisher.publish(self.repo, run_id="current")
        self.checkpoint_run("next", "2026-09-07T13:00:00Z", 62000, [], [61000, 62000],
                            market="m2", prior_refs={"m1": previous})
        result = publisher.publish(self.repo, run_id="next")
        self.assertIsNone(result["derived_skipped"])
        refs = json.loads(self.remote_read("data/operations/state.json"))["sources"]["polymarket"]["recovery"]
        self.assertEqual(set(refs), {"m1", "m2"})

    def test_invalid_recovery_reference_retains_raw_without_publishing_unverified_state(self):
        self.checkpoint_run("invalid", "2026-09-07T12:00:00Z", 60000, [], [59000, 60000])
        path = self.repo / "data/operations/state.json"
        state = json.loads(path.read_text())
        state["sources"]["polymarket"]["recovery"]["m1"]["file_sha256"] = "0" * 64
        path.write_bytes(self.encoded(state))
        result = publisher.publish(self.repo, run_id="invalid")
        self.assertEqual(result["derived_skipped"], "RECOVERY_REFERENCE_NOT_VERIFIED")
        self.assertIn("invalid", self.remote_read("data/operations/runs/invalid/report.json"))
        self.assertNotIn("data/operations/state.json", self.git(self.remote, "ls-tree", "-r", "main"))

    def test_concurrent_partial_capture_keeps_remote_backfill_progress_after_push_retry(self):
        self.checkpoint_run("initial-partial", "2026-09-07T12:00:00Z", 60000, [[100, 200]], [59000, 60000])
        self.add_attempts("initial-partial", {"m1": "2026-09-07T12:00:00Z"})
        publisher.publish(self.repo, run_id="initial-partial")
        self.checkpoint_run("late-stale", "2026-09-07T14:00:00Z", 61000, [[100, 200]], [60000, 61000])
        self.add_attempts("late-stale", {"m1": "2026-09-07T12:00:00Z", "m2": "2026-09-07T14:00:00Z"})
        original_git, raced = publisher._git, []

        def race(repo, *args, **kwargs):
            if args[0] == "push" and not raced:
                raced.append(True)
                original_repo = self.repo
                try:
                    self.repo = self.other
                    self.checkpoint_run("concurrent-partial", "2026-09-07T13:00:00Z", 60050,
                                        [[150, 200]], [100, 150])
                    self.add_attempts("concurrent-partial", {"m1": "2026-09-07T13:00:00Z",
                                                              "m3": "2026-09-07T13:00:00Z"})
                    concurrent = publisher.publish(self.repo, run_id="concurrent-partial")
                    self.assertIsNone(concurrent["derived_skipped"])
                finally:
                    self.repo = original_repo
            return original_git(repo, *args, **kwargs)

        with patch.object(publisher, "_git", side_effect=race):
            result = publisher.publish(self.repo, run_id="late-stale")
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(result["derived_skipped"], "CANDIDATE_RECOVERY_WOULD_REGRESS")
        self.assertEqual(json.loads(self.remote_read("data/operations/health.json"))["run_id"], "concurrent-partial")
        source = json.loads(self.remote_read("data/operations/state.json"))["sources"]["polymarket"]
        self.assertEqual(source["recovery"]["m1"]["latest"], 60050)
        self.assertEqual(set(source["market_attempts"]), {"m1", "m2", "m3"})
        self.assertEqual(source["market_attempts"]["m1"]["attempted_at"], "2026-09-07T13:00:00Z")
        self.assertEqual(source["market_attempts"]["m2"]["attempted_at"], "2026-09-07T14:00:00Z")
        self.assertEqual(result["attempt_references_merged"], 1)
        self.assertNotIn("last_success_at", source)
        self.assertIn("late-stale", self.remote_read("data/operations/runs/late-stale/report.json"))

    def test_newer_run_keeps_missing_and_newer_remote_attempts(self):
        self.run_payload("first", "2026-09-07T13:00:00Z", status="FAILED")
        self.add_attempts("first", {"m1": "2026-09-07T13:00:00Z", "m3": "2026-09-07T13:00:00Z"})
        publisher.publish(self.repo, run_id="first")
        self.run_payload("second", "2026-09-07T14:00:00Z", status="FAILED")
        self.add_attempts("second", {"m1": "2026-09-07T12:00:00Z", "m2": "2026-09-07T14:00:00Z"})
        result = publisher.publish(self.repo, run_id="second")
        self.assertIsNone(result["derived_skipped"])
        source = json.loads(self.remote_read("data/operations/state.json"))["sources"]["polymarket"]
        self.assertEqual(set(source["market_attempts"]), {"m1", "m2", "m3"})
        self.assertEqual(source["market_attempts"]["m1"]["attempted_at"], "2026-09-07T13:00:00Z")
        self.assertNotIn("cursor", source)
        self.assertNotIn("last_success_at", source)
        self.assertEqual(json.loads(self.remote_read("data/operations/health.json"))["status"], "FAILED")

    def test_invalid_attempt_ref_does_not_change_derived_state_but_retains_raw(self):
        self.run_payload("first", "2026-09-07T13:00:00Z", status="FAILED")
        self.add_attempts("first", {"m1": "2026-09-07T13:00:00Z"})
        publisher.publish(self.repo, run_id="first")
        original = self.remote_read("data/operations/state.json")
        for index, mutation in enumerate(({"attempt_sha256": "0" * 64},
                                           {"file": "../outside/market-attempts.jsonl"},
                                           {"attempted_at": "2026-09-07T16:00:00Z"})):
            run_id = f"invalid-{index}"
            self.run_payload(run_id, "2026-09-07T14:00:00Z", status="FAILED")
            self.add_attempts(run_id, {"m1": "2026-09-07T14:00:00Z"})
            path = self.repo / "data/operations/state.json"
            state = json.loads(path.read_text())
            state["sources"]["polymarket"]["market_attempts"]["m1"].update(mutation)
            path.write_bytes(self.encoded(state))
            result = publisher.publish(self.repo, run_id=run_id)
            self.assertEqual(result["derived_skipped"], "ATTEMPT_REFERENCE_NOT_VERIFIED")
            self.assertEqual(self.remote_read("data/operations/state.json"), original)
            self.assertIn(run_id, self.remote_read(f"data/operations/runs/{run_id}/report.json"))

    def test_completed_interval_cannot_regress_while_last_observed_price_is_unchanged(self):
        self.checkpoint_run("current", "2026-09-07T12:00:00Z", 60000, [], [59000, 65000], through=65000)
        publisher.publish(self.repo, run_id="current")
        original = self.remote_read("data/operations/state.json")
        self.checkpoint_run("stale", "2026-09-07T13:00:00Z", 60000, [], [59000, 63000], through=63000)
        result = publisher.publish(self.repo, run_id="stale")
        self.assertEqual(result["derived_skipped"], "CANDIDATE_RECOVERY_WOULD_REGRESS")
        self.assertEqual(self.remote_read("data/operations/state.json"), original)

    def test_whole_source_interval_cursor_cannot_regress_with_same_price_highwater(self):
        state = {"sources": {"polymarket": {"cursor": {
            "markets": {"m1": {}}, "latest": {"m1": 60000}, "collected_through": {"m1": 65000}}}}}
        self.run_payload("current", "2026-09-07T12:00:00Z", state=state)
        publisher.publish(self.repo, run_id="current")
        state["sources"]["polymarket"]["cursor"]["collected_through"]["m1"] = 63000
        self.run_payload("stale", "2026-09-07T13:00:00Z", state=state)
        result = publisher.publish(self.repo, run_id="stale")
        self.assertEqual(result["derived_skipped"], "CANDIDATE_STATE_WOULD_SHRINK")
        saved = json.loads(self.remote_read("data/operations/state.json"))
        self.assertEqual(saved["sources"]["polymarket"]["cursor"]["collected_through"]["m1"], 65000)

    def test_empty_terminal_gap_can_extend_to_run_cutoff_after_older_backfill_succeeds(self):
        self.checkpoint_run("current", "1970-01-01T20:00:00Z", 60000, [[100, 200]],
                            [59000, 65000], through=65000)
        publisher.publish(self.repo, run_id="current")

        def terminal(run_id, lower, upper):
            self.checkpoint_run(run_id, "1970-01-02T03:46:41Z", 60000,
                                [[150, 200], [lower, upper]], [100, 150], through=65000)
            output = self.repo / "data/operations"
            report_path = output / "runs" / run_id / "report.json"
            report = json.loads(report_path.read_text())
            report["started_at"] = "1970-01-02T03:46:40Z"  # request cutoff = 100000
            self.put(report_path, self.encoded(report))
            self.put(output / "health.json", self.encoded(report))

        terminal("terminal", 57800, 100000)
        result = publisher.publish(self.repo, run_id="terminal")
        self.assertIsNone(result["derived_skipped"])
        saved = self.remote_read("data/operations/state.json")
        source = json.loads(saved)["sources"]["polymarket"]
        self.assertNotIn("last_success_at", source)
        self.assertNotIn("cursor", source)
        self.assertEqual(json.loads(self.remote_read("data/operations/health.json"))["status"], "FAILED")
        for run_id, lower, upper in (("too-old", 55000, 100000), ("past-cutoff", 57800, 100001)):
            terminal(run_id, lower, upper)
            # Later completion lets the interval guard, rather than tie handling,
            # decide whether the new snapshot preserves the current one.
            output = self.repo / "data/operations"
            report_path = output / "runs" / run_id / "report.json"
            report = json.loads(report_path.read_text())
            report["completed_at"] = "1970-01-02T03:46:42Z"
            self.put(report_path, self.encoded(report))
            self.put(output / "health.json", self.encoded(report))
            # No newly bound control output is needed to preserve failed raw.
            for path in (output / "runs" / run_id / "control").iterdir():
                path.unlink()
            result = publisher.publish(self.repo, run_id=run_id)
            self.assertEqual(result["derived_skipped"], "CANDIDATE_RECOVERY_WOULD_REGRESS")
            self.assertEqual(self.remote_read("data/operations/state.json"), saved)


if __name__ == "__main__":
    unittest.main()
