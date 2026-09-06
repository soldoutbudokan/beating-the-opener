"""Reproduce the report's 2025 PBP quality counts without fitting or scoring.

Run from the repository root:
    python -m wnba.research.rich_context.tools.check_feature_quality

Reads only the four pinned 2025 source files and writes a separate quality
receipt. It does not load predictions, market prices, models, or 2026 data.
"""
from pathlib import Path
import json

import pandas as pd

from wnba.research.rich_context.features import build_state_tables
from wnba.research.rich_context.sources import (
    DEFAULT_RAW, SOURCE_COMMIT, SOURCE_REPOSITORY, atomic_json, file_sha256,
    utc_now,
)


def main():
    study = Path(__file__).resolve().parents[1]
    manifest = json.loads((DEFAULT_RAW / "manifest.json").read_text())
    names = ["player_box_2025.parquet", "team_box_2025.parquet",
             "play_by_play_2025.parquet", "wnba_schedule_2025.parquet"]
    assets = {entry["filename"]: entry for entry in manifest["assets"]}
    verified, frames = [], []
    for name in names:
        asset = assets[name]
        expected_url = f"/{SOURCE_COMMIT}/"
        if expected_url not in asset["url"]:
            raise ValueError(f"Source is not pinned to the study commit: {name}")
        path = DEFAULT_RAW / name
        digest = file_sha256(path)
        if digest != asset["sha256"]:
            raise ValueError(f"Source checksum mismatch: {name}")
        verified.append({"filename": name, "sha256": digest, "url": asset["url"]})
        frames.append(pd.read_parquet(path))
    states = build_state_tables(*frames, availability_hours=8)
    receipt = {
        "schema_version": 1,
        "created_at_utc": utc_now(),
        "purpose": "Post-report, quality-only recomputation of the reported 2025 source reconciliation counts.",
        "scope": "Completed 2025 games; the schedule's All-Star game is excluded.",
        "model_fit_performed": False,
        "forecast_generation_performed": False,
        "forecast_evaluation_performed": False,
        "existing_results_modified": False,
        "explanation": "These counts measure agreement between archived play-by-play and box scores. They do not rerun or change the 2025 prediction test, model selection, forecasts, or profitability results.",
        "source_repository": SOURCE_REPOSITORY,
        "source_commit": SOURCE_COMMIT,
        "verified_assets": verified,
        "feature_code_sha256": file_sha256(study / "features.py"),
        "reproducer": "python -m wnba.research.rich_context.tools.check_feature_quality",
        "reproducer_sha256": file_sha256(Path(__file__)),
        "coverage": states.coverage,
        "reconciliation_rule": "A game passes a feature family only when every player count agrees with the box score. Shots compare FGA, FGM, 3PA and 3PM; assists compare credited assists; rebounds compare offensive and defensive rebounds. A failed family is missing for that game, rather than set to zero.",
    }
    output = study / "results" / "feature_quality_2025.json"
    atomic_json(output, receipt)
    print(json.dumps({"output": str(output), "reconciliation": states.coverage["reconciliation"]}, indent=2))


if __name__ == "__main__":
    main()
