"""Recovery cannot freeze a partial run, invent an old seal or skip reproduction."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from research.wnba_v2 import comparison as runner, prepare_candidate as freeze, reconstruction, shadow
from tests.test_wnba_v2_prepare_candidate import comparison


PROVENANCE = {"schema": reconstruction.SCHEMA, "original_artifact_identity_verified": False,
              "synthetic": True}
RECOVERY_REG = {"path": reconstruction.REGISTRATION_PATH, "commit": reconstruction.REGISTRATION_COMMIT,
                "sha256": "d" * 64}


def replace_json(root, name, value, *, rehash=False):
    raw = shadow.encode(value)
    (root / name).write_bytes(raw)
    if rehash:
        receipt = shadow.load(root / "receipt.json")
        for item in receipt["artifacts"]:
            if item["path"] == name:
                item["sha256"] = shadow.digest(raw)
                break
        else:
            receipt["artifacts"].append({"path": name, "sha256": shadow.digest(raw)})
        (root / "receipt.json").write_bytes(shadow.encode(receipt))


def recovery_comparison(root, *, execution_id):
    comparison(root)
    candidate = shadow.load(root / "candidate-recipe.json")
    result = shadow.load(root / "results.json")
    result["inherited_recipe_provenance"] = deepcopy(PROVENANCE)
    replace_json(root, "results.json", result, rehash=True)
    expectation = {"schema": "wnba-v2-reconstruction-expectation-v1",
                   "model_recipe_hash": candidate["recipe_hash"], "variance_mode": candidate["variance_mode"],
                   "source": "prior_conversation_record"}
    replace_json(root, "expectation.json", expectation, rehash=True)
    receipt = shadow.load(root / "receipt.json")
    receipt.update(reconstruction_registration=RECOVERY_REG, reconstruction_provenance=PROVENANCE)
    replace_json(root, "receipt.json", receipt)
    (root / "reconstruction").mkdir()
    inner = {"execution_id": execution_id, "started_at": "2020-01-01T00:01:00Z",
             "completed_at": "2020-01-01T00:02:00Z"}
    replace_json(root, "reconstruction/receipt.json", inner)
    start = {"schema": "wnba-v2-reconstruction-execution-v1", "status": "started",
             "started_at": "2020-01-01T00:00:00Z", "registration": RECOVERY_REG, "budget_seconds": 600,
             "expectation_sha256": shadow.digest((root / "expectation.json").read_bytes())}
    execution = dict(start, status="complete", completed_at="2020-01-01T00:05:00Z",
                     elapsed_seconds=300., error=None, original_artifact_identity_verified=False,
                     comparison_receipt_sha256=shadow.digest((root / "receipt.json").read_bytes()))
    replace_json(root, "execution-start.json", start)
    replace_json(root, "execution.json", execution)


def accepted_inheritance(directory, **kwargs):
    # The inherited fitter and its full saved-input validator have separate
    # tests. Here the surrounding freeze gates must run on real saved files.
    return shadow.load(Path(directory).parent / "inherited-recipe.json"), deepcopy(PROVENANCE)


class RecoveryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.primary, self.reproduction = self.root / "primary", self.root / "reproduction"
        recovery_comparison(self.primary, execution_id="a" * 32)
        recovery_comparison(self.reproduction, execution_id="b" * 32)
        validator = patch.object(reconstruction, "validate_reconstruction", side_effect=accepted_inheritance)
        self.validate = validator.start()
        self.addCleanup(validator.stop)

    def test_matching_complete_executions_allow_only_the_recorded_candidate(self):
        receipt, _, _, candidate = freeze.reproduced_candidate(self.primary, self.reproduction)
        self.assertEqual(receipt["reconstruction_registration"], RECOVERY_REG)
        self.assertEqual(candidate["recipe_hash"], shadow.load(self.primary / "expectation.json")["model_recipe_hash"])
        self.assertGreaterEqual(self.validate.call_count, 4)

    def test_stopped_or_in_progress_execution_cannot_freeze_complete_inner_comparison(self):
        saved = shadow.load(self.primary / "execution.json")
        for status in ("stopped", "started"):
            with self.subTest(status=status):
                replace_json(self.primary, "execution.json", dict(saved, status=status))
                with self.assertRaisesRegex(ValueError, "Incomplete or invalid"):
                    freeze.checked_comparison(self.primary)

    def test_missing_completion_or_start_receipt_blocks_freeze(self):
        for filename in ("execution.json", "execution-start.json"):
            with self.subTest(filename=filename):
                raw = (self.primary / filename).read_bytes()
                (self.primary / filename).unlink()
                try:
                    with self.assertRaises(FileNotFoundError):
                        freeze.checked_comparison(self.primary)
                finally:
                    (self.primary / filename).write_bytes(raw)

    def test_over_budget_negative_or_boolean_elapsed_cannot_be_complete(self):
        saved = shadow.load(self.primary / "execution.json")
        for elapsed in (600.001, -1., True):
            with self.subTest(elapsed=elapsed):
                replace_json(self.primary, "execution.json", dict(saved, elapsed_seconds=elapsed))
                with self.assertRaisesRegex(ValueError, "Incomplete or invalid"):
                    freeze.checked_comparison(self.primary)

    def test_changed_expectation_cannot_relabel_a_valid_research_candidate(self):
        expected = shadow.load(self.primary / "expectation.json")
        expected["model_recipe_hash"] = "e" * 64
        replace_json(self.primary, "expectation.json", expected, rehash=True)
        with self.assertRaisesRegex(ValueError, "recorded identity"):
            freeze.checked_comparison(self.primary)

    def test_expectation_has_to_precede_execution_and_keep_its_recorded_digest(self):
        execution = shadow.load(self.primary / "execution.json")
        execution["expectation_sha256"] = "e" * 64
        replace_json(self.primary, "execution.json", execution)
        with self.assertRaisesRegex(ValueError, "Incomplete or invalid"):
            freeze.checked_comparison(self.primary)

    def test_removing_reconstruction_registration_cannot_select_original_path(self):
        receipt = shadow.load(self.primary / "receipt.json")
        receipt.pop("reconstruction_registration")
        replace_json(self.primary, "receipt.json", receipt)
        with self.assertRaisesRegex(ValueError, "cannot omit"):
            freeze.checked_comparison(self.primary)

    def test_reconstructed_recipe_has_to_match_the_recorded_inheritance(self):
        receipt = shadow.load(self.primary / "receipt.json")
        receipt["reconstruction_provenance"] = {"fabricated": True}
        replace_json(self.primary, "receipt.json", receipt)
        with self.assertRaisesRegex(ValueError, "reconstructed inheritance"):
            freeze.checked_comparison(self.primary)

    def test_inner_reconstruction_cannot_fall_outside_full_execution_clock(self):
        inner = shadow.load(self.primary / "reconstruction/receipt.json")
        inner["completed_at"] = "2020-01-01T00:06:00Z"
        replace_json(self.primary, "reconstruction/receipt.json", inner)
        with self.assertRaisesRegex(ValueError, "clocks fall outside"):
            freeze.checked_comparison(self.primary)

    def test_copied_fit_execution_is_not_a_second_reproduction(self):
        inner = shadow.load(self.reproduction / "reconstruction/receipt.json")
        inner["execution_id"] = "a" * 32
        replace_json(self.reproduction, "reconstruction/receipt.json", inner)
        with self.assertRaisesRegex(ValueError, "Copied execution"):
            freeze.reproduced_candidate(self.primary, self.reproduction)

    def test_original_entry_point_still_requires_its_actual_old_seal(self):
        with patch.object(runner, "registration_identity", return_value={"synthetic": True}):
            with patch.object(runner, "_run_comparison") as continuation:
                with self.assertRaises(FileNotFoundError):
                    runner.run(self.root / "raw", self.root / "old-fit", self.root / "bundle.zip",
                               self.root / "missing-seal.json", self.root / "original-missing", "unused", "a" * 40)
                replace_json(self.root, "fake-seal.json", {"schema": "wnba-v2-reconstruction-execution-v1"})
                with self.assertRaisesRegex(ValueError, "Unknown original private seal"):
                    runner.run(self.root / "raw", self.root / "old-fit", self.root / "bundle.zip",
                               self.root / "fake-seal.json", self.root / "original-fake", "unused", "a" * 40)
                continuation.assert_not_called()


if __name__ == "__main__":
    unittest.main()
