"""Download the frozen, historical ESPN/wehoop inputs for this research.

Run from the repository root::

    python3 wnba/research/rich_context/sources.py

Only 2003--2025 box scores/schedules and 2010--2025 play-by-play are fetched.
The downloader deliberately cannot fetch 2026 or touch the live experiment.
Network and file handling use only Python's standard library. ``--qc`` needs
pandas and pyarrow, which the research requirements file declares.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import urllib.error
import urllib.request


SOURCE_REPOSITORY = "sportsdataverse/wehoop-wnba-data"
SOURCE_COMMIT = "db0d4921daa58aec06da2931eec713d9e9a52c6e"
BASE_URL = f"https://raw.githubusercontent.com/{SOURCE_REPOSITORY}/{SOURCE_COMMIT}/wnba"
DEFAULT_RAW = Path(__file__).resolve().parents[2] / "data" / "rich_context" / "raw"
SOURCE_KINDS = {
    "player_box": "player_box/parquet/player_box_{year}.parquet",
    "team_box": "team_box/parquet/team_box_{year}.parquet",
    "schedule": "schedules/parquet/wnba_schedule_{year}.parquet",
    "pbp": "pbp/parquet/play_by_play_{year}.parquet",
}
PBP_COLUMNS = [
    "game_id", "season", "season_type", "game_date", "game_date_time",
    "id", "sequence_number", "game_play_number", "type_id", "type_text", "text",
    "away_score", "home_score", "period_number", "clock_display_value",
    "scoring_play", "score_value", "team_id", "home_team_id", "away_team_id",
    "athlete_id_1", "athlete_id_2", "athlete_id_3", "athlete_name_1",
    "athlete_name_2", "athlete_name_3", "wallclock", "shooting_play",
    "coordinate_x_raw", "coordinate_y_raw", "coordinate_x", "coordinate_y",
    "points_attempted",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def requested_assets(start_year: int = 2003, end_year: int = 2025,
                     pbp_start_year: int = 2010) -> list[dict]:
    if not (2003 <= start_year <= end_year <= 2025):
        raise ValueError("This study permits historical seasons 2003--2025 only.")
    if not (2003 <= pbp_start_year <= end_year):
        raise ValueError("PBP start year must be between 2003 and the end year.")
    assets = []
    for kind, template in SOURCE_KINDS.items():
        first = max(start_year, pbp_start_year) if kind == "pbp" else start_year
        for year in range(first, end_year + 1):
            relative = template.format(year=year)
            assets.append({"kind": kind, "season": year,
                           "filename": Path(relative).name,
                           "url": f"{BASE_URL}/{relative}"})
    return assets


def _is_parquet(path: Path) -> bool:
    if path.stat().st_size < 12:
        return False
    with path.open("rb") as handle:
        if handle.read(4) != b"PAR1":
            return False
        handle.seek(-4, 2)
        return handle.read(4) == b"PAR1"


def fetch_asset(asset: dict, raw_dir: Path, prior: dict | None = None,
                attempts: int = 3, timeout: float = 30) -> dict:
    """Reuse only a checksum-verified file from the same frozen URL."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    target = raw_dir / asset["filename"]
    if (target.exists() and prior and prior.get("url") == asset["url"]
            and prior.get("sha256") == file_sha256(target) and _is_parquet(target)):
        return {**asset, "sha256": prior["sha256"], "bytes": target.stat().st_size}
    last_error = None
    for attempt in range(attempts):
        fd, temporary = tempfile.mkstemp(prefix=target.name + ".", dir=raw_dir)
        try:
            request = urllib.request.Request(
                asset["url"], headers={"User-Agent": "beating-the-opener-research/1.0"})
            with os.fdopen(fd, "wb") as output:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    while chunk := response.read(1024 * 1024):
                        output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            downloaded = Path(temporary)
            if not _is_parquet(downloaded):
                raise ValueError("Response is not a complete Parquet file")
            result = {**asset, "sha256": file_sha256(downloaded),
                      "bytes": downloaded.stat().st_size}
            os.replace(downloaded, target)
            return result
        except (OSError, ValueError, urllib.error.URLError) as error:
            last_error = error
            if attempt + 1 < attempts:
                time.sleep(0.5 * (attempt + 1))
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    raise RuntimeError(f"{asset['filename']}: {last_error}")


def download(raw_dir: Path = DEFAULT_RAW, start_year: int = 2003,
             end_year: int = 2025, pbp_start_year: int = 2010,
             workers: int = 6) -> dict:
    assets = requested_assets(start_year, end_year, pbp_start_year)
    raw_dir = Path(raw_dir)
    manifest_path = raw_dir / "manifest.json"
    prior = {}
    if manifest_path.exists():
        try:
            previous = json.loads(manifest_path.read_text())
            if previous.get("source_commit") == SOURCE_COMMIT:
                prior = {item["filename"]: item for item in previous.get("assets", [])}
        except (ValueError, KeyError, TypeError):
            # An unreadable manifest is not evidence of a valid local download.
            prior = {}
    completed, failures = [], []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(fetch_asset, asset, raw_dir,
                                   prior.get(asset["filename"])): asset
                   for asset in assets}
        for future in as_completed(futures):
            asset = futures[future]
            try:
                item = future.result()
                completed.append(item)
                print(f"OK {item['filename']} ({item['bytes']:,} bytes)", flush=True)
            except Exception as error:
                failures.append({"filename": asset["filename"], "error": str(error)})
                print(f"FAILED {asset['filename']}: {error}", flush=True)
    manifest = {
        "schema_version": 1, "source_repository": SOURCE_REPOSITORY,
        "source_commit": SOURCE_COMMIT, "recorded_at_utc": utc_now(),
        "complete": len(completed) == len(assets),
        "expected_files": len(assets), "downloaded_files": len(completed),
        "total_bytes": sum(item["bytes"] for item in completed),
        "assets": sorted(completed, key=lambda item: (item["kind"], item["season"])),
        "failures": failures,
        "availability_note": (
            "These are historical files retrieved after the games, not original "
            "publication-time snapshots. Later source corrections may be present. "
            "Research features must use completed prior games only; no 2026 files "
            "are included. ESPN athlete/game/team IDs are shared across these tables.")}
    atomic_json(manifest_path, manifest)
    if failures:
        raise RuntimeError(f"Incomplete source download: {len(failures)} failed files; "
                           f"see {manifest_path}")
    return manifest


def quality_report(raw_dir: Path = DEFAULT_RAW) -> dict:
    """Inventory observed coverage/schema without constructing model features."""
    import pandas as pd

    raw_dir = Path(raw_dir)
    manifest = json.loads((raw_dir / "manifest.json").read_text())
    if not manifest.get("complete") or manifest.get("source_commit") != SOURCE_COMMIT:
        raise ValueError("QC requires a complete manifest from the pinned source.")
    tables, summaries, schemas = {}, [], {}
    for item in manifest["assets"]:
        path = raw_dir / item["filename"]
        if file_sha256(path) != item["sha256"]:
            raise ValueError(f"Checksum changed: {path}")
        data = pd.read_parquet(path)
        kind, year = item["kind"], item["season"]
        schemas.setdefault(kind, {})[str(year)] = {
            column: str(dtype) for column, dtype in data.dtypes.items()}
        row = {"kind": kind, "season": year, "rows": len(data),
               "columns": len(data.columns), "bytes": item["bytes"]}
        game_col = "game_id" if "game_id" in data else "id" if kind == "schedule" else None
        if game_col:
            ids = set(pd.to_numeric(data[game_col], errors="coerce").dropna().astype(int))
            tables[kind, year] = ids
            row["games"] = len(ids)
        for date_col in ("game_date", "game_date_time", "date"):
            if date_col in data:
                dates = pd.to_datetime(data[date_col], errors="coerce", utc=True).dropna()
                if len(dates):
                    row["earliest_date"] = dates.min().isoformat()
                    row["latest_date"] = dates.max().isoformat()
                break
        if "season_type" in data:
            row["rows_by_season_type"] = {
                str(k): int(v) for k, v in data["season_type"].value_counts().items()}
        if kind == "player_box":
            row["duplicate_game_athlete_rows"] = int(
                data.duplicated(["game_id", "athlete_id"]).sum())
        if kind == "pbp":
            row["duplicate_game_event_rows"] = int(data.duplicated(["game_id", "id"]).sum())
            field_goal = data["shooting_play"].fillna(False) & ~data["type_text"].str.contains(
                "free throw", case=False, na=False)
            if "points_attempted" in data:
                field_goal &= data["points_attempted"].isin([2, 3])
            row["explicit_points_attempted_column"] = "points_attempted" in data
            shots = data.loc[field_goal]
            row["field_goal_attempt_events"] = len(shots)
            row["shots_with_actor_id"] = int(shots["athlete_id_1"].notna().sum())
            row["shots_with_assist_text"] = int(shots["text"].str.contains(
                r"assists|assisted by", case=False, na=False).sum())
            row["shots_with_distance_text"] = int(shots["text"].str.contains(
                r"\d+(?:\.\d+)?(?:-foot|\s+ft)", case=False, na=False).sum())
            row["shots_with_bounded_raw_coordinates"] = int((
                shots["coordinate_x_raw"].between(-5, 55)
                & shots["coordinate_y_raw"].between(-5, 100)).sum())
            row["shots_with_zero_coordinate_pair"] = int((
                shots["coordinate_x_raw"].eq(0) & shots["coordinate_y_raw"].eq(0)).sum())
            row["distinct_raw_shot_coordinate_pairs"] = int(shots[
                ["coordinate_x_raw", "coordinate_y_raw"]].drop_duplicates().shape[0])
            row["substitution_events"] = int(data["type_text"].eq("Substitution").sum())
            row["events_with_wallclock"] = int(data["wallclock"].notna().sum()) if "wallclock" in data else 0
        summaries.append(row)
    joins = []
    for year in sorted({year for _, year in tables}):
        boxes = tables.get(("player_box", year), set())
        pbp = tables.get(("pbp", year), set())
        if pbp:
            joins.append({"season": year, "box_games": len(boxes), "pbp_games": len(pbp),
                          "shared_games": len(boxes & pbp),
                          "box_games_without_pbp": sorted(boxes - pbp),
                          "pbp_games_without_boxes": sorted(pbp - boxes)})
    report = {
        "schema_version": 1, "source_commit": SOURCE_COMMIT,
        "tables": summaries, "game_id_joins": joins, "schemas": schemas,
        "warnings": [
            "Coordinates contain numeric missing-value sentinels. Non-null does not mean usable.",
            "points_attempted exists only in 2024--2025; earlier seasons require shot/free-throw type and text parsing. wallclock is absent in 2010--2012.",
            "Older descriptions use 'made', 'missed', '22 ft' and 'Assisted by'; newer descriptions use 'makes', 'misses', '22-foot' and 'assists'. Parse both.",
            "Only shooting_play=True with points_attempted=2 or 3 identifies field-goal attempts; shooting_play also includes free throws.",
            "For made assisted shots actor 1 is shooter and actor 2 is assister. On blocked attempts actor 2 is the blocker; require explicit assist text.",
            "Substitution actor 1 enters and actor 2 leaves. Full lineup reconstruction requires starters and reconciliation, not event counts alone.",
            "Do not model from game_spread, home_team_spread, home_favorite, lead_* or current-game outcomes.",
            "Player-box active/reason fields can disagree with recorded participation; they are not historical pregame injury reports.",
            "Historical availability is not established by file retrieval time; lag all game-derived features and disclose source revisions."]}
    atomic_json(raw_dir / "quality_report.json", report)
    return report


def load_sources(raw_dir: Path = DEFAULT_RAW, pbp_start_year: int = 2010,
                 verify: bool = True) -> tuple:
    """Return ``(player_boxes, team_boxes, play_by_play, schedules)`` DataFrames.

    Reads the manifest's explicit historical file list, never a wildcard that
    could later include protected data. PBP omits upstream betting lines and
    forward-looking convenience columns. Event outcomes remain necessary for
    historical aggregation, so the feature builder must still lag them.
    """
    import pandas as pd
    import pyarrow.parquet as pq

    raw_dir = Path(raw_dir)
    manifest = json.loads((raw_dir / "manifest.json").read_text())
    if not manifest.get("complete") or manifest.get("source_commit") != SOURCE_COMMIT:
        raise ValueError("Loading requires a complete manifest from the pinned source.")
    if not (2003 <= pbp_start_year <= 2025):
        raise ValueError("PBP loading is restricted to 2003--2025.")
    collected = {kind: [] for kind in SOURCE_KINDS}
    for item in manifest["assets"]:
        kind, year = item["kind"], item["season"]
        if year > 2025 or year < 2003 or kind not in collected:
            raise ValueError("Unexpected source asset outside the registered historical scope.")
        if kind == "pbp" and year < pbp_start_year:
            continue
        path = raw_dir / item["filename"]
        if verify and file_sha256(path) != item["sha256"]:
            raise ValueError(f"Checksum changed: {path}")
        columns = None
        if kind == "pbp":
            available = set(pq.read_schema(path).names)
            columns = [name for name in PBP_COLUMNS if name in available]
        collected[kind].append(pd.read_parquet(path, columns=columns))
    return tuple(pd.concat(collected[kind], ignore_index=True)
                 for kind in ("player_box", "team_box", "pbp", "schedule"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--start-year", type=int, default=2003)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument("--pbp-start-year", type=int, default=2010)
    parser.add_argument("--workers", type=int, choices=range(1, 9), default=6)
    parser.add_argument("--qc", action="store_true", help="Verify checksums and inventory table coverage.")
    parser.add_argument("--qc-only", action="store_true")
    args = parser.parse_args()
    if not args.qc_only:
        result = download(args.raw_dir, args.start_year, args.end_year,
                          args.pbp_start_year, args.workers)
        print(f"Complete: {result['downloaded_files']} files, {result['total_bytes']:,} bytes")
    if args.qc or args.qc_only:
        report = quality_report(args.raw_dir)
        print(f"QC complete: {len(report['tables'])} tables; {args.raw_dir / 'quality_report.json'}")


if __name__ == "__main__":
    main()
