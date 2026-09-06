"""Reproduce the frozen study in separate results/cache directories.

This wrapper is outside the registered model-code hash. It changes output
locations only; the model, source loader, and evaluation rules stay frozen.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

STUDY = Path(__file__).resolve().parents[1]
REPO = STUDY.parents[2]
DATA = STUDY.parents[1] / "data" / "rich_context"


def output_paths(value):
    """Reject destinations that could overlap the study or original inputs."""
    root = Path(value).expanduser().resolve()
    protected = [STUDY, DATA / "raw", DATA / "cache"]
    for original in protected:
        original = original.resolve()
        if root == original or root in original.parents or original in root.parents:
            raise ValueError(f"Output must be separate from {original}")
    if root.exists() and not root.is_dir():
        raise ValueError("Output path is not a directory")
    results, cache = root / "results", root / "cache"
    # Resolving descendants also catches an existing results/cache symlink.
    for destination in (results, cache):
        resolved = destination.resolve()
        if root not in resolved.parents:
            raise ValueError(f"Output child escapes its directory: {destination}")
    return root, results, cache


def asset_records(manifest):
    fields = ("filename", "kind", "season", "url", "sha256", "bytes")
    return sorted(tuple(item[field] for field in fields)
                  for item in manifest["assets"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "fit", "evaluate"))
    parser.add_argument("--output-dir", required=True, type=Path,
                        help="New directory containing separate results/ and cache/ folders")
    parser.add_argument("--check", action="store_true",
                        help="Check paths, frozen code, and source fingerprints without running or writing")
    args = parser.parse_args(argv)
    try:
        root, results, cache = output_paths(args.output_dir)
        sys.path.insert(0, str(REPO))
        from wnba.research.rich_context import run, sources

        original_receipt = json.loads((STUDY / "results" / "evaluation_receipt.json").read_text())
        implementation = run.code_hash()
        if implementation != original_receipt["implementation_sha256"]:
            raise ValueError("Model code differs from the registered run; use its published commit to reproduce")

        published = json.loads((STUDY / "results" / "source_manifest.json").read_text())
        manifest_path = sources.DEFAULT_RAW / "manifest.json"
        downloaded = json.loads(manifest_path.read_text())
        if (not downloaded.get("complete")
                or downloaded.get("source_commit") != published.get("source_commit")
                or asset_records(downloaded) != asset_records(published)):
            raise ValueError("Downloaded inputs do not match the published source fingerprints")
        for asset in downloaded["assets"]:
            path = sources.DEFAULT_RAW / asset["filename"]
            if sources.file_sha256(path) != asset["sha256"]:
                raise ValueError(f"Downloaded file changed: {path}")
        metadata = {
            "implementation_sha256": implementation,
            "source_manifest_sha256": sources.file_sha256(manifest_path),
            "registered_receipt_sha256": sources.file_sha256(STUDY / "results" / "evaluation_receipt.json"),
            "purpose": "Reproduction of the published 2025 development comparison; not a new holdout test",
        }
        marker = root / "reproduction.json"
        if args.phase == "prepare":
            if root.exists() and any(root.iterdir()):
                raise ValueError("Prepare requires a new or empty output directory; retain earlier reproductions")
        else:
            if not marker.exists() or json.loads(marker.read_text()) != metadata:
                raise ValueError("Prepare this output directory first; its code or source manifest may have changed")
            required = cache / ("prepared.pkl" if args.phase == "fit" else "fitted.pkl")
            if not required.is_file():
                raise ValueError(f"Missing preceding phase output: {required}")
            if args.phase == "fit" and (cache / "fitted.pkl").exists():
                raise ValueError("This directory is already fitted; retain it and choose a new output directory")
            if args.phase == "evaluate" and (results / "evaluation_receipt.json").exists():
                raise ValueError("This reproduction already has an evaluation receipt; retain it")
        if args.check:
            print(f"Checks passed. Results: {results}; cache: {cache}. No files written.")
            return 0

        run.RESULTS = results
        if args.phase == "prepare":
            results.mkdir(parents=True)
            cache.mkdir()
            run.save_json(marker, metadata)
            run.save_json(results / "source_manifest.json", downloaded)
        getattr(run, args.phase)(cache_dir=cache)
        print(f"Reproduction {args.phase} complete: {root}")
        return 0
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        parser.exit(2, f"Reproduction stopped: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
