"""Prepare a private frozen inference bundle after the registered comparison.

This packages the already selected recipe. It cannot fit or select a model and
does not publish a bundle, observe prices, initialize a live study or claim a seal.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import zipfile

from research.engine import sources as historical
from . import future, shadow, sources

REGISTRATION = ("research/experiments/2026-09-wnba-participation-v2.md", "6b3a072efbefe00b356bc2872b8663bc30163ccb")
QUARANTINE = ("research/experiments/2026-09-wnba-v2-source-quarantine.md", "dec2a5a681090c220a58b9a4f42dda49b944155b")


def metadata(raw):
    return {"sha256": shadow.digest(raw), "bytes": len(raw)}


def checked_comparison(directory):
    directory = Path(directory)
    receipt = shadow.load(directory / "receipt.json")
    if receipt.get("schema") != "wnba-v2-comparison-receipt-v1" or receipt.get("status") != "complete":
        raise ValueError("A completed registered comparison receipt is required")
    artifacts = {}
    for item in receipt.get("artifacts", []):
        name = shadow.identifier(item.get("path"), "comparison artifact")
        if name in artifacts:
            raise ValueError("Duplicate comparison artifact")
        raw = shadow._safe_path(directory, name).read_bytes()
        if shadow.digest(raw) != item.get("sha256"):
            raise ValueError("Comparison artifact hash differs")
        artifacts[name] = item["sha256"]
    required = {"results.json", "candidate-recipe.json", "scalar-freeze.json", "inherited-recipe.json",
                "calibration-checkpoints.jsonl.gz", "source-check.json", "source-quality.json", "repair-cohort.json",
                "checkpoints-2023.jsonl.gz", "checkpoints-2024.jsonl.gz",
                "count-scores-2023.jsonl.gz", "count-scores-2024.jsonl.gz"}
    if not required <= artifacts.keys():
        raise ValueError("Comparison has no complete selected candidate evidence")
    results = shadow.load(directory / "results.json")
    if (results.get("source_status") != "PASS_UNDER_ASSUMPTION"
            or results.get("decision", {}).get("status") != "RESEARCH_ONLY"):
        raise ValueError("Comparison did not authorize a research candidate")
    recipe = shadow.load(directory / "candidate-recipe.json")
    if recipe.get("variance_mode") != results["decision"].get("selected_variance_mode"):
        raise ValueError("Recipe differs from the recorded selection")
    # Link the frozen candidate back to the actual saved calibration, not merely
    # a self-consistent recipe hash and a variance-mode label.
    from .model import ParticipationModel, configuration
    inherited = shadow.load(directory / "inherited-recipe.json")
    scalars = shadow.load(directory / "scalar-freeze.json")
    if (scalars.get("calibration_sha256") != artifacts["calibration-checkpoints.jsonl.gz"]
            or scalars.get("inherited_recipe_sha256") != artifacts["inherited-recipe.json"]
            or scalars.get("scalars") != results.get("scalars")):
        raise ValueError("Calibration, inherited recipe and saved scalar freeze disagree")
    expected = configuration(inherited, scalars["scalars"], results["decision"]["selected_variance_mode"])
    if recipe != expected:
        raise ValueError("Candidate differs from the inherited recipe and frozen scalars")
    from .comparison import decision
    source = shadow.load(directory / "source-check.json")
    if source.get("status") != results.get("source_status"):
        raise ValueError("Saved source status disagrees with source verification")
    if decision(source, scalars["scalars"], results["variances"], results["counts"]) != results["decision"]:
        raise ValueError("Saved candidate decision does not reproduce its fixed gates")
    if results.get("registration") != receipt.get("registration") or results.get("source_policy") != receipt.get("source_policy"):
        raise ValueError("Saved result registrations differ from the execution receipt")
    if "reconstruction_registration" in receipt:
        verify_reconstruction_execution(directory, receipt, artifacts, results, recipe)
    elif "expectation.json" in artifacts or "reconstruction_provenance" in receipt or (directory / "reconstruction").exists():
        raise ValueError("Reconstruction evidence cannot omit its separate provenance")
    ParticipationModel((), recipe)
    return receipt, artifacts, results, recipe


def verify_reconstruction_execution(directory, receipt, artifacts, results, recipe):
    from . import reconstruction
    from .comparison import checked_expectation
    reg = receipt["reconstruction_registration"]
    inherited, provenance = reconstruction.validate_reconstruction(
        directory / "reconstruction", expected_recipe=recipe["base_recipe"], expected_registration=reg)
    if (provenance != receipt.get("reconstruction_provenance") or
            provenance != results.get("inherited_recipe_provenance")):
        raise ValueError("Comparison differs from its reconstructed inheritance")
    if "expectation.json" not in artifacts:
        raise ValueError("Missing recorded candidate expectation")
    expected = checked_expectation(directory / "expectation.json")
    if (recipe["recipe_hash"] != expected["model_recipe_hash"] or
            recipe["variance_mode"] != expected["variance_mode"]):
        raise ValueError("Reconstructed candidate differs from the recorded identity")
    execution = shadow.load(directory / "execution.json")
    start = shadow.load(directory / "execution-start.json")
    began, ended = shadow.instant(execution.get("started_at")), shadow.instant(execution.get("completed_at"))
    elapsed, budget = execution.get("elapsed_seconds"), execution.get("budget_seconds")
    if (execution.get("schema") != "wnba-v2-reconstruction-execution-v1" or execution.get("status") != "complete"
            or start.get("schema") != execution["schema"] or start.get("status") != "started"
            or any(start.get(k) != execution.get(k) for k in
                   ("started_at", "registration", "budget_seconds", "expectation_sha256"))
            or execution.get("registration") != reg or execution.get("error") is not None
            or execution.get("original_artifact_identity_verified") is not False
            or execution.get("expectation_sha256") != artifacts["expectation.json"]
            or execution.get("comparison_receipt_sha256") != shadow.digest((directory / "receipt.json").read_bytes())
            or not began <= ended <= datetime.now(timezone.utc)
            or type(budget) is not int or not 0 < budget <= 600
            or isinstance(elapsed, bool) or not isinstance(elapsed, (int, float))
            or not math.isfinite(elapsed) or not 0 <= elapsed <= budget
            or (ended - began).total_seconds() > budget + 1
            or abs((ended - began).total_seconds() - elapsed) > 1):
        raise ValueError("Incomplete or invalid bounded reconstruction execution")
    inner = shadow.load(directory / "reconstruction/receipt.json")
    if not began <= shadow.instant(inner["started_at"]) <= shadow.instant(inner["completed_at"]) <= ended:
        raise ValueError("Inherited reconstruction clocks fall outside the comparison execution")


def reproduced_candidate(primary, reproduction):
    if Path(primary).resolve() == Path(reproduction).resolve():
        raise ValueError("Primary and reproduction must be different saved executions")
    left = checked_comparison(primary)
    right = checked_comparison(reproduction)
    if (left[1] != right[1] or left[0]["registration"] != right[0]["registration"]
            or left[0].get("source_policy") != right[0].get("source_policy")):
        raise ValueError("Saved-input reproduction differs from the primary artifacts")
    if left[0]["implementation"] != right[0]["implementation"]:
        raise ValueError("Reproduction used different implementation bytes")
    if left[0].get("reconstruction_registration") != right[0].get("reconstruction_registration"):
        raise ValueError("Reconstruction registrations differ")
    if "reconstruction_registration" in left[0]:
        from .reconstruction import verify_reconstruction_pair
        verify_reconstruction_pair(Path(primary) / "reconstruction", Path(reproduction) / "reconstruction")
    return left


def implementation_manifest(code_repo, code_commit):
    from .runtime import REQUIRED_CODE, git
    code_repo = Path(code_repo)
    names = {str(p.relative_to(code_repo)) for p in (code_repo / "research").rglob("*.py")
             if "work" not in p.relative_to(code_repo).parts}
    names |= set(REQUIRED_CODE)
    names |= {str(p.relative_to(code_repo)) for p in (code_repo / "research/experiments").glob("*wnba-v2*.md")}
    names.add("research/experiments/2026-09-wnba-participation-v2.md")
    result = {}
    for name in sorted(names):
        content = shadow._safe_path(code_repo, name).read_bytes()
        committed = git(code_repo, "show", code_commit + ":" + name).stdout
        if content != committed:
            raise ValueError("Implementation is dirty or absent from its pinned commit: " + name)
        result[name] = metadata(content)
    return result


def verify_provenance(receipt, implementations):
    registration = receipt.get("registration", {})
    if (registration.get("path"), registration.get("commit")) != REGISTRATION:
        raise ValueError("Unrecognized comparison registration")
    if registration.get("sha256") != implementations[REGISTRATION[0]]["sha256"]:
        raise ValueError("Comparison registration bytes differ")
    policy = receipt.get("source_policy")
    if policy is not None:
        if (policy.get("path"), policy.get("commit")) != QUARANTINE:
            raise ValueError("Unrecognized source quarantine policy")
        if policy.get("sha256") != implementations[QUARANTINE[0]]["sha256"]:
            raise ValueError("Source policy registration bytes differ")
    expected = {name: implementations["research/wnba_v2/" + name]["sha256"]
                for name in ("comparison.py", "model.py", "sources.py")}
    recovery = receipt.get("reconstruction_registration")
    if recovery is not None:
        from . import reconstruction
        if (recovery.get("path"), recovery.get("commit")) != reconstruction.REGISTRATION:
            raise ValueError("Unrecognized reconstruction registration")
        if recovery.get("sha256") != implementations[reconstruction.REGISTRATION_PATH]["sha256"]:
            raise ValueError("Reconstruction registration bytes differ")
        expected["reconstruction.py"] = implementations["research/wnba_v2/reconstruction.py"]["sha256"]
    recorded = receipt.get("implementation", [])
    if (len(recorded) != len(expected)
            or {x.get("path"): x.get("sha256") for x in recorded} != expected):
        raise ValueError("Comparison implementation differs from the candidate freeze")
    return policy is not None


def prepare(primary, reproduction, raw, output, code_repo, code_commit):
    from .runtime import SHA1, git
    if not SHA1.fullmatch(code_commit):
        raise ValueError("An exact published implementation commit is required")
    code_repo = Path(code_repo)
    if git(code_repo, "rev-parse", "HEAD").stdout.decode().strip() != code_commit:
        raise ValueError("Implementation checkout is not at its pinned commit")
    receipt, artifacts, results, recipe = reproduced_candidate(primary, reproduction)
    implementations = implementation_manifest(code_repo, code_commit)
    quarantine_pre2015 = verify_provenance(receipt, implementations)
    out = Path(output)
    out.mkdir(parents=True, exist_ok=False)
    frozen = datetime.now(timezone.utc)
    candidate_id = "wnba-v2-points-" + recipe["variance_mode"] + "-" + frozen.strftime("%Y%m%dT%H%M%SZ")
    selection = {"schema": "wnba-v2-candidate-selection-v1", "candidate_id": candidate_id,
                 "frozen_at": shadow.stamp(frozen), "selection": results["decision"],
                 "comparison_registration": receipt["registration"],
                 "source_policy": receipt.get("source_policy"),
                 "comparison_artifacts": artifacts, "code_commit": code_commit,
                 "seed_loaded_before_selection": False, "qualified": False}
    if "reconstruction_registration" in receipt:
        selection["reconstruction_registration"] = receipt["reconstruction_registration"]
        selection["reconstruction_provenance"] = receipt["reconstruction_provenance"]
        selection["original_artifact_identity_verified"] = False
    shadow.write_once(out / "selection.json", shadow.encode(selection))
    shadow.write_once(out / "recipe.json", shadow.encode(recipe))
    # This is deliberately after verified candidate selection and its durable
    # local evidence write. It cannot change the recorded recipe or its choice.
    data = historical.load_historical(raw)
    seed = sources.inference_seed_observations(data, frozen_at=frozen)
    quality = data.quality
    shadow.write_once(out / "seed-quality.json", shadow.encode(quality))
    from .comparison import verify_admitted_sources
    try:
        gate = verify_admitted_sources(data, seed, quarantine_pre2015=quarantine_pre2015)
    except (ValueError, KeyError) as error:
        gate = {"status": "FAIL", "reason": str(error)}
    shadow.write_once(out / "seed-gate.json", shadow.encode(gate))
    if gate["status"] != "PASS_UNDER_ASSUMPTION":
        raise ValueError("Seed source gate failed; candidate cannot be activated")
    future.FutureOnlyModel(seed, recipe, frozen_at=frozen)
    future.write_observations(out / "seed.jsonl.gz", seed)
    shadow.write_once(out / "source-manifest.json", shadow.encode(data.manifest))
    if implementation_manifest(code_repo, code_commit) != implementations:
        raise ValueError("Implementation changed while preparing the frozen seed")
    files = {p.name: metadata(p.read_bytes()) for p in sorted(out.iterdir()) if p.is_file()}
    manifest = {"schema": "wnba-shadow-bundle-v1", "candidate_id": candidate_id,
                "frozen_at": shadow.stamp(frozen), "recipe_file": "recipe.json", "seed_file": "seed.jsonl.gz",
                "model_recipe_hash": recipe["recipe_hash"], "implementation_files": implementations,
                "implementation_sha256": shadow.digest(shadow.encode(implementations)), "files": files}
    shadow.write_once(out / "MANIFEST.json", shadow.encode(manifest))
    archive = out / "candidate.zip"
    with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED, compresslevel=6) as zipped:
        for name in ["MANIFEST.json", *sorted(files)]:
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            zipped.writestr(info, (out / name).read_bytes())
    result = {"schema": "wnba-v2-candidate-bundle-receipt-v1", "candidate_id": candidate_id,
              "created_at": shadow.stamp(datetime.now(timezone.utc)), "frozen_at": shadow.stamp(frozen),
              "code_commit": code_commit, "model_recipe_hash": recipe["recipe_hash"],
              "implementation_sha256": manifest["implementation_sha256"],
              "bundle_sha256": shadow.digest(archive.read_bytes()),
              "recipe_sha256": shadow.digest((out / "recipe.json").read_bytes()),
              "seed_sha256": shadow.digest((out / "seed.jsonl.gz").read_bytes()),
              "seed_observations": len(seed), "seed_max_season": max(o.season for o in seed),
              "last_seed_event_at": shadow.stamp(max(o.effective_at for o in seed)),
              "durable_seal": None, "active": False, "qualified": False}
    shadow.write_once(out / "bundle-receipt.json", shadow.encode(result))
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for key in ("primary", "reproduction", "raw", "output", "code-repo"):
        p.add_argument("--" + key, required=True, type=Path)
    p.add_argument("--code-commit", required=True)
    a = p.parse_args()
    print(json.dumps(prepare(a.primary, a.reproduction, a.raw, a.output, a.code_repo, a.code_commit), sort_keys=True))


if __name__ == "__main__":
    main()
