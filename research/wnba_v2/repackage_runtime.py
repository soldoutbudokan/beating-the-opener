"""Replace only a frozen candidate's storage implementation, without fitting.

The original archive and receipt remain separate evidence. The replacement has
a new freeze clock and implementation identity, but copies the selected recipe,
seed and source audit bytes exactly. This command neither publishes nor starts
a prospective study.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import gzip
import io
import json
from pathlib import Path
import stat
import zipfile

from . import future, runtime, shadow
from .prepare_candidate import implementation_manifest, metadata


AMENDMENT = "research/experiments/2026-09-wnba-v2-storage-verification.md"
AMENDMENT_COMMIT = "76c7ff6f631ca8879cdbf9620cc633fc63971a6a"
SELF = "research/wnba_v2/repackage_runtime.py"
ALLOWED_CHANGES = {"research/wnba_v2/shadow.py", SELF, AMENDMENT}
ORIGINAL_FILES = {"recipe.json", "seed.jsonl.gz", "seed-gate.json", "seed-quality.json",
                  "source-manifest.json", "selection.json"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_regular(path):
    path = Path(path)
    shadow._safe_path(path.parent, path.name)
    require(stat.S_ISREG(path.stat().st_mode), "Evidence must be a regular file")
    return path.read_bytes()


def checked_archive(raw, expected_hash, *, manifest_hash=None):
    """Check the complete archive before returning any member for reuse."""
    require(len(raw) <= runtime.MAX_BUNDLE_BYTES and shadow.digest(raw) == expected_hash,
            "Original bundle hash or size differs")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            names = [runtime.safe_relative(item.filename) for item in entries]
            require(len(names) == len(set(names)) and "MANIFEST.json" in names,
                    "Duplicate or missing bundle members")
            require(not any(item.is_dir() or item.flag_bits & 1
                            or stat.S_IFMT(item.external_attr >> 16) not in (0, stat.S_IFREG)
                            or item.file_size > runtime.MAX_MEMBER_BYTES for item in entries)
                    and sum(item.file_size for item in entries) <= runtime.MAX_BUNDLE_BYTES,
                    "Invalid bundle member type or size")
            manifest_raw = archive.read("MANIFEST.json")
            if manifest_hash is not None:
                require(shadow.digest(manifest_raw) == manifest_hash, "Original manifest hash differs")
            manifest = runtime.document(manifest_raw)
            files = manifest.get("files")
            require(manifest.get("schema") == "wnba-shadow-bundle-v1" and isinstance(files, dict)
                    and set(names) == {"MANIFEST.json", *files}, "Bundle manifest members differ")
            contents = {"MANIFEST.json": manifest_raw}
            for name, expected in files.items():
                content = archive.read(runtime.safe_relative(name))
                require(metadata(content) == expected, "Bundle member hash or size differs")
                contents[name] = content
            return manifest, contents
    except (zipfile.BadZipFile, KeyError) as error:
        raise ValueError("Invalid bundle archive") from error


def committed_implementations(repo, commit):
    """Enumerate every originally bound implementation from the Git tree."""
    entries = {}
    for row in runtime.git(repo, "ls-tree", "-r", "-z", commit).stdout.split(b"\0"):
        if not row:
            continue
        info, name = row.split(b"\t", 1)
        mode, kind, _ = info.decode().split()
        name = name.decode()
        path = Path(name)
        included = (name in runtime.REQUIRED_CODE
                    or name.startswith("research/") and name.endswith(".py") and "work" not in path.parts
                    or path.parent.as_posix() == "research/experiments"
                    and ("wnba-v2" in path.name and path.suffix == ".md"
                         or path.name == "2026-09-wnba-participation-v2.md"))
        if included:
            runtime.safe_relative(name)
            require(mode == "100644" and kind == "blob", "Implementation must be a regular Git blob")
            entries[name] = metadata(runtime.git(repo, "show", commit + ":" + name).stdout)
    require(runtime.REQUIRED_CODE <= entries.keys(), "Pinned implementation is incomplete")
    return entries


def checked_code(repo, old_commit, new_commit, old_manifest, amendment_commit):
    for value in (old_commit, new_commit, amendment_commit):
        require(isinstance(value, str) and runtime.SHA1.fullmatch(value), "Exact pinned commits are required")
    require(amendment_commit == AMENDMENT_COMMIT, "Unrecognized runtime storage amendment")
    require(runtime.git(repo, "rev-parse", "HEAD").stdout.decode().strip() == new_commit,
            "Implementation checkout differs from the replacement commit")
    require(runtime.git(repo, "merge-base", "--is-ancestor", old_commit, new_commit, check=False).returncode == 0,
            "Replacement must descend from the original implementation")
    require(runtime.git(repo, "merge-base", "--is-ancestor", amendment_commit, new_commit, check=False).returncode == 0,
            "Runtime amendment is not in the replacement history")
    old = committed_implementations(repo, old_commit)
    require(old_manifest.get("implementation_files") == old
            and old_manifest.get("implementation_sha256") == shadow.digest(shadow.encode(old)),
            "Original implementation does not match its pinned Git bytes")
    new = committed_implementations(repo, new_commit)
    require(implementation_manifest(repo, new_commit) == new, "Replacement implementation is dirty or incomplete")
    changed = sorted(name for name in old.keys() | new.keys() if old.get(name) != new.get(name))
    require(set(changed) == ALLOWED_CHANGES and not (old.keys() - new.keys()),
            "Replacement changes exceed the registered storage repair")
    amendment_raw = runtime.git(repo, "show", amendment_commit + ":" + AMENDMENT).stdout
    require(metadata(amendment_raw) == new[AMENDMENT], "Runtime amendment bytes differ from its pinned commit")
    return new, changed, {"path": AMENDMENT, "commit": amendment_commit,
                          "sha256": shadow.digest(amendment_raw)}


def checked_original(bundle, receipt_path, expectation_path, *, receipt_sha256, manifest_sha256, old_code_commit, now):
    for value in (receipt_sha256, manifest_sha256):
        shadow.sha(value, "original evidence hash")
    receipt_raw, expectation_raw = read_regular(receipt_path), read_regular(expectation_path)
    require(shadow.digest(receipt_raw) == receipt_sha256, "Original receipt hash differs")
    receipt, expectation = runtime.document(receipt_raw), runtime.document(expectation_raw)
    require(receipt.get("schema") == "wnba-v2-candidate-bundle-receipt-v1"
            and receipt.get("code_commit") == old_code_commit and receipt.get("active") is False
            and receipt.get("qualified") is False, "Original candidate receipt is invalid")
    require(expectation.get("schema") == "wnba-v2-reconstruction-expectation-v1"
            and expectation.get("source") == "prior_conversation_record"
            and expectation.get("original_artifact_identity_verified") is False,
            "Saved reconstruction expectation is required")
    manifest, contents = checked_archive(read_regular(bundle), receipt.get("bundle_sha256"), manifest_hash=manifest_sha256)
    require(set(contents) == {"MANIFEST.json", *ORIGINAL_FILES}, "Original candidate contents differ")
    require(manifest.get("recipe_file") == "recipe.json" and manifest.get("seed_file") == "seed.jsonl.gz",
            "Original model input paths differ")
    for key in ("candidate_id", "frozen_at", "implementation_sha256", "model_recipe_hash"):
        require(manifest.get(key) == receipt.get(key), "Original manifest and receipt disagree")
    shadow.identifier(receipt.get("candidate_id"), "original candidate")
    frozen, created = shadow.instant(receipt.get("frozen_at")), shadow.instant(receipt.get("created_at"))
    require(frozen <= created <= now and shadow.instant(expectation.get("recorded_before_execution_at")) < frozen,
            "Original candidate clocks are invalid")
    selection, recipe = runtime.document(contents["selection.json"]), runtime.document(contents["recipe.json"])
    require(selection.get("schema") == "wnba-v2-candidate-selection-v1"
            and all(selection.get(key) == receipt.get(key) for key in ("candidate_id", "frozen_at", "code_commit"))
            and selection.get("qualified") is False and selection.get("original_artifact_identity_verified") is False
            and "runtime_repack" not in selection, "Original selection identity or provenance differs")
    require(selection.get("comparison_artifacts", {}).get("expectation.json") == shadow.digest(expectation_raw),
            "Expectation differs from the original comparison binding")
    decision = selection.get("selection", {})
    require(decision.get("status") == "RESEARCH_ONLY" and decision.get("market_advantage_established") is False
            and decision.get("selected_variance_mode") == recipe.get("variance_mode") == expectation.get("variance_mode")
            and recipe.get("recipe_hash") == receipt.get("model_recipe_hash") == expectation.get("model_recipe_hash"),
            "Original recipe differs from the recorded candidate choice")
    for name, key in (("recipe.json", "recipe_sha256"), ("seed.jsonl.gz", "seed_sha256")):
        require(shadow.digest(contents[name]) == receipt.get(key), "Original model input hash differs")
    require(runtime.document(contents["seed-gate.json"]).get("status") == "PASS_UNDER_ASSUMPTION",
            "Original seed source gate did not pass")
    return receipt, receipt_raw, manifest, contents, selection, recipe, frozen, expectation_raw


def write_verified(path, raw):
    shadow.write_once(path, raw)
    require(read_regular(path) == raw, "Replacement evidence failed its stored-byte verification")


def repackage(bundle, receipt, expectation, output, code_repo, old_code_commit, code_commit, amendment_commit,
              *, receipt_sha256, manifest_sha256, clock=shadow.utcnow):
    frozen = clock()
    shadow.stamp(frozen)
    checked = checked_original(bundle, receipt, expectation, receipt_sha256=receipt_sha256,
                               manifest_sha256=manifest_sha256, old_code_commit=old_code_commit, now=frozen)
    old_receipt, receipt_raw, old_manifest, original, old_selection, recipe, old_freeze, expectation_raw = checked
    require(frozen > old_freeze, "Replacement freeze must follow the original freeze")
    implementations, changed, amendment = checked_code(code_repo, old_code_commit, code_commit, old_manifest, amendment_commit)
    # Only an already frozen seed is read. Raw sources, calibration rows and
    # outcome scores are deliberately absent from this command's interface.
    with gzip.open(io.BytesIO(original["seed.jsonl.gz"]), "rt") as stream:
        seed = tuple(future.observation_from_record(json.loads(line)) for line in stream if line.strip())
    require(seed and all(row.season <= 2025 for row in seed), "Replacement seed includes post-2025 observations")
    seed_details = {"seed_observations": len(seed), "seed_max_season": max(row.season for row in seed),
                    "last_seed_event_at": shadow.stamp(max(row.effective_at for row in seed))}
    require(all(old_receipt.get(k) == v for k, v in seed_details.items()), "Original seed summary differs")
    future.FutureOnlyModel(seed, recipe, frozen_at=old_freeze)
    future.FutureOnlyModel(seed, recipe, frozen_at=frozen)
    candidate_id = "wnba-v2-points-" + recipe["variance_mode"] + "-" + frozen.strftime("%Y%m%dT%H%M%S%fZ")
    proof = {"schema": "wnba-v2-runtime-repack-v1", "original_candidate_id": old_receipt["candidate_id"],
             "original_frozen_at": old_receipt["frozen_at"], "original_bundle_sha256": old_receipt["bundle_sha256"],
             "original_manifest_sha256": manifest_sha256, "original_receipt_sha256": receipt_sha256,
             "original_selection_sha256": shadow.digest(original["selection.json"]),
             "expectation_sha256": shadow.digest(expectation_raw), "original_code_commit": old_code_commit,
             "replacement_code_commit": code_commit, "changed_implementation_files": changed,
             "amendment": amendment, "recipe_bytes_preserved": True, "seed_bytes_preserved": True,
             "source_audit_bytes_preserved": True, "parameter_refits": 0, "model_reselections": 0,
             "raw_sources_reopened": False, "original_artifact_identity_verified": False}
    selection = deepcopy(old_selection)
    selection.update(candidate_id=candidate_id, frozen_at=shadow.stamp(frozen), code_commit=code_commit,
                     runtime_repack=proof)
    contents = {name: raw for name, raw in original.items() if name not in {"MANIFEST.json", "selection.json"}}
    contents.update({"selection.json": shadow.encode(selection), "original-selection.json": original["selection.json"],
                     "original-MANIFEST.json": original["MANIFEST.json"], "original-bundle-receipt.json": receipt_raw,
                     "reconstruction-expectation.json": expectation_raw})
    manifest = deepcopy(old_manifest)
    manifest.update(candidate_id=candidate_id, frozen_at=shadow.stamp(frozen), implementation_files=implementations,
                    implementation_sha256=shadow.digest(shadow.encode(implementations)),
                    files={name: metadata(raw) for name, raw in sorted(contents.items())})
    contents["MANIFEST.json"] = shadow.encode(manifest)
    out = Path(output)
    shadow._safe_path(out.parent, out.name)
    out.mkdir(parents=True, exist_ok=False)
    for name, raw in sorted(contents.items()):
        write_verified(shadow._safe_path(out, name), raw)
    require(implementation_manifest(code_repo, code_commit) == implementations,
            "Implementation changed during runtime replacement")
    archive = out / "candidate.zip"
    with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED, compresslevel=6) as zipped:
        for name in ["MANIFEST.json", *sorted(manifest["files"])]:
            # Recheck immediately before packaging to catch a later local edit.
            raw = read_regular(out / name)
            require(raw == contents[name], "Stored replacement member changed before packaging")
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            zipped.writestr(info, raw)
    archived = read_regular(archive)
    archive_hash = shadow.digest(archived)
    checked_manifest, checked_contents = checked_archive(archived, archive_hash,
                                                        manifest_hash=shadow.digest(contents["MANIFEST.json"]))
    require(checked_manifest == manifest and checked_contents == contents,
            "Replacement archive does not reproduce its verified files")
    for name, raw in contents.items():
        require(read_regular(out / name) == raw, "Stored replacement member changed after packaging")
    completed = clock()
    require(completed >= frozen, "Replacement completion clock moved backwards")
    result = {"schema": "wnba-v2-candidate-bundle-receipt-v1", "candidate_id": candidate_id,
              "created_at": shadow.stamp(completed), "frozen_at": shadow.stamp(frozen), "code_commit": code_commit,
              "model_recipe_hash": recipe["recipe_hash"], "implementation_sha256": manifest["implementation_sha256"],
              "bundle_sha256": archive_hash, "recipe_sha256": old_receipt["recipe_sha256"],
              "seed_sha256": old_receipt["seed_sha256"], **seed_details, "durable_seal": None,
              "active": False, "qualified": False, "runtime_repack": proof}
    write_verified(out / "bundle-receipt.json", shadow.encode(result))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("bundle", "receipt", "expectation", "output", "code-repo"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("old-code-commit", "code-commit", "amendment-commit", "receipt-sha256", "manifest-sha256"):
        parser.add_argument("--" + name, required=True)
    args = vars(parser.parse_args())
    print(json.dumps(repackage(**args), sort_keys=True))


if __name__ == "__main__":
    main()
