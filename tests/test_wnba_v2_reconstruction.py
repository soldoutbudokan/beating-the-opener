"""Lost-file recovery preserves source/clock boundaries and honest provenance."""
from contextlib import ExitStack
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
from time import perf_counter
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from research.engine import model as original
from research.wnba_v2 import reconstruction as recovery, shadow
from tests.test_engine_model import history, player


class ReconstructionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.raw = self.root / "raw"
        self.raw.mkdir()
        self.manifest = {
            "source_commit": recovery.sources.SOURCE_COMMIT, "complete": True,
            "recorded_at_utc": "2026-09-06T03:53:52+00:00", "assets": [
                {"kind": kind, "season": year, "filename": f"{kind}-{year}.parquet",
                 "url": f"https://raw.githubusercontent.com/example/{recovery.sources.SOURCE_COMMIT}/wnba/{kind}-{year}.parquet",
                 "bytes": 10, "sha256": "a" * 64}
                for kind in sorted(recovery.sources.KINDS)
                for year in range(2010 if kind == "pbp" else 2003, 2026)]}
        manifest_bytes = shadow.encode(self.manifest)
        (self.raw / "manifest.json").write_bytes(manifest_bytes)
        records = history() + [player("later", datetime(2023, 6, 1, tzinfo=timezone.utc))]
        self.observations = [replace(r, source_id="wehoop:" + recovery.sources.SOURCE_COMMIT) for r in records]
        self.reg = {"path": recovery.REGISTRATION_PATH, "commit": recovery.REGISTRATION_COMMIT,
                    "sha256": "d" * 64}
        self.code_path = "research/clocks.py"
        self.code_bytes = (recovery.ROOT / self.code_path).read_bytes()
        self.implementation = {"commit": "c" * 40, "files": {
            self.code_path: {"bytes": len(self.code_bytes), "sha256": shadow.digest(self.code_bytes)}}}
        self.environment = {"python": "3.12.13", "dependencies": recovery.DEPENDENCIES,
                            "machine": "test", "system": "test", "numpy_configuration": {"synthetic": True}}
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for target, value in (
            ("sources.SOURCE_MANIFEST_SHA256", shadow.digest(manifest_bytes)),
            ("CODE_PATHS", {self.code_path}),
        ):
            self.stack.enter_context(patch("research.wnba_v2.reconstruction." + target, value))
        self.stack.enter_context(patch.object(recovery, "_registration", return_value=self.reg))
        self.stack.enter_context(patch.object(recovery, "_implementation", return_value=self.implementation))
        self.stack.enter_context(patch.object(recovery, "_environment", return_value=self.environment))
        self.stack.enter_context(patch.object(recovery, "_git", return_value=self.code_bytes))
        self.stack.enter_context(patch.object(recovery.previous, "load_sources", side_effect=self.load_sources))
        self.stack.enter_context(patch.object(recovery.sources, "observations", side_effect=lambda *a, **k: self.observations))

    def load_sources(self, raw, out, deadline):
        recovery._write(out / "source-manifest.json", dict(self.manifest, assets=recovery.previous.retained_assets(self.manifest)))
        recovery._write(out / "sample.json", [{"season": 2023, "game_id": "synthetic"}])
        return SimpleNamespace(quality={"synthetic": True}), []

    def recover(self, name="primary", **kwargs):
        out = self.root / name
        result = recovery.recover_initial_recipe(self.raw, out, recovery.ROOT / recovery.REGISTRATION_PATH,
            recovery.REGISTRATION_COMMIT, deadline=perf_counter() + 30, **kwargs)
        return out, result

    def rehash(self, root, name):
        receipt = shadow.load(root / "receipt.json")
        receipt["artifacts"][name] = shadow.digest((root / name).read_bytes())
        (root / "receipt.json").write_bytes(shadow.encode(receipt))

    def test_one_fixed_fit_excludes_later_data_before_estimator(self):
        with patch.object(original, "fit", wraps=original.fit) as fit:
            out, (recipe, provenance) = self.recover()
        fit.assert_called_once()
        self.assertEqual(fit.call_args.kwargs, {"through_season": 2022, "shrinkage": 1, "variant": "box"})
        self.assertTrue(all(r.season <= 2022 and r.effective_at.year <= 2022 and r.available_at.year <= 2022
                            for r in fit.call_args.args[0]))
        self.assertEqual(recovery.validate_reconstruction(out, expected_recipe=recipe), (recipe, provenance))
        self.assertFalse(provenance["original_artifact_identity_verified"])
        self.assertNotIn("library_file_id", json.dumps(provenance))

    def test_separate_reconstruction_reproduces_substantive_artifacts(self):
        a, left = self.recover()
        b, right = self.recover("reproduction")
        self.assertEqual(left, right)
        self.assertEqual(recovery.verify_reconstruction_pair(a, b), left)
        with self.assertRaisesRegex(ValueError, "directories"):
            recovery.verify_reconstruction_pair(a, a)

    def test_copied_execution_is_not_reproduction(self):
        import shutil
        a, _ = self.recover()
        b = self.root / "copy"
        shutil.copytree(a, b)
        with self.assertRaisesRegex(ValueError, "Copied execution"):
            recovery.verify_reconstruction_pair(a, b)

    def test_missing_or_modified_artifact_cannot_pass(self):
        root, _ = self.recover()
        (root / "recipe.json").write_bytes(b"{}")
        with self.assertRaisesRegex(ValueError, "hash differs"):
            recovery.validate_reconstruction(root)
        (root / "recipe.json").unlink()
        with self.assertRaisesRegex(ValueError, "missing reconstruction files"):
            recovery.validate_reconstruction(root)

    def test_rehashed_out_of_bounds_input_is_still_rejected(self):
        root, _ = self.recover()
        entries = recovery._read_inputs(root / "fit-inputs.jsonl.gz")
        entries[0]["season"] = 2025
        (root / "fit-inputs.jsonl.gz").unlink()
        recovery.previous.write_rows(root / "fit-inputs.jsonl.gz", entries)
        self.rehash(root, "fit-inputs.jsonl.gz")
        with self.assertRaisesRegex(ValueError, "fixed 2022 bound"):
            recovery.validate_reconstruction(root)

    def test_rehashed_recipe_cannot_claim_other_training_population(self):
        root, _ = self.recover()
        recipe = shadow.load(root / "recipe.json")
        recipe["fit_input_rows"] += 1
        recipe.pop("recipe_hash")
        recipe["recipe_hash"] = recovery.payload_digest(recipe)
        (root / "recipe.json").write_bytes(shadow.encode(recipe))
        self.rehash(root, "recipe.json")
        with self.assertRaisesRegex(ValueError, "fit_input_rows"):
            recovery.validate_reconstruction(root)

    def test_naive_future_and_out_of_order_execution_clocks_fail(self):
        root, _ = self.recover()
        saved = shadow.load(root / "receipt.json")
        for value in ("2026-09-08T00:00:00", shadow.stamp(datetime.now(timezone.utc) + timedelta(days=1)),
                      "2000-01-01T00:00:00Z"):
            amended = dict(saved, completed_at=value)
            (root / "receipt.json").write_bytes(shadow.encode(amended))
            with self.assertRaises(ValueError):
                recovery.validate_reconstruction(root)

    def test_rehashed_unpinned_dependencies_fail(self):
        root, _ = self.recover()
        environment = shadow.load(root / "environment.json")
        environment["dependencies"]["numpy"] = "999.0"
        (root / "environment.json").write_bytes(shadow.encode(environment))
        self.rehash(root, "environment.json")
        with self.assertRaisesRegex(ValueError, "Unpinned"):
            recovery.validate_reconstruction(root)

    def test_old_start_cannot_hide_elapsed_budget_or_clock_jump(self):
        root, _ = self.recover()
        receipt = shadow.load(root / "receipt.json")
        started = shadow.load(root / "started.json")
        for seconds in (5, 601, 60 * 60 * 24 * 30):
            # Both execution records agree on this valid past timestamp. Their
            # short claimed monotonic duration must still disagree with UTC.
            old_start = shadow.stamp(shadow.instant(receipt["completed_at"]) - timedelta(seconds=seconds))
            (root / "receipt.json").write_bytes(shadow.encode(dict(receipt, started_at=old_start)))
            (root / "started.json").write_bytes(shadow.encode(dict(started, started_at=old_start)))
            with self.assertRaisesRegex(ValueError, "wall and monotonic clocks disagree"):
                recovery.validate_reconstruction(root)

    def test_original_manifest_mismatch_stops_before_any_fit(self):
        (self.raw / "manifest.json").write_bytes(b"{}")
        with patch.object(original, "fit") as fit:
            with self.assertRaisesRegex(ValueError, "public source manifest changed"):
                self.recover()
        fit.assert_not_called()
        receipt = shadow.load(self.root / "primary/receipt.json")
        self.assertEqual(receipt["status"], "stopped")
        with self.assertRaises(FileExistsError):
            self.recover()

    def test_source_checksum_error_is_preserved_without_retry(self):
        with patch.object(recovery.previous, "load_sources", side_effect=ValueError("Old source bytes changed")) as loader:
            with patch.object(original, "fit") as fit:
                with self.assertRaisesRegex(ValueError, "source bytes"):
                    self.recover()
        loader.assert_called_once()
        fit.assert_not_called()
        self.assertEqual(shadow.load(self.root / "primary/receipt.json")["error"], "Old source bytes changed")

    def test_elapsed_deadline_has_stopped_receipt_and_no_budget_reset(self):
        out = self.root / "expired"
        with patch.object(original, "fit") as fit:
            with self.assertRaisesRegex(ValueError, "budget exhausted"):
                recovery.recover_initial_recipe(self.raw, out, "unused", recovery.REGISTRATION_COMMIT,
                                                deadline=perf_counter() - 1)
        fit.assert_not_called()
        self.assertEqual(shadow.load(out / "receipt.json")["status"], "stopped")

    def test_future_availability_in_old_season_is_excluded(self):
        old = self.observations[0]
        late = replace(old, record_id="late-correction", assumed_available_at=datetime(2023, 1, 1, tzinfo=timezone.utc))
        self.observations.append(late)
        with patch.object(original, "fit", wraps=original.fit) as fit:
            self.recover()
        self.assertNotIn("late-correction", [r.record_id for r in fit.call_args.args[0]])


if __name__ == "__main__":
    unittest.main()
