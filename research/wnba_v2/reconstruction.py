"""Recreate a lost inherited recipe without inventing an original private seal.

This is a separately registered recovery execution, not an old-study retry.
It calls the unchanged original estimator once, with its original input policy.
The caller owns the shared comparison deadline; this module never resets it.
"""
from __future__ import annotations

from datetime import datetime, timezone
import gzip
from importlib import metadata
import json
import math
from pathlib import Path
import platform
import re
import subprocess
import sys
from time import perf_counter
import uuid

from research.diagnostics import rate_uncertainty as previous
from research.engine import model as original, sources
from research.engine.store import PointInTimeStore, payload_digest
from . import shadow

ROOT = Path(__file__).resolve().parents[2]
REGISTRATION_PATH = "research/experiments/2026-09-wnba-v2-reconstruction.md"
REGISTRATION_COMMIT = "1ac79e3d856580869ccf4051ec68ca395499b8b8"
REGISTRATION = (REGISTRATION_PATH, REGISTRATION_COMMIT)
SCHEMA = "wnba-v2-inherited-reconstruction-v1"
MAX_SECONDS = 600
PINNED_ENGINE = {
    "research/engine/model.py": "03998bc2aa3626ca4245e658a3fc99a94dcc4acc4f435fc89ff8e00f63b64b67",
    "research/engine/sources.py": "2ee98a2fd115d3e8e8f08854230caa9c8318e339268de3e3ce9f1c65544cf77d",
    "research/engine/store.py": "af233f4bc110bd1bd8f774baa92f4c020845488a65a8c1b944e865fa648dda35",
    "research/clocks.py": "e893cea4fa85f327c4f66568f7c9f05cec23f1eee35d7bb3e1da74ee08f3009c",
}
DEPENDENCIES = {"numpy": "2.3.5", "pandas": "2.2.3", "scipy": "1.17.0",
                "pyarrow": "25.0.1", "requests": "2.32.5"}
CODE_PATHS = set(PINNED_ENGINE) | {
    "research/engine/__init__.py", "research/engine/requirements.txt",
    "research/diagnostics/rate_uncertainty.py", "research/diagnostics/structural_failure.py",
    "research/wnba_v2/reconstruction.py", "research/wnba_v2/shadow.py",
}
REQUIRED_ARTIFACTS = {"original-source-manifest.json", "source-manifest.json", "sample.json",
                      "source-quality.json", "fit-inputs.jsonl.gz", "input-summary.json",
                      "recipe.json", "implementation.json", "environment.json"}


def _now():
    return datetime.now(timezone.utc)


def _git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True).stdout


def _write(path, value):
    shadow.write_once(Path(path), shadow.encode(value))


def _hash(path):
    return shadow.digest(Path(path).read_bytes())


def _require(value, message):
    if not value:
        raise ValueError(message)


def _check_deadline(deadline):
    _require(perf_counter() <= deadline, "Shared reconstruction/comparison budget exhausted")


def _registration(path, commit):
    _require(commit == REGISTRATION_COMMIT, "Unrecognized reconstruction registration commit")
    identity = previous.registration_identity(path, commit)
    _require(identity["path"] == REGISTRATION_PATH, "Unrecognized reconstruction registration")
    return identity


def _implementation():
    commit = _git("rev-parse", "HEAD").decode().strip()
    _require(re.fullmatch(r"[0-9a-f]{40}", commit), "Exact implementation commit required")
    result = {}
    for name in sorted(CODE_PATHS):
        raw = shadow._safe_path(ROOT, name).read_bytes()
        _require(raw == _git("show", commit + ":" + name), "Dirty reconstruction implementation: " + name)
        digest = shadow.digest(raw)
        _require(name not in PINNED_ENGINE or digest == PINNED_ENGINE[name],
                 "Original frozen engine changed: " + name)
        result[name] = {"sha256": digest, "bytes": len(raw)}
    return {"commit": commit, "files": result}


def _environment():
    _require(sys.version_info[:2] == (3, 12), "Reconstruction requires Python 3.12")
    versions = {name: metadata.version(name) for name in DEPENDENCIES}
    _require(versions == DEPENDENCIES, "Reconstruction dependency versions differ from the pinned run")
    import numpy as np
    return {"python": platform.python_version(), "dependencies": versions,
            "machine": platform.machine(), "system": platform.system(),
            "numpy_configuration": np.show_config(mode="dicts")}


def _read_inputs(path):
    with gzip.open(path, "rt") as stream:
        return [json.loads(line) for line in stream]


def _input_summary(entries):
    _require(bool(entries), "Missing inherited fitting inputs")
    seen, seasons, effective, available = set(), [], [], []
    for row in entries:
        year = row.get("season")
        _require(type(year) is int and 2003 <= year <= 2022, "Inherited input season exceeds fixed 2022 bound")
        tip, known = shadow.instant(row.get("effective_at")), shadow.instant(row.get("available_at"))
        _require(2003 <= tip.year <= 2022 and 2003 <= known.year <= 2022 and tip < known,
                 "Inherited input clocks exceed fixed bounds or order")
        _require(row.get("source_id") == "wehoop:" + sources.SOURCE_COMMIT and
                 row.get("kind") == "historical_outcome" and row.get("time_basis") == "assumed" and
                 row.get("assumed_available_at") == row.get("available_at") and
                 row.get("observed_at") is None and row.get("published_at") is None,
                 "Inherited input source/timing policy changed")
        shadow.sha(row.get("payload_hash"), "inherited payload hash")
        key = (row.get("source_id"), row.get("record_id"), row.get("available_at"), row.get("payload_hash"))
        _require(key not in seen, "Duplicate inherited fitting input")
        seen.add(key)
        seasons.append(year)
        effective.append(tip)
        available.append(known)
    ordered = sorted(entries, key=lambda r: (r["source_id"], r["record_id"],
                                            shadow.instant(r["available_at"]), r["payload_hash"]))
    _require(entries == ordered, "Inherited fitting input order changed")
    return {"fit_input_hash": payload_digest({"observations": entries}), "fit_input_rows": len(entries),
            "max_season": max(seasons), "max_effective_at": shadow.stamp(max(effective)),
            "max_available_at": shadow.stamp(max(available)), "through_season": 2022,
            "shrinkage": 1, "variant": "box", "availability_hours": 8,
            "training_cutoff": "24 hours before scheduled tip", "market_prices_read": 0,
            "source_asset_max_season": 2024, "original_artifact_identity_verified": False}


def _recipe_matches(recipe, summary):
    body = dict(recipe)
    claimed = body.pop("recipe_hash", None)
    _require(claimed == payload_digest(body), "Reconstructed recipe content hash differs")
    for key in ("through_season", "shrinkage", "variant", "training_cutoff", "fit_input_rows", "fit_input_hash"):
        _require(recipe.get(key) == summary[key], "Reconstructed recipe/input contract differs: " + key)
    _require(recipe.get("rate_q_multiplier") == .0003 and recipe.get("ridge") == 20. and
             recipe.get("conditional_fano_bounds") == [1., 2.], "Original estimator settings changed")
    original.StructuralModel((), recipe)


def _provenance(receipt):
    # Execution IDs/clocks belong to receipts, not comparison identity. This
    # deterministic link permits independent full-execution artifact comparison.
    return {"schema": SCHEMA, "registration": receipt["registration"],
            "artifact_hashes": receipt["artifacts"],
            "original_artifact_identity_verified": False,
            "meaning": "Reconstructed from pinned public inputs; original private bytes unavailable"}


def recover_initial_recipe(raw, output, registration, registration_commit, *, deadline, budget_seconds=600):
    """Return ``(recipe, provenance)``; write one honest exclusive execution.

    No old fit/seal is created. A failure is terminal for this invocation and
    leaves its started record, partial artifacts and stopped receipt intact.
    """
    _require(type(budget_seconds) is int and 0 < budget_seconds <= MAX_SECONDS, "Invalid reconstruction budget")
    _require(not isinstance(deadline, bool) and isinstance(deadline, (int, float)) and math.isfinite(deadline),
             "Caller must supply a finite shared deadline")
    started_monotonic = perf_counter()
    _require(deadline <= started_monotonic + budget_seconds, "Shared deadline exceeds the fixed budget")
    out = shadow._safe_path(Path(output))
    out.mkdir(parents=True, exist_ok=False)
    started = {"schema": SCHEMA, "execution_id": uuid.uuid4().hex,
               "started_at": shadow.stamp(_now()), "budget_seconds": budget_seconds,
               "original_artifact_identity_verified": False, "status": "started"}
    _write(out / "started.json", started)
    reg = None
    try:
        _check_deadline(deadline)
        reg = _registration(registration, registration_commit)
        _write(out / "implementation.json", _implementation())
        _write(out / "environment.json", _environment())
        manifest_bytes = shadow._safe_path(Path(raw), "manifest.json").read_bytes()
        _require(shadow.digest(manifest_bytes) == sources.SOURCE_MANIFEST_SHA256, "Original public source manifest changed")
        shadow.write_once(out / "original-source-manifest.json", manifest_bytes)
        data, _ = previous.load_sources(raw, out, deadline)
        _check_deadline(deadline)
        observations = sources.observations(data, availability_hours=8)
        _require(all(o.season <= 2024 and o.effective_at.year <= 2024 for o in observations),
                 "Source loader admitted excluded observation years")
        eligible = [o for o in PointInTimeStore(observations).observations
                    if o.season <= 2022 and o.effective_at.year <= 2022 and o.available_at.year <= 2022]
        entries = [o.manifest_entry() for o in sorted(eligible, key=original._record_key)]
        summary = _input_summary(entries)
        previous.write_rows(out / "fit-inputs.jsonl.gz", entries)
        _write(out / "input-summary.json", summary)
        _write(out / "source-quality.json", data.quality)
        _check_deadline(deadline)
        recipe = original.fit(eligible, through_season=2022, shrinkage=1, variant="box")
        _check_deadline(deadline)
        _recipe_matches(recipe, summary)
        _write(out / "recipe.json", recipe)
        _require(shadow.load(out / "implementation.json") == _implementation(), "Implementation changed during reconstruction")
        _require(shadow.load(out / "environment.json") == _environment(), "Environment changed during reconstruction")
        _check_deadline(deadline)
        artifacts = {name: _hash(out / name) for name in sorted(REQUIRED_ARTIFACTS)}
        receipt = {**started, "status": "complete", "registration": reg,
                   "completed_at": shadow.stamp(_now()), "elapsed_seconds": perf_counter() - started_monotonic,
                   "artifacts": artifacts, "original_estimator_calls": 1, "new_estimator_settings": 0}
        _write(out / "receipt.json", receipt)
        return recipe, _provenance(receipt)
    except BaseException as error:
        if not (out / "receipt.json").exists():
            partial = {p.name: _hash(p) for p in sorted(out.iterdir()) if p.is_file() and p.name != "started.json"}
            _write(out / "receipt.json", {**started, "status": "stopped", "registration": reg,
                   "completed_at": shadow.stamp(_now()), "elapsed_seconds": perf_counter() - started_monotonic,
                   "error_type": type(error).__name__, "error": str(error), "artifacts": partial})
        raise


def validate_reconstruction(directory, *, expected_recipe=None, expected_registration=None):
    """Recheck saved reconstruction provenance without refitting or old seals."""
    root = shadow._safe_path(Path(directory))
    receipt = shadow.load(shadow._safe_path(root, "receipt.json"))
    started = shadow.load(shadow._safe_path(root, "started.json"))
    _require(receipt.get("schema") == SCHEMA and receipt.get("status") == "complete", "Completed reconstruction receipt required")
    _require(started.get("schema") == SCHEMA and started.get("status") == "started" and
             started.get("original_artifact_identity_verified") is False and
             re.fullmatch(r"[0-9a-f]{32}", str(receipt.get("execution_id", ""))) is not None and
             all(receipt.get(k) == started.get(k) for k in ("execution_id", "started_at", "budget_seconds")),
             "Reconstruction execution identity differs")
    start, end = shadow.instant(receipt.get("started_at")), shadow.instant(receipt.get("completed_at"))
    budget, elapsed = receipt.get("budget_seconds"), receipt.get("elapsed_seconds")
    _require(start <= end <= _now(), "Reconstruction clocks are future-dated or out of order")
    _require(type(budget) is int and 0 < budget <= MAX_SECONDS and not isinstance(elapsed, bool) and
             isinstance(elapsed, (int, float)) and math.isfinite(elapsed) and 0 <= elapsed <= budget,
             "Reconstruction exceeded its fixed budget")
    wall_seconds = (end - start).total_seconds()
    _require(wall_seconds <= budget + 1 and abs(wall_seconds - elapsed) <= 1,
             "Reconstruction wall and monotonic clocks disagree")
    _require(receipt.get("original_artifact_identity_verified") is False and
             receipt.get("original_estimator_calls") == 1 and receipt.get("new_estimator_settings") == 0,
             "Reconstruction cannot claim the missing original artifact or a new search")
    reg = receipt.get("registration", {})
    _require(reg == _registration(ROOT / REGISTRATION_PATH, reg.get("commit")), "Reconstruction registration differs")
    _require(expected_registration is None or reg == expected_registration, "Unexpected reconstruction registration")
    artifacts = receipt.get("artifacts", {})
    _require(set(artifacts) == REQUIRED_ARTIFACTS, "Missing or unexpected reconstruction artifacts")
    _require({p.name for p in root.iterdir()} == REQUIRED_ARTIFACTS | {"receipt.json", "started.json"},
             "Unlisted or missing reconstruction files")
    for name, digest in artifacts.items():
        _require(_hash(shadow._safe_path(root, name)) == digest, "Reconstruction artifact hash differs: " + name)
    raw_manifest = (root / "original-source-manifest.json").read_bytes()
    _require(shadow.digest(raw_manifest) == sources.SOURCE_MANIFEST_SHA256, "Original public source manifest changed")
    manifest = json.loads(raw_manifest)
    expected_source = dict(manifest, assets=previous.retained_assets(manifest))
    _require(shadow.load(root / "source-manifest.json") == expected_source, "Retained source selection differs")
    implementation = shadow.load(root / "implementation.json")
    _require(set(implementation.get("files", {})) == CODE_PATHS, "Missing reconstruction implementation dependencies")
    commit = implementation.get("commit", "")
    _require(re.fullmatch(r"[0-9a-f]{40}", commit), "Missing exact reconstruction code commit")
    for name, spec in implementation["files"].items():
        saved = _git("show", commit + ":" + name)
        _require(spec == {"sha256": shadow.digest(saved), "bytes": len(saved)} and
                 saved == shadow._safe_path(ROOT, name).read_bytes(), "Reconstruction implementation differs: " + name)
        _require(name not in PINNED_ENGINE or spec["sha256"] == PINNED_ENGINE[name], "Original engine pin differs")
    environment = shadow.load(root / "environment.json")
    _require(environment.get("dependencies") == DEPENDENCIES and
             str(environment.get("python", "")).startswith("3.12.") and
             isinstance(environment.get("numpy_configuration"), dict), "Unpinned reconstruction environment")
    summary = _input_summary(_read_inputs(root / "fit-inputs.jsonl.gz"))
    _require(summary == shadow.load(root / "input-summary.json"), "Inherited input summary differs")
    recipe = shadow.load(root / "recipe.json")
    _recipe_matches(recipe, summary)
    _require(expected_recipe is None or recipe == expected_recipe, "Reconstructed recipe differs from inherited recipe")
    return recipe, _provenance(receipt)


def verify_reconstruction_pair(primary_dir, reproduction_dir):
    """Require two actual saved executions with identical substantive artifacts."""
    _require(Path(primary_dir).resolve() != Path(reproduction_dir).resolve(), "Independent reconstruction directories required")
    left, right = validate_reconstruction(primary_dir), validate_reconstruction(reproduction_dir)
    a, b = shadow.load(Path(primary_dir) / "receipt.json"), shadow.load(Path(reproduction_dir) / "receipt.json")
    _require(a["execution_id"] != b["execution_id"], "Copied execution is not an independent reconstruction")
    _require(left == right, "Independent reconstructed recipe or substantive artifacts differ")
    return left
