"""Runtime replacement preserves fitted evidence and fails on storage damage."""
from datetime import datetime, timedelta, timezone
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import warnings
import zipfile

from research.wnba_v2 import future, repackage_runtime as repack, runtime, shadow
from tests.test_wnba_v2_model import observation, recipe
from tests.test_wnba_v2_runtime import command


NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)


def archive_bytes(contents, *, extra=None):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as zipped:
        for name, raw in contents.items():
            zipped.writestr(name, raw)
        if extra:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                zipped.writestr(*extra)
    return stream.getvalue()


class RuntimeRepackageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / "code"
        self.repo.mkdir()
        command(self.repo, "init", "--initial-branch=main")
        names = runtime.REQUIRED_CODE | {"research/experiments/2026-09-wnba-participation-v2.md",
                                        "research/wnba_v2/comparison.py", "research/wnba_v2/reconstruction.py"}
        for name in names:
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("synthetic original implementation\n")
        command(self.repo, "add", ".")
        command(self.repo, "commit", "-m", "Original synthetic implementation")
        self.old_commit = command(self.repo, "rev-parse", "HEAD")
        old_implementation = repack.committed_implementations(self.repo, self.old_commit)
        for name in repack.ALLOWED_CHANGES:
            (self.repo / name).write_text("synthetic runtime storage repair\n")
        command(self.repo, "add", ".")
        command(self.repo, "commit", "-m", "Synthetic registered storage repair")
        self.new_commit = command(self.repo, "rev-parse", "HEAD")
        amendment = patch.object(repack, "AMENDMENT_COMMIT", self.new_commit)
        amendment.start()
        self.addCleanup(amendment.stop)
        self.recipe = recipe()
        self.expectation = {"schema": "wnba-v2-reconstruction-expectation-v1",
                            "source": "prior_conversation_record", "original_artifact_identity_verified": False,
                            "recorded_before_execution_at": shadow.stamp(NOW - timedelta(days=3)),
                            "model_recipe_hash": self.recipe["recipe_hash"], "variance_mode": "constant"}
        self.expectation_path = self.root / "expectation.json"
        self.expectation_path.write_bytes(shadow.encode(self.expectation))
        self.frozen = shadow.stamp(NOW - timedelta(days=1))
        self.selection = {"schema": "wnba-v2-candidate-selection-v1", "candidate_id": "synthetic-original",
                          "frozen_at": self.frozen, "code_commit": self.old_commit, "qualified": False,
                          "original_artifact_identity_verified": False, "seed_loaded_before_selection": False,
                          "comparison_artifacts": {"expectation.json": shadow.digest(self.expectation_path.read_bytes()),
                                                   "scalar-freeze.json": "a" * 64},
                          "comparison_registration": {"commit": "b" * 40},
                          "reconstruction_provenance": {"preserve": "synthetic provenance"},
                          "selection": {"status": "RESEARCH_ONLY", "market_advantage_established": False,
                                        "selected_variance_mode": "constant", "gates": {"kept": True}}}
        seed_path = self.root / "seed.jsonl.gz"
        self.seed = (observation("history", year=2025),)
        future.write_observations(seed_path, self.seed)
        self.contents = {"recipe.json": shadow.encode(self.recipe), "selection.json": shadow.encode(self.selection),
                         "seed.jsonl.gz": seed_path.read_bytes(), "seed-gate.json": shadow.encode({"status": "PASS_UNDER_ASSUMPTION"}),
                         "seed-quality.json": shadow.encode({"synthetic": True}),
                         "source-manifest.json": shadow.encode({"synthetic_source": "unchanged"})}
        self.manifest = {"schema": "wnba-shadow-bundle-v1", "candidate_id": "synthetic-original",
                         "frozen_at": self.frozen, "model_recipe_hash": self.recipe["recipe_hash"],
                         "recipe_file": "recipe.json", "seed_file": "seed.jsonl.gz",
                         "implementation_files": old_implementation,
                         "implementation_sha256": shadow.digest(shadow.encode(old_implementation)),
                         "files": {name: repack.metadata(raw) for name, raw in self.contents.items()}}
        self.receipt = {"schema": "wnba-v2-candidate-bundle-receipt-v1", "candidate_id": "synthetic-original",
                        "frozen_at": self.frozen, "created_at": self.frozen, "code_commit": self.old_commit,
                        "implementation_sha256": self.manifest["implementation_sha256"],
                        "model_recipe_hash": self.recipe["recipe_hash"],
                        "recipe_sha256": shadow.digest(self.contents["recipe.json"]),
                        "seed_sha256": shadow.digest(self.contents["seed.jsonl.gz"]),
                        "seed_observations": len(self.seed), "seed_max_season": 2025,
                        "last_seed_event_at": shadow.stamp(self.seed[0].effective_at),
                        "active": False, "qualified": False, "durable_seal": None}
        self.bundle, self.receipt_path = self.root / "original.zip", self.root / "receipt.json"
        self.save_original()

    def save_original(self, *, extra=None):
        self.contents["MANIFEST.json"] = shadow.encode(self.manifest)
        self.bundle.write_bytes(archive_bytes(self.contents, extra=extra))
        self.receipt["bundle_sha256"] = shadow.digest(self.bundle.read_bytes())
        self.receipt_path.write_bytes(shadow.encode(self.receipt))
        self.manifest_sha256 = shadow.digest(self.contents["MANIFEST.json"])
        self.receipt_sha256 = shadow.digest(self.receipt_path.read_bytes())

    def run_repack(self, **kwargs):
        values = {"bundle": self.bundle, "receipt": self.receipt_path, "expectation": self.expectation_path,
                  "output": self.root / "replacement", "code_repo": self.repo, "old_code_commit": self.old_commit,
                  "code_commit": self.new_commit, "amendment_commit": self.new_commit,
                  "receipt_sha256": self.receipt_sha256, "manifest_sha256": self.manifest_sha256,
                  "clock": lambda: NOW}
        values.update(kwargs)
        return repack.repackage(**values)

    def test_replacement_preserves_selected_recipe_seed_audits_and_provenance(self):
        original_archive = self.bundle.read_bytes()
        with patch("research.engine.sources.load_historical", side_effect=AssertionError("No source reopen")), \
                patch("research.engine.model.fit", side_effect=AssertionError("No refit")), \
                patch("research.wnba_v2.comparison.decision", side_effect=AssertionError("No reselection")):
            result = self.run_repack()
        replacement = self.root / "replacement"
        manifest, contents = repack.checked_archive((replacement / "candidate.zip").read_bytes(), result["bundle_sha256"])
        self.assertEqual(self.bundle.read_bytes(), original_archive)
        for name in repack.ORIGINAL_FILES - {"selection.json"}:
            self.assertEqual(contents[name], self.contents[name])
        selected = runtime.document(contents["selection.json"])
        for key in ("selection", "comparison_artifacts", "comparison_registration", "reconstruction_provenance"):
            self.assertEqual(selected[key], self.selection[key])
        self.assertEqual(contents["original-selection.json"], self.contents["selection.json"])
        self.assertEqual(contents["original-MANIFEST.json"], self.contents["MANIFEST.json"])
        self.assertEqual(contents["original-bundle-receipt.json"], self.receipt_path.read_bytes())
        self.assertEqual(result["recipe_sha256"], self.receipt["recipe_sha256"])
        self.assertEqual(result["seed_sha256"], self.receipt["seed_sha256"])
        self.assertNotEqual(result["implementation_sha256"], self.receipt["implementation_sha256"])
        self.assertEqual(result["frozen_at"], shadow.stamp(NOW))
        self.assertEqual(result["runtime_repack"]["parameter_refits"], 0)
        self.assertFalse(result["active"])
        with zipfile.ZipFile(replacement / "candidate.zip") as zipped:
            self.assertTrue(all(item.date_time == (1980, 1, 1, 0, 0, 0) for item in zipped.infolist()))
        # Compatibility is checked through the real prospective restore path.
        restored_recipe, seed = runtime.restore_bundle(replacement / "candidate.zip", self.root / "restore",
                                                       result, self.repo, self.new_commit)
        self.assertEqual(restored_recipe, self.recipe)
        self.assertEqual(seed.read_bytes(), self.contents["seed.jsonl.gz"])

    def test_tampered_zip_member_fails_even_with_refreshed_outer_archive_hash(self):
        self.contents["seed-quality.json"] = b"truncated"
        self.save_original()
        with self.assertRaisesRegex(ValueError, "member hash or size"):
            self.run_repack()
        self.assertFalse((self.root / "replacement").exists())

    def test_duplicate_or_unsafe_archive_members_fail_before_output(self):
        for name in ("recipe.json", "../escape", "/absolute"):
            with self.subTest(name=name):
                self.save_original(extra=(name, b"invalid"))
                with self.assertRaises((ValueError, runtime.RuntimeFailure)):
                    self.run_repack()
                self.assertFalse((self.root / "replacement").exists())

    def test_original_receipt_and_manifest_require_the_supplied_hashes(self):
        for key in ("receipt_sha256", "manifest_sha256"):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "hash differs"):
                self.run_repack(**{key: "0" * 64})

    def test_omitting_original_bound_code_fails_even_with_self_consistent_hashes(self):
        del self.manifest["implementation_files"]["research/wnba_v2/comparison.py"]
        changed = shadow.digest(shadow.encode(self.manifest["implementation_files"]))
        self.manifest["implementation_sha256"] = self.receipt["implementation_sha256"] = changed
        self.save_original()
        with self.assertRaisesRegex(ValueError, "pinned Git bytes"):
            self.run_repack()

    def test_committed_model_or_source_changes_are_not_runtime_repairs(self):
        path = self.repo / "research/wnba_v2/model.py"
        path.write_text("new model coefficients\n")
        command(self.repo, "add", ".")
        command(self.repo, "commit", "-m", "Forbidden model change")
        with self.assertRaisesRegex(ValueError, "exceed the registered storage repair"):
            self.run_repack(code_commit=command(self.repo, "rev-parse", "HEAD"))

    def test_dirty_code_does_not_receive_a_clean_commit_identity(self):
        (self.repo / "research/wnba_v2/shadow.py").write_text("dirty\n")
        with self.assertRaisesRegex(ValueError, "dirty"):
            self.run_repack()

    def test_unrecognized_amendment_commit_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unrecognized runtime"):
            self.run_repack(amendment_commit=self.old_commit)

    def test_changed_expectation_cannot_reselect_a_candidate(self):
        self.expectation["variance_mode"] = "rate"
        self.expectation_path.write_bytes(shadow.encode(self.expectation))
        with self.assertRaisesRegex(ValueError, "Expectation differs"):
            self.run_repack()

    def test_seed_summary_must_match_the_exact_saved_observations(self):
        self.receipt["seed_observations"] = 999
        self.save_original()
        with self.assertRaisesRegex(ValueError, "seed summary differs"):
            self.run_repack()

    def test_even_rehashed_post_2025_seed_cannot_enter_replacement(self):
        seed_path = self.root / "post-2025.jsonl.gz"
        future.write_observations(seed_path, (observation("protected", year=2026),))
        self.contents["seed.jsonl.gz"] = seed_path.read_bytes()
        self.manifest["files"]["seed.jsonl.gz"] = repack.metadata(seed_path.read_bytes())
        self.receipt["seed_sha256"] = shadow.digest(seed_path.read_bytes())
        self.save_original()
        with self.assertRaisesRegex(ValueError, "post-2025"):
            self.run_repack()

    def test_old_and_new_freeze_clocks_cannot_be_backdated(self):
        with self.assertRaisesRegex(ValueError, "follow the original"):
            self.run_repack(clock=lambda: NOW - timedelta(days=1))
        self.receipt["created_at"] = shadow.stamp(NOW + timedelta(hours=1))
        self.save_original()
        with self.assertRaisesRegex(ValueError, "clocks are invalid"):
            self.run_repack()

    def test_truncated_local_write_fails_without_creating_a_success_receipt(self):
        actual = shadow.write_once
        def damaged(path, raw):
            actual(path, raw)
            if Path(path).name == "seed-quality.json":
                Path(path).write_bytes(raw[:-1])
        with patch.object(shadow, "write_once", side_effect=damaged):
            with self.assertRaisesRegex(ValueError, "stored-byte verification"):
                self.run_repack()
        self.assertFalse((self.root / "replacement/bundle-receipt.json").exists())
        self.assertTrue((self.root / "replacement/seed-quality.json").exists())

    def test_existing_output_cannot_be_overwritten(self):
        (self.root / "replacement").mkdir()
        sentinel = self.root / "replacement/keep.txt"
        sentinel.write_bytes(b"preserved")
        with self.assertRaises(FileExistsError):
            self.run_repack()
        self.assertEqual(sentinel.read_bytes(), b"preserved")

    def test_verified_member_changed_later_cannot_enter_archive(self):
        actual = shadow.write_once
        def damaged(path, raw):
            actual(path, raw)
            if Path(path).name == "source-manifest.json":
                Path(path).with_name("seed-quality.json").write_bytes(b"later corruption")
        with patch.object(shadow, "write_once", side_effect=damaged):
            with self.assertRaisesRegex(ValueError, "changed before packaging"):
                self.run_repack()
        self.assertFalse((self.root / "replacement/bundle-receipt.json").exists())


if __name__ == "__main__":
    unittest.main()
