"""Checks reconstruction endpoint, original arithmetic and review honesty."""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from research.prospective_props.runner import endpoint_population, registered_cluster, score_segment, new_directory, verify_published_freeze
from research.prospective_props.review import review, validate_population
from research.prospective_props import runner


def review_fixture():
    """Entirely synthetic governance fixture; no empirical score is opened."""
    empty = {"bets": 0, "profit": 0., "stake": 0., "roi": None,
             "registered_t": None, "selections": []}
    def segment(n):
        return {"n": n, "log_loss_model": .602, "log_loss_opener": .6,
                "model_minus_opener": {"estimate": .002, "registered_t": 1., "ci95": [-.001, .005]},
                "model_minus_close": {"n": n, "estimate": .003, "registered_t": 2.},
                "economics": {"candidate": deepcopy(empty), "placebo": deepcopy(empty)}}
    results = {"schema": "prospective-props-reconstruction-v1", "arm": "fp-prospective-1",
               "original_forecast_bytes_recovered": False,
               "population": {"endpoint_n": 3001, "endpoint_date": "2026-08-04", "minimum": 3000,
                              "endpoint_rule": "entire_first_ET_date_reaching_threshold", "available_eligible_n": 3006,
                              "daily_counts": [{"date": "2026-08-01", "n": 500, "cumulative": 500},
                                               {"date": "2026-08-04", "n": 2501, "cumulative": 3001},
                                               {"date": "2026-08-05", "n": 5, "cumulative": 3006}]},
               "segments": {"overall": segment(3001), "august_1_3": segment(500), "august_4_onward": segment(2501)}}
    receipt = {"status": "complete", "arm": "fp-prospective-1", "reconstructed": True,
               "recipe_sha256": "a" * 64, "forecasts_sha256": "b" * 64,
               "implementation_sha256": "c" * 64, "generation_implementation_sha256": "d" * 64,
               "registration_sha256": "e" * 64, "attestation_sha256": "f" * 64,
               "registration_commit": "a" * 40, "freeze_commit": "b" * 40}
    return results, receipt


def signed_review(results, receipt):
    contents = json.dumps(results, allow_nan=False).encode()
    receipt = {**receipt, "results_sha256": hashlib.sha256(contents).hexdigest()}
    return review(results, receipt, results_bytes=contents)


def sealed_fixture(root):
    """Synthetic private storage receipt plus exact local ZIP snapshot."""
    paths = [path for path in root.rglob("*") if path.is_file()]
    bundle = root / "private-snapshot.zip"
    with runner.zipfile.ZipFile(bundle, "x") as archive:
        for path in paths:
            archive.write(path, path.relative_to(root).as_posix())
    seal = {"schema": "prospective-props-private-seal-v1", "library_file_id": "synthetic-file",
            "library_version_id": "synthetic-version", "bundle_path": str(bundle),
            "bundle_sha256": runner.digest(bundle), "attestation_sha256": runner.digest(root / runner.ATTESTATION_PATH)}
    path = root / "private-seal.json"
    path.write_text(json.dumps(seal))
    return path


class ProspectivePropsTests(unittest.TestCase):
    def test_endpoint_keeps_entire_crossing_day_and_excludes_void_push(self):
        rows = []
        for date, number in (("2026-08-01", 2), ("2026-08-02", 3), ("2026-08-03", 4)):
            rows.extend(dict(date=date, matched=True, void=False, open_coherent=True,
                             actual=6., open_line=5.5) for _ in range(number))
        rows.extend([dict(date="2026-08-01", matched=True, void=True, open_coherent=True, actual=0., open_line=5.5),
                     dict(date="2026-08-01", matched=True, void=False, open_coherent=True, actual=5., open_line=5.)])
        cohort, result = endpoint_population(pd.DataFrame(rows), minimum=3)
        self.assertEqual(result["endpoint_date"], "2026-08-02")
        self.assertEqual(result["endpoint_n"], 5)
        self.assertEqual(len(cohort), 5)
        with self.assertRaises(ValueError):
            endpoint_population(pd.DataFrame(rows), minimum=10)

    def test_native_cluster_formula_and_original_multiple_prop_selection(self):
        result = registered_cluster([1., 1., -1.], ["a", "a", "b"])
        self.assertAlmostEqual(result["estimate"], 1 / 3)
        self.assertAlmostEqual(result["registered_t"], (1 / 3) / math.sqrt(32 / 81))
        rows = [dict(event_id=1, date="2026-08-01", market=market, player="Same Player",
                     open_line=5.5, open_over_cost=100., open_under_cost=100.,
                     actual=6., p_model=.7, p_open=.5, p_close=.55, coh_close=True, line_close=5.5)
                for market in ("points", "rebounds")]
        scored = score_segment(rows)
        self.assertAlmostEqual(scored["log_loss_model"], -math.log(.7))
        self.assertEqual(scored["economics"]["candidate"]["bets"], 2)
        self.assertEqual(scored["economics"]["candidate"]["profit"], 2.)
        self.assertEqual(scored["economics"]["placebo"]["bets"], 0)

    def test_review_refuses_original_byte_claim_population_and_hash_tamper(self):
        results, receipt = review_fixture()
        contents = json.dumps(results).encode()
        receipt["results_sha256"] = hashlib.sha256(contents).hexdigest()
        self.assertTrue(review(results, receipt, results_bytes=contents)["primary_pass"])
        for field, value in (("original_forecast_bytes_recovered", True), ("arm", "wrong")):
            changed = deepcopy(results)
            changed[field] = value
            with self.assertRaises(ValueError):
                signed_review(changed, receipt)
        with self.assertRaises(ValueError):
            review(results, receipt, results_bytes=b"tampered")
        results["segments"]["august_1_3"]["n"] = 499
        with self.assertRaises(ValueError):
            signed_review(results, receipt)

    def test_review_binds_object_to_bytes_and_rejects_fake_provenance(self):
        results, receipt = review_fixture()
        contents = json.dumps(results).encode()
        receipt["results_sha256"] = hashlib.sha256(contents).hexdigest()
        changed = deepcopy(results)
        changed["segments"]["overall"]["model_minus_opener"]["estimate"] = -.1
        with self.assertRaisesRegex(ValueError, "hashed saved bytes"):
            review(changed, receipt, results_bytes=contents)
        for field, invalid in (("recipe_sha256", "a"), ("implementation_sha256", None),
                               ("generation_implementation_sha256", "x" * 64),
                               ("freeze_commit", "latest"), ("attestation_sha256", ""),
                               ("reconstructed", False)):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    signed_review(results, {**receipt, field: invalid})
        with self.assertRaises(ValueError):
            review(results, receipt)

    def test_review_rejects_late_endpoint_daily_arithmetic_and_asg_misallocation(self):
        results, receipt = review_fixture()
        mutations = [lambda r: r["population"].update(endpoint_date="2026-08-05", endpoint_n=3006),
                     lambda r: r["population"]["daily_counts"][1].update(cumulative=3000),
                     lambda r: r["population"]["daily_counts"].reverse(),
                     lambda r: r["population"].update(available_eligible_n=3001),
                     lambda r: r["population"]["daily_counts"][0].update(n=True),
                     lambda r: r["segments"]["august_1_3"].update(n=501),
                     lambda r: r["segments"]["overall"]["model_minus_opener"].update(estimate=-.1),
                     lambda r: r["segments"]["overall"]["economics"]["candidate"].update(roi=.1),
                     lambda r: r["segments"]["overall"]["model_minus_close"].update(n=4000)]
        for mutate in mutations:
            changed = deepcopy(results)
            mutate(changed)
            with self.assertRaises(ValueError):
                signed_review(changed, receipt)
        early = {"minimum": 3000, "endpoint_rule": "entire_first_ET_date_reaching_threshold",
                 "endpoint_date": "2026-08-01", "endpoint_n": 3001, "available_eligible_n": 3005,
                 "daily_counts": [{"date": "2026-08-01", "n": 3001, "cumulative": 3001},
                                  {"date": "2026-08-02", "n": 4, "cumulative": 3005}]}
        self.assertEqual(validate_population(early), {"early_n": 3001, "later_n": 0})

    def test_published_freeze_refuses_missing_commit_and_unbound_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for commit in (None, "main", "x" * 40):
                with self.assertRaises(ValueError):
                    verify_published_freeze(root, root / "outside", commit)
            with self.assertRaises(ValueError):
                verify_published_freeze(root, root / "outside", "a" * 40)

    def test_published_freeze_rejects_changed_bootstrap_dependency(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            public = (*runner.EVALUATION_CODE, runner.REGISTRATION_PATH, runner.PRIVATE_EXECUTION_PATH)
            private = tuple(runner.FROZEN_ROOT + "/" + name for name in runner.FROZEN_FILES)
            paths = (*public,
                     *(runner.FROZEN_ROOT + "/" + name for name in runner.FROZEN_FILES))
            original = {path: b"synthetic committed bytes" for path in paths}
            attestation = {"schema": "prospective-props-published-freeze-v1",
                           "public_files": list(public), "private_files": list(private),
                           "artifact_visibility": runner.EVIDENCE_VISIBILITY,
                           "publication_approval_reason": runner.PUBLICATION_APPROVAL_REASON,
                           "files": {path: hashlib.sha256(body).hexdigest() for path, body in original.items()}}
            for name, body in original.items():
                destination = root / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(body)
            (root / runner.ATTESTATION_PATH).write_text(json.dumps(attestation))
            seal_path = sealed_fixture(root)
            (root / "research/engine/scoring.py").write_bytes(b"changed bootstrap")
            def committed(command, **kwargs):
                name = command[-1].split(":", 1)[1]
                return json.dumps(attestation).encode() if name == runner.ATTESTATION_PATH else original[name]
            with patch.object(runner.subprocess, "check_output", side_effect=committed):
                with self.assertRaisesRegex(ValueError, "scoring.py"):
                    verify_published_freeze(root, root / runner.FROZEN_ROOT, "a" * 40, seal_path)

    def test_private_library_seal_binds_artifacts_without_reading_them_from_git(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            frozen = root / runner.FROZEN_ROOT
            frozen.mkdir(parents=True)
            public = (*runner.EVALUATION_CODE, runner.REGISTRATION_PATH, runner.PRIVATE_EXECUTION_PATH)
            for name in public:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"public code or amendment")
            for name in runner.FROZEN_FILES:
                (frozen / name).write_bytes(b"private artifact")
            population = review_fixture()[0]["population"]
            (frozen / "population.json").write_text(json.dumps(population))
            recipe = {"parameters_sha256": runner.digest(frozen / "parameters.pkl"),
                      "parameters_json_sha256": runner.digest(frozen / "parameters.json")}
            (frozen / "recipe.json").write_text(json.dumps(recipe))
            prepared = {"population": population, "recipe_sha256": runner.digest(frozen / "recipe.json"),
                        "implementation_sha256": "a" * 64,
                        "forecasts": {arm: {"path": f"{arm}.forecasts.json.gz",
                                            "sha256": runner.digest(frozen / f"{arm}.forecasts.json.gz")}
                                      for arm in ("fp-prospective-1", "fp-prospective-2")}}
            (frozen / "prepared.json").write_text(json.dumps(prepared))
            runner.attest(root)
            seal_path = sealed_fixture(root)
            committed_bytes = {path: (root / path).read_bytes() for path in public}
            def committed(command, **kwargs):
                path = command[-1].split(":", 1)[1]
                self.assertIn(path, committed_bytes, "Private artifact must not be requested from Git")
                return committed_bytes[path]
            with patch.object(runner.subprocess, "check_output", side_effect=committed):
                checked, _, _ = verify_published_freeze(root, frozen, "a" * 40, seal_path)
                self.assertEqual(checked, prepared)
                (frozen / "parameters.pkl").write_bytes(b"tampered private parameters")
                with self.assertRaisesRegex(ValueError, "parameters.pkl"):
                    verify_published_freeze(root, frozen, "a" * 40, seal_path)

    def test_reconstruction_output_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "fresh"
            new_directory(path)
            (path / "sentinel").write_text("preserve")
            with self.assertRaises(FileExistsError):
                new_directory(path)
            self.assertEqual((path / "sentinel").read_text(), "preserve")


if __name__ == "__main__":
    unittest.main()
