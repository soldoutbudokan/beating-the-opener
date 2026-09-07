"""A freeze cannot conceal a changed candidate or failed reproduction."""
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from research.wnba_v2 import prepare_candidate as freeze, shadow, model, comparison as comparison_module
from tests.test_wnba_v2_model import recipe


def comparison(root, *, selected="constant", recipe_mode="constant"):
    root.mkdir()
    saved_recipe = recipe(recipe_mode)
    scalars = {m: dict(values, total_floor=False, noise_floor=False)
               for m, values in saved_recipe["noise_scalars"].items()}
    saved_recipe = model.configuration(saved_recipe["base_recipe"], scalars, recipe_mode)
    source = {"status": "PASS_UNDER_ASSUMPTION"}
    checks = {y: {"points": {"aggregate_rate_uncertainty_share": .1, "rate_minus_constant_score": -.02,
                             "variances": {"rate": {"squared_error_to_variance": 1.}}}}
              for y in ("2023", "2024")}
    counts = {y: {"distributions_valid": True,
                  "markets": {"points": {"rate_minus_constant_nll": -.006 if selected == "rate" else 0.}}}
              for y in checks}
    decision = comparison_module.decision(source, scalars, checks, counts)
    registration, policy = {"commit": "a" * 40}, {"commit": "b" * 40}
    inherited = saved_recipe["base_recipe"]
    inherited_hash = shadow.digest(shadow.encode(inherited))
    calibration = {"synthetic": True}
    calibration_hash = shadow.digest(shadow.encode(calibration))
    values = {
        "results.json": {"source_status": "PASS_UNDER_ASSUMPTION",
                         "scalars": saved_recipe["noise_scalars"],
                         "variances": checks, "counts": counts, "decision": decision,
                         "registration": registration, "source_policy": policy},
        "candidate-recipe.json": saved_recipe,
        "inherited-recipe.json": inherited,
        "calibration-checkpoints.jsonl.gz": calibration,
        "scalar-freeze.json": {"scalars": saved_recipe["noise_scalars"],
                               "calibration_sha256": calibration_hash, "inherited_recipe_sha256": inherited_hash},
        "checkpoints-2023.jsonl.gz": {}, "checkpoints-2024.jsonl.gz": {},
        "count-scores-2023.jsonl.gz": {}, "count-scores-2024.jsonl.gz": {},
        "source-check.json": source,
        "source-quality.json": {}, "repair-cohort.json": {}}
    artifacts = []
    for name, value in values.items():
        data = shadow.encode(value)
        (root / name).write_bytes(data)
        artifacts.append({"path": name, "sha256": shadow.digest(data)})
    (root / "receipt.json").write_bytes(shadow.encode({
        "schema": "wnba-v2-comparison-receipt-v1", "status": "complete",
        "registration": registration, "source_policy": policy,
        "implementation": [{"path": "model.py", "sha256": "c" * 64}], "artifacts": artifacts}))


class CandidateFreezeTests(unittest.TestCase):
    def test_matching_complete_reproduction_allows_only_recorded_recipe(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / "a", Path(tmp) / "b"
            comparison(a)
            comparison(b)
            _, _, result, candidate = freeze.reproduced_candidate(a, b)
            self.assertEqual(candidate["variance_mode"], result["decision"]["selected_variance_mode"])

    def test_changed_saved_bytes_cannot_be_frozen(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a"
            comparison(a)
            (a / "candidate-recipe.json").write_bytes(b"{}")
            with self.assertRaisesRegex(ValueError, "hash differs"):
                freeze.checked_comparison(a)

    def test_rehashed_alternative_cannot_replace_registered_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a"
            comparison(a, selected="constant", recipe_mode="rate")
            with self.assertRaisesRegex(ValueError, "recorded selection"):
                freeze.checked_comparison(a)

    def test_rehashed_noise_change_cannot_replace_saved_scalar_fit(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a"
            comparison(a)
            raw = shadow.encode(recipe(total=3.))
            (a / "candidate-recipe.json").write_bytes(raw)
            receipt = json.loads((a / "receipt.json").read_text())
            for item in receipt["artifacts"]:
                if item["path"] == "candidate-recipe.json":
                    item["sha256"] = shadow.digest(raw)
            (a / "receipt.json").write_bytes(shadow.encode(receipt))
            with self.assertRaisesRegex(ValueError, "frozen scalars"):
                freeze.checked_comparison(a)

    def test_different_source_policy_cannot_pass_exact_reproduction(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / "a", Path(tmp) / "b"
            comparison(a)
            comparison(b)
            receipt = json.loads((b / "receipt.json").read_text())
            receipt["source_policy"] = {"commit": "d" * 40}
            (b / "receipt.json").write_bytes(shadow.encode(receipt))
            with self.assertRaisesRegex(ValueError, "registrations"):
                freeze.reproduced_candidate(a, b)

    def test_same_directory_is_not_a_reproduction(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a"
            comparison(a)
            with self.assertRaisesRegex(ValueError, "different saved executions"):
                freeze.reproduced_candidate(a, a)

    def test_dirty_implementation_cannot_be_labeled_with_pinned_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "research/experiments").mkdir(parents=True)
            (root / freeze.REGISTRATION[0]).write_text("registered")
            (root / "research/example.py").write_text("changed")
            def git_read(*args):
                return SimpleNamespace(stdout=b"original" if args[-1].endswith("example.py") else b"registered")
            with patch("research.wnba_v2.runtime.REQUIRED_CODE", set()), patch("research.wnba_v2.runtime.git", git_read):
                with self.assertRaisesRegex(ValueError, "dirty"):
                    freeze.implementation_manifest(root, "a" * 40)

    def test_comparison_code_and_recognized_policy_are_bound(self):
        implementations = {"research/wnba_v2/" + name: {"sha256": "c" * 64}
                           for name in ("comparison.py", "model.py", "sources.py")}
        implementations.update({freeze.REGISTRATION[0]: {"sha256": "a" * 64},
                                freeze.QUARANTINE[0]: {"sha256": "b" * 64}})
        receipt = {"registration": dict(zip(("path", "commit"), freeze.REGISTRATION), sha256="a" * 64),
                   "source_policy": dict(zip(("path", "commit"), freeze.QUARANTINE), sha256="b" * 64),
                   "implementation": [{"path": name, "sha256": "c" * 64}
                                      for name in ("comparison.py", "model.py", "sources.py")]}
        self.assertTrue(freeze.verify_provenance(receipt, implementations))
        receipt["implementation"][0]["sha256"] = "d" * 64
        with self.assertRaisesRegex(ValueError, "implementation differs"):
            freeze.verify_provenance(receipt, implementations)
        receipt["source_policy"]["commit"] = "e" * 40
        with self.assertRaisesRegex(ValueError, "Unrecognized source"):
            freeze.verify_provenance(receipt, implementations)

    def test_stopped_comparison_cannot_supply_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a"
            comparison(a)
            receipt = json.loads((a / "receipt.json").read_text())
            receipt["artifacts"] = [x for x in receipt["artifacts"] if x["path"] != "candidate-recipe.json"]
            (a / "receipt.json").write_bytes(shadow.encode(receipt))
            with self.assertRaisesRegex(ValueError, "no complete selected candidate"):
                freeze.checked_comparison(a)


if __name__ == "__main__":
    unittest.main()
