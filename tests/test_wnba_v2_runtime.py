"""Real local Git transactions and synthetic frozen-source runtime checks."""
from copy import deepcopy
from contextlib import redirect_stdout
from datetime import timedelta
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from research.wnba_v2 import runtime, shadow
from research.wnba_v2.future import write_observations
from research.engine.store import ForecastRequest
from research.wnba_v2.model import price_forecast
from tests.test_wnba_v2_model import observation, recipe
from tests.test_wnba_v2_shadow import NOW, RAW, RESULT, forecast, study, settlement


def command(root, *args):
    result = subprocess.run(["git", "-C", str(root), *args], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, check=True,
                            env={**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
                                 "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@example.invalid"})
    return result.stdout.decode().strip()


class GitFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.remote, self.local = self.root / "remote.git", self.root / "local"
        self.remote.mkdir()
        command(self.remote, "init", "--bare", "--initial-branch=main")
        command(self.root, "clone", str(self.remote), str(self.local))
        (self.local / "unrelated.txt").write_text("preserve me\n")
        command(self.local, "add", "unrelated.txt")
        command(self.local, "commit", "-m", "Initial unrelated content")
        command(self.local, "push", "origin", "main")
        self.archive = runtime.GitArchive(self.local, "owner/private-study", clock=lambda: NOW)

    def snapshot(self):
        self.archive.fetch()
        raw = command(self.local, "ls-tree", "-r", "--name-only", "FETCH_HEAD")
        return raw.splitlines()

    def read_remote(self, path):
        head = self.archive.fetch()
        return runtime.git(self.local, "show", head + ":" + path).stdout


class GitArchiveTests(GitFixture):
    def test_append_preserves_unrelated_tree_and_checkout_and_retries_idempotently(self):
        path = runtime.PREFIX + "/runs/one/runtime.json"
        payload = {path: b'{"status":"NO_EVENTS"}\n'}
        old_head = command(self.local, "rev-parse", "HEAD")
        old_index = command(self.local, "write-tree")
        first = self.archive.append(payload, run_id="one")
        second = self.archive.append(payload, run_id="one")
        self.assertEqual(first["commit_sha"], second["commit_sha"])
        self.assertEqual(command(self.local, "rev-parse", "HEAD"), old_head)
        self.assertEqual(command(self.local, "write-tree"), old_index)
        self.assertEqual(self.read_remote("unrelated.txt"), b"preserve me\n")
        self.assertEqual(self.read_remote(path), payload[path])

    def test_concurrent_fast_forward_preserves_both_runs(self):
        other = self.root / "other"
        command(self.root, "clone", str(self.remote), str(other))
        other_archive = runtime.GitArchive(other, "owner/private-study", clock=lambda: NOW)
        first_path = runtime.PREFIX + "/runs/first/runtime.json"
        second_path = runtime.PREFIX + "/runs/second/runtime.json"
        original = runtime.git
        raced = False

        def concurrent(repo, *args, **kwargs):
            nonlocal raced
            if Path(repo) == self.local and args[0] == "push" and not raced:
                raced = True
                other_archive.append({second_path: b"second"}, run_id="second")
            return original(repo, *args, **kwargs)

        with patch.object(runtime, "git", side_effect=concurrent):
            self.archive.append({first_path: b"first"}, run_id="first")
        self.assertTrue(raced)
        self.assertEqual(self.read_remote(first_path), b"first")
        self.assertEqual(self.read_remote(second_path), b"second")

    def test_conflicting_immutable_row_and_out_of_scope_write_fail(self):
        path = runtime.PREFIX + "/ledger/batches/a/manifest.json"
        self.archive.append({path: b"first"}, run_id="a")
        with self.assertRaisesRegex(runtime.RuntimeFailure, "IMMUTABLE_PATH_CONFLICT"):
            self.archive.append({path: b"replacement"}, run_id="a")
        for path in ("unrelated.txt", runtime.CONFIG, runtime.STUDY,
                     runtime.PREFIX + "/ledger/batches/../study.json"):
            with self.subTest(path=path), self.assertRaises(runtime.RuntimeFailure):
                self.archive.append({path: b"bad"}, run_id="a")

    def test_path_collision_and_remote_symlink_fail_without_deleting_files(self):
        parent = self.local / runtime.PREFIX / "ledger/batches"
        parent.mkdir(parents=True)
        (parent / "link").symlink_to("../../../unrelated.txt")
        command(self.local, "add", runtime.PREFIX)
        command(self.local, "commit", "-m", "Synthetic unsafe remote path")
        command(self.local, "push", "origin", "main")
        with self.assertRaisesRegex(runtime.RuntimeFailure, "REMOTE_SPECIAL_FILE"):
            self.archive.append({runtime.PREFIX + "/runs/a/runtime.json": b"a"}, run_id="a")

    def test_receipt_binds_reachable_commit_and_exact_artifact_bytes(self):
        path = runtime.PREFIX + "/ledger/batches/a/manifest.json"
        published = self.archive.append({path: b"manifest"}, run_id="a")
        receipt = {**published, "provider": "github", "artifact_path": path,
                   "stored_artifact_sha256": shadow.digest(b"manifest")}
        self.archive.verify_receipt(receipt, {path: b"manifest"})
        for mutation in ({"commit_sha": "0" * 40}, {"stored_artifact_sha256": "0" * 64},
                         {"repository_full_name": "other/repo"}):
            with self.subTest(mutation=mutation), self.assertRaises(runtime.RuntimeFailure):
                self.archive.verify_receipt({**receipt, **mutation}, {path: b"manifest"})

    def test_persistence_clock_is_sampled_after_remote_verification(self):
        observations = []
        original_verify = self.archive.verify
        def verified(commit, payload):
            original_verify(commit, payload)
            observations.append("verified")
        def clock():
            self.assertEqual(observations[-1], "verified")
            return NOW + timedelta(minutes=20)
        self.archive.verify = verified
        self.archive.clock = clock
        receipt = self.archive.append({runtime.PREFIX + "/runs/clock/runtime.json": b"clock"}, run_id="clock")
        self.assertEqual(receipt["durable_at"], shadow.stamp(NOW + timedelta(minutes=20)))

    def test_unchanged_remote_push_failure_is_not_retried(self):
        original = runtime.git
        pushes = []
        def blocked(repo, *args, **kwargs):
            if args[0] == "push":
                pushes.append(args)
                return subprocess.CompletedProcess(args, 1, b"", b"private credential detail")
            return original(repo, *args, **kwargs)
        with patch.object(runtime, "git", side_effect=blocked), self.assertRaisesRegex(
                runtime.RuntimeFailure, "GIT_PUSH_FAILED"):
            self.archive.append({runtime.PREFIX + "/runs/blocked/runtime.json": b"x"}, run_id="blocked")
        self.assertEqual(len(pushes), 1)

    def test_newly_published_history_is_protected_during_the_next_phase(self):
        path = runtime.PREFIX + "/history/current/observations.jsonl.gz"
        self.archive.append({path: b"original fresh source"}, run_id="current")
        other = self.root / "interphase-writer"
        command(self.root, "clone", str(self.remote), str(other))
        (other / path).write_bytes(b"changed after first push")
        command(other, "add", ".")
        command(other, "commit", "-m", "Synthetic interphase rewrite")
        command(other, "push", "origin", "main")
        with self.assertRaisesRegex(runtime.RuntimeFailure, "RESTORED_EVIDENCE_CHANGED_OR_DELETED"):
            self.archive.append({runtime.PREFIX + "/runs/current/runtime.json": b"completion"}, run_id="current")


class FrozenRuntimeTests(GitFixture):
    def setUp(self):
        super().setUp()
        self.code = self.root / "code"
        self.code.mkdir()
        command(self.code, "init", "--initial-branch=main")
        implementation = {}
        for name in sorted(runtime.REQUIRED_CODE):
            path = self.code / name
            path.parent.mkdir(parents=True, exist_ok=True)
            raw = b"# synthetic implementation identity fixture\n"
            path.write_bytes(raw)
            implementation[name] = {"sha256": shadow.digest(raw), "bytes": len(raw)}
        command(self.code, "add", ".")
        command(self.code, "commit", "-m", "Synthetic frozen code")
        self.code_commit = command(self.code, "rev-parse", "HEAD")
        self.recipe = recipe()
        self.seed = self.root / "seed-observations.jsonl.gz"
        write_observations(self.seed, [observation("seed", year=2025)])
        self.study = study()
        self.study.update(recipe_sha256=shadow.digest(shadow.encode(self.recipe)),
                          model_recipe_hash=self.recipe["recipe_hash"],
                          implementation_sha256=shadow.digest(shadow.encode(implementation)))
        files = {"candidate-recipe.json": shadow.encode(self.recipe),
                 "seed-observations.jsonl.gz": self.seed.read_bytes()}
        self.manifest = {"schema": "wnba-shadow-bundle-v1",
                         **{k: self.study[k] for k in ("candidate_id", "frozen_at", "model_recipe_hash", "implementation_sha256")},
                         "implementation_files": implementation, "recipe_file": "candidate-recipe.json",
                         "seed_file": "seed-observations.jsonl.gz",
                         "files": {name: {"sha256": shadow.digest(raw), "bytes": len(raw)}
                                   for name, raw in files.items()}}
        self.files = files
        self.bundle = self.root / "candidate.zip"
        self.write_bundle()
        self.config = {"schema": "wnba-shadow-runtime-v1", "repository_full_name": "owner/private-study",
                       "code_commit": self.code_commit, "bundle_path": runtime.PREFIX + "/frozen/candidate.zip",
                       "ledger_path": runtime.PREFIX + "/ledger"}
        for path, raw in {runtime.CONFIG: shadow.encode(self.config), runtime.STUDY: shadow.encode(self.study),
                          self.config["bundle_path"]: self.bundle.read_bytes()}.items():
            target = self.local / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        command(self.local, "add", runtime.PREFIX)
        command(self.local, "commit", "-m", "Initialize synthetic private study")
        command(self.local, "push", "origin", "main")

    def write_bundle(self, extra=None):
        with zipfile.ZipFile(self.bundle, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("MANIFEST.json", shadow.encode(self.manifest))
            for name, raw in self.files.items():
                archive.writestr(name, raw)
            for name, raw in (extra or {}).items():
                archive.writestr(name, raw)
        self.study["bundle_sha256"] = shadow.digest(self.bundle.read_bytes())

    def restore(self, name="restored"):
        return runtime.restore_bundle(self.bundle, self.root / name, self.study, self.code, self.code_commit)

    def refresh_empty(self, root, ledger, frozen_at, **kwargs):
        self.assertEqual(frozen_at, shadow.instant(self.study["frozen_at"]))
        return {"observations": [], "restored_observations": [], "status": "NO_EVENTS", "attempts": []}

    def empty_scan(self, model, ledger, run_id, **kwargs):
        self.assertEqual(model.recipe_hash, self.recipe["recipe_hash"])
        staged = ledger.stage(run_id, [], {"events.body": RAW}, now=NOW,
                              attempts=[{"source": "synthetic", "status": "NO_EVENTS"}])
        return {**staged, "capture_status": "NO_EVENTS"}

    def forecast_scan(self, model, ledger, run_id, **kwargs):
        request = ForecastRequest("p1", "future-game", "t1", "t2", 2026,
                                  NOW + timedelta(minutes=45), NOW - timedelta(seconds=30))
        output = model.predict(request)
        row = forecast()
        row.update(game_id="future-game", player_id="p1", team_id="t1", opponent_id="t2",
                   tip_at=shadow.stamp(request.tip_at), input_cutoff_at=shadow.stamp(request.as_of),
                   input_max_available_at=output["input_max_available_at"],
                   model_output=output, model_output_sha256=shadow.digest(shadow.encode(output)),
                   input_manifest_sha256=output["input_manifest_hash"], p_dnp=output["p_dnp"])
        row.update({k: self.study[k] for k in ("bundle_sha256", "recipe_sha256", "implementation_sha256")})
        row["quote"].update(game_id="future-game", player_id="p1")
        row.update({k: v for k, v in price_forecast(output, "points", row["quote"]["line"]).items()
                    if k in {"p_over", "p_push", "p_under"}})
        return {**ledger.stage(run_id, [row], {"offers.body": RAW}, now=NOW,
                               attempts=[{"status": "OK"}]), "capture_status": "OK"}

    def test_bundle_restores_exact_seed_and_recipe(self):
        recipe_out, seed = self.restore()
        self.assertEqual(recipe_out, self.recipe)
        self.assertEqual(seed.read_bytes(), self.seed.read_bytes())

    def test_bundle_detects_changed_archive_recipe_and_executed_code(self):
        claimed = deepcopy(self.study)
        claimed["bundle_sha256"] = "0" * 64
        with self.assertRaisesRegex(runtime.RuntimeFailure, "BUNDLE_HASH_MISMATCH"):
            runtime.restore_bundle(self.bundle, self.root / "bad", claimed, self.code, self.code_commit)
        code = self.code / sorted(runtime.REQUIRED_CODE)[0]
        code.write_text("changed implementation")
        with self.assertRaisesRegex(runtime.RuntimeFailure, "IMPLEMENTATION_BYTES_CHANGED"):
            self.restore()

    def test_archive_extra_member_or_path_escape_is_rejected(self):
        for extra in ({"extra.txt": b"extra"}, {"../escape": b"bad"}):
            self.write_bundle(extra)
            with self.subTest(extra=extra), self.assertRaises(runtime.RuntimeFailure):
                self.restore()
        self.assertFalse((self.root / "escape").exists())

    def test_no_events_run_is_durable_but_admits_no_forecasts(self):
        result = runtime.run_once(self.local, self.code, "empty", repository_full_name="owner/private-study",
                                  clock=lambda: NOW, scan=self.empty_scan, refresh=self.refresh_empty,
                                  archive=self.archive)
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["staged"]["forecasts"], 0)
        self.assertIn(runtime.PREFIX + "/ledger/batches/empty/seal.json", self.snapshot())
        restored = self.root / "verify"
        self.archive.restore(restored)
        ledger = shadow.Ledger(restored / self.config["ledger_path"])
        self.assertEqual(ledger.population(NOW)[0], [])
        manifest = runtime.PREFIX + "/ledger/batches/empty/manifest.json"
        seal = document_at(self.read_remote(runtime.PREFIX + "/ledger/batches/empty/seal.json"))
        self.archive.verify_receipt(seal, {manifest: self.read_remote(manifest)})
        self.assertEqual(result["parameter_refits"], 0)

    def test_source_exception_is_preserved_without_fake_forecast_or_secret(self):
        def failed(model, ledger, run_id, **kwargs):
            raise ValueError("secret token or private provider body")
        result = runtime.run_once(self.local, self.code, "failed", repository_full_name="owner/private-study",
                                  clock=lambda: NOW, scan=failed, refresh=self.refresh_empty,
                                  archive=self.archive)
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["error_code"], "ValueError")
        self.assertNotIn("secret", self.read_remote(runtime.PREFIX + "/runs/failed/runtime.json").decode())
        self.assertFalse(any("batches/failed" in name for name in self.snapshot()))

    def test_same_run_id_is_resumed_without_recapturing_or_overwriting(self):
        first = runtime.run_once(self.local, self.code, "once", repository_full_name="owner/private-study",
                                 clock=lambda: NOW, scan=self.empty_scan, refresh=self.refresh_empty,
                                 archive=self.archive)
        def forbidden(*args, **kwargs):
            raise AssertionError("A completed run cannot fetch again")
        second = runtime.run_once(self.local, self.code, "once", repository_full_name="owner/private-study",
                                  clock=lambda: NOW, scan=forbidden, refresh=forbidden, archive=self.archive)
        self.assertTrue(second["already_recorded"])
        self.assertEqual(first["status"], second["status"])

    def test_degraded_history_blocks_forecasts_but_keeps_a_durable_receipt(self):
        def degraded(*args, **kwargs):
            return {"observations": [], "restored_observations": [], "status": "DEGRADED",
                    "attempts": [{"status": "MISSING_GAME"}]}
        def forbidden(*args, **kwargs):
            raise AssertionError("Incomplete source history must block a forecast")
        result = runtime.run_once(self.local, self.code, "degraded", repository_full_name="owner/private-study",
                                  clock=lambda: NOW, scan=forbidden, refresh=degraded, archive=self.archive)
        self.assertEqual(result["status"], "DEGRADED")
        self.assertEqual(result["staged"]["capture_status"], "BLOCKED_HISTORY")
        self.assertEqual(result["staged"]["forecasts"], 0)

    def test_crash_after_batch_push_resumes_without_network_recapture(self):
        original = self.archive.append
        calls = []
        def fail_second(payload, **kwargs):
            calls.append(payload)
            if len(calls) == 2:
                raise runtime.RuntimeFailure("SYNTHETIC_SEAL_PUSH_INTERRUPTED")
            return original(payload, **kwargs)
        with patch.object(self.archive, "append", side_effect=fail_second), self.assertRaisesRegex(
                runtime.RuntimeFailure, "SYNTHETIC_SEAL_PUSH_INTERRUPTED"):
            runtime.run_once(self.local, self.code, "recover", repository_full_name="owner/private-study",
                             clock=lambda: NOW, scan=self.empty_scan, refresh=self.refresh_empty,
                             archive=self.archive)
        self.assertIn(runtime.PREFIX + "/ledger/batches/recover/manifest.json", self.snapshot())
        self.assertNotIn(runtime.PREFIX + "/ledger/batches/recover/seal.json", self.snapshot())
        def forbidden(*args, **kwargs):
            raise AssertionError("Published batch recovery must not recapture")
        result = runtime.run_once(self.local, self.code, "recover", repository_full_name="owner/private-study",
                                  clock=lambda: NOW + timedelta(minutes=2), scan=forbidden, refresh=forbidden,
                                  archive=runtime.GitArchive(self.local, "owner/private-study",
                                                             clock=lambda: NOW + timedelta(minutes=2)))
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["seal"]["durable_at"], shadow.stamp(NOW + timedelta(minutes=2)))

    def test_real_model_forecast_saved_after_cutoff_is_excluded(self):
        current = [NOW]
        def slow_durability():
            current[0] = NOW + timedelta(minutes=31)
            return current[0]
        self.archive.clock = slow_durability
        result = runtime.run_once(self.local, self.code, "late", repository_full_name="owner/private-study",
                                  clock=lambda: current[0], scan=self.forecast_scan, refresh=self.refresh_empty,
                                  archive=self.archive)
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["seal"]["late_seals"], 1)
        restored = self.root / "late-verify"
        self.archive.restore(restored)
        ledger = shadow.Ledger(restored / self.config["ledger_path"])
        rows, excluded = ledger.population(current[0])
        self.assertEqual(rows, [])
        self.assertEqual(excluded["sealed_later_than_15_minutes_before_tip"], 1)

    def test_prior_reachable_receipt_is_verified_before_any_new_source_call(self):
        runtime.run_once(self.local, self.code, "first", repository_full_name="owner/private-study",
                         clock=lambda: NOW, scan=self.empty_scan, refresh=self.refresh_empty, archive=self.archive)
        other = self.root / "tamper"
        command(self.root, "clone", str(self.remote), str(other))
        seal_path = other / runtime.PREFIX / "ledger/batches/first/seal.json"
        seal = json.loads(seal_path.read_bytes())
        seal["commit_sha"] = "0" * 40
        seal_path.write_bytes(shadow.encode(seal))
        command(other, "add", ".")
        command(other, "commit", "-m", "Synthetic invalid old receipt")
        command(other, "push", "origin", "main")
        called = []
        def forbidden(*args, **kwargs):
            called.append(True)
            raise AssertionError("Unverified prior receipt must prevent source calls")
        with self.assertRaisesRegex(runtime.RuntimeFailure, "RECEIPT_COMMIT_NOT_REACHABLE"):
            runtime.run_once(self.local, self.code, "next", repository_full_name="owner/private-study",
                             clock=lambda: NOW, scan=forbidden, refresh=forbidden, archive=self.archive)
        self.assertEqual(called, [])

    def test_old_evidence_rewrite_is_rejected_even_when_current_payload_is_unrelated(self):
        path = runtime.PREFIX + "/history/previous/receipt.json"
        self.archive.append({path: b"original"}, run_id="previous")
        self.archive.restore(self.root / "stale-restoration")
        other = self.root / "concurrent"
        command(self.root, "clone", str(self.remote), str(other))
        (other / path).write_bytes(b"rewritten")
        command(other, "add", ".")
        command(other, "commit", "-m", "Synthetic illegal old evidence rewrite")
        command(other, "push", "origin", "main")
        with self.assertRaisesRegex(runtime.RuntimeFailure, "RESTORED_EVIDENCE_CHANGED_OR_DELETED"):
            self.archive.append({runtime.PREFIX + "/runs/new/runtime.json": b"new"}, run_id="new")

    def test_concurrent_settlement_roots_are_quarantined_without_poisoning_ledger(self):
        runtime.run_once(self.local, self.code, "pregame", repository_full_name="owner/private-study",
                         clock=lambda: NOW, scan=self.forecast_scan, refresh=self.refresh_empty,
                         archive=self.archive)
        later = NOW + timedelta(hours=6)
        other = self.root / "settler-a"
        command(self.root, "clone", str(self.remote), str(other))
        writer_a = runtime.GitArchive(other, "owner/private-study", clock=lambda: later)
        def concurrent_refresh(history, ledger_b, frozen_at, **kwargs):
            row = ledger_b.population(later)[0][0]
            ledger_b.settle(settlement(row, actual=12, settlement_id="root-b"), RESULT, later)
            working_a = self.root / "working-a"
            writer_a.restore(working_a)
            before_a = runtime.regular_tree(working_a)
            ledger_a = shadow.Ledger(working_a / self.config["ledger_path"])
            ledger_a.settle(settlement(row, actual=12, settlement_id="root-a"), RESULT, later)
            writer_a.append(runtime._changed(before_a, working_a), run_id="settler-a")
            return {"observations": [], "restored_observations": [], "status": "OK", "attempts": []}
        def empty_later(model, ledger, run_id, **kwargs):
            return {**ledger.stage(run_id, [], {}, now=later, attempts=[{"status": "NO_EVENTS"}]),
                    "capture_status": "NO_EVENTS"}
        result = runtime.run_once(self.local, self.code, "settler-b", repository_full_name="owner/private-study",
                                  clock=lambda: later, scan=empty_later, refresh=concurrent_refresh,
                                  archive=runtime.GitArchive(self.local, "owner/private-study", clock=lambda: later))
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["error_code"], "MERGED_LEDGER_CONFLICT")
        self.assertTrue(result["rejected_additions"])
        paths = self.snapshot()
        self.assertIn(runtime.PREFIX + "/ledger/settlements/root-a/record.json", paths)
        self.assertNotIn(runtime.PREFIX + "/ledger/settlements/root-b/record.json", paths)
        self.assertTrue(any("/rejected/" in path and path.endswith("root-b/source.body") for path in paths))

    def test_orphan_history_observations_are_never_loaded(self):
        orphan = runtime.PREFIX + "/history/interrupted/observations.jsonl.gz"
        self.archive.append({orphan: b"deliberately not a valid gzip file"}, run_id="interrupted")
        result = runtime.run_once(self.local, self.code, "safe", repository_full_name="owner/private-study",
                                  clock=lambda: NOW, scan=self.empty_scan, refresh=self.refresh_empty,
                                  archive=self.archive)
        self.assertEqual(result["status"], "OK")

    def test_frozen_controls_cannot_change_during_a_running_capture(self):
        self.archive.restore(self.root / "initial-restore")
        target = self.local / runtime.CONFIG
        target.write_text("changed")
        command(self.local, "add", runtime.CONFIG)
        command(self.local, "commit", "-m", "Synthetic concurrent config change")
        command(self.local, "push", "origin", "main")
        with self.assertRaisesRegex(runtime.RuntimeFailure, "FROZEN_DEPLOYMENT_CHANGED"):
            self.archive.append({runtime.PREFIX + "/runs/a/runtime.json": b"a"}, run_id="a")


def document_at(raw):
    return json.loads(raw)


class DestinationTests(unittest.TestCase):
    def test_malformed_initial_study_is_a_sanitized_failed_cli_receipt(self):
        code_repo = Path(runtime.__file__).resolve().parents[2]
        argv = ["runtime", "--code-repo", str(code_repo), "--data-repo", "/unused", "--repository", "owner/study",
                "--run-id", "malformed"]
        output = io.StringIO()
        with patch("sys.argv", argv), patch.object(runtime, "verify_destination", return_value=True), \
                patch.object(runtime, "run_once", side_effect=ValueError("private malformed study contents")), \
                redirect_stdout(output):
            code = runtime.main()
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue()), {
            "run_id": "malformed", "status": "FAILED", "error_code": "ValueError", "receipt_saved": False})

    def test_public_destination_requires_explicit_deployment_choice(self):
        class Response(io.BytesIO):
            pass
        def opener(request, timeout):
            self.assertEqual(timeout, 20)
            return Response(shadow.encode({"full_name": "owner/study", "private": False, "default_branch": "main"}))
        with patch.object(runtime, "git", return_value=subprocess.CompletedProcess(
                [], 0, b"https://github.com/owner/study.git\n", b"")), patch.dict(os.environ, {"GITHUB_TOKEN": "fixture"}):
            with self.assertRaisesRegex(runtime.RuntimeFailure, "PUBLIC_EVIDENCE_NOT_AUTHORIZED"):
                runtime.verify_destination(Path("unused"), "owner/study", opener=opener)
            self.assertFalse(runtime.verify_destination(Path("unused"), "owner/study", opener=opener,
                                                        allow_public=True))

    def test_credential_bearing_or_mismatched_remote_is_never_followed(self):
        with patch.object(runtime, "git", return_value=subprocess.CompletedProcess(
                [], 0, b"https://token@github.com/owner/study.git\n", b"")):
            with self.assertRaisesRegex(runtime.RuntimeFailure, "UNEXPECTED_GIT_REMOTE"):
                runtime.verify_destination(Path("unused"), "owner/study")


if __name__ == "__main__":
    unittest.main()
