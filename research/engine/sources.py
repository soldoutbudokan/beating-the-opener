"""Pinned historical inputs, explicit availability assumptions, and source pilots.

The parquet files contain corrected historical records, not publication logs.
Downloading them today never establishes historical availability. No function
in this module accepts a season later than 2025 or reads a live directory.
"""
from dataclasses import dataclass, field
from datetime import timedelta
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from research.clocks import parse, schedule_tip
from .store import Observation, ObservationKind, TimeBasis, payload_digest

SOURCE_COMMIT = "db0d4921daa58aec06da2931eec713d9e9a52c6e"
SOURCE_MANIFEST_SHA256 = "1fedab483827cca1d4887dce270a63e124fe4d4edf3c1786adcff7104025a132"
PR2_COMMIT = "64acad8248ef6536ef7c655d733293f6763263cc"
FROZEN_HASHES = {
    "8h": "37f05608b87742f5194c009fde8dbfcfd91a5f127fe970e54c34228fe1fa35e0",
    "24h": "d19b505009cf6016179b2326f8ddccbcaaa58ef1381180730470b7886f05e778",
}
INPUTS = Path(__file__).resolve().parents[1] / "structural" / "inputs"
COUNTS = {"points": "points", "rebounds": "rebounds", "assists": "assists",
          "threes": "three_point_field_goals_made"}
KINDS = {"player_box", "team_box", "schedule", "pbp"}
PBP_COLUMNS = ("game_id", "season", "season_type", "game_date", "game_date_time",
    "id", "sequence_number", "game_play_number", "type_id", "type_text", "text",
    "away_score", "home_score", "period_number", "clock_display_value",
    "scoring_play", "score_value", "team_id", "home_team_id", "away_team_id",
    "athlete_id_1", "athlete_id_2", "athlete_id_3", "athlete_name_1",
    "athlete_name_2", "athlete_name_3", "wallclock", "shooting_play", "points_attempted")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _id(value):
    if pd.isna(value):
        return None
    return str(value).removesuffix(".0")


def _normalise(frame):
    out = frame.copy()
    for c in ("game_id", "athlete_id", "team_id", "opponent_team_id",
              "home_team_id", "away_team_id", "athlete_id_1", "athlete_id_2",
              "athlete_id_3"):
        if c in out:
            out[c] = out[c].map(_id)
    return out


@dataclass
class HistoricalSources:
    player_box: pd.DataFrame
    team_box: pd.DataFrame
    pbp: pd.DataFrame
    schedule: pd.DataFrame
    manifest: dict
    quality: dict = field(default_factory=dict)

    @property
    def box(self):
        return self.player_box

    @property
    def team(self):
        return self.team_box


def verify_manifest(raw_dir, manifest_path=None):
    """Verify bytes before parsing, and reject protected/out-of-contract files."""
    root = Path(raw_dir)
    path = Path(manifest_path) if manifest_path else root / "manifest.json"
    manifest = json.loads(path.read_text())
    if manifest.get("source_commit") != SOURCE_COMMIT or not manifest.get("complete"):
        raise ValueError("A complete manifest from the pinned source is required")
    assets = manifest.get("assets", [])
    expected = {(k, y) for k in KINDS for y in range(2010 if k == "pbp" else 2003, 2026)}
    actual = []
    for a in assets:
        kind, season = a.get("kind"), a.get("season")
        if kind not in KINDS or type(season) is not int or not 2003 <= season <= 2025:
            raise ValueError("Source outside registered historical scope")
        name = a.get("filename", "")
        if not name or Path(name).name != name or not name.endswith(".parquet"):
            raise ValueError("Manifest filenames must be local parquet basenames")
        if f"/{SOURCE_COMMIT}/wnba/" not in a.get("url", ""):
            raise ValueError("Source URL is not pinned")
        source = root / name
        if source.is_symlink() or not source.is_file():
            raise ValueError(f"Missing or symlink source: {name}")
        if source.stat().st_size != a["bytes"] or digest(source) != a["sha256"]:
            raise ValueError(f"Source checksum changed: {name}")
        actual.append((kind, season))
    if len(actual) != 85 or len(set(actual)) != 85 or set(actual) != expected:
        raise ValueError("Expected exactly the frozen 85 historical assets")
    if digest(path) != SOURCE_MANIFEST_SHA256:
        raise ValueError("Frozen original source manifest changed; record retrieval separately")
    return manifest


def load_historical(raw_dir, manifest_path=None):
    manifest = verify_manifest(raw_dir, manifest_path)
    tables = {kind: [] for kind in KINDS}
    for a in manifest["assets"]:
        path = Path(raw_dir) / a["filename"]
        columns = None
        if a["kind"] == "pbp":
            import pyarrow.parquet as pq
            present = set(pq.read_schema(path).names)
            columns = [c for c in PBP_COLUMNS if c in present]
        frame = pd.read_parquet(path, columns=columns)
        if "season" in frame:
            years = pd.to_numeric(frame.season, errors="raise")
            if not years.eq(a["season"]).all() or years.gt(2025).any():
                raise ValueError(f"Source row season disagrees: {a['filename']}")
        tables[a["kind"]].append(_normalise(frame))
    tables = {k: pd.concat(v, ignore_index=True) for k, v in tables.items()}
    schedule = tables["schedule"]
    if schedule.game_id.duplicated().any():
        raise ValueError("Ambiguous schedule game identity")
    schedule["tip_at"] = [schedule_tip(d, e) for d, e in zip(schedule.date, schedule.game_date_time)]
    return HistoricalSources(tables["player_box"], tables["team_box"], tables["pbp"],
                             schedule, manifest, {"verified_source_files": 85})


def select_pilot_sample(schedule):
    """Call with schedule IDs only, and save this result before opening values."""
    rows = []
    for year in (2015, 2020, 2024):
        season = schedule.loc[schedule.season.eq(year), "game_id"].map(_id)
        if season.isna().any() or season.duplicated().any() or len(season) < 20:
            raise ValueError(f"Incomplete/ambiguous sampling schedule for {year}")
        ids = sorted(season, key=lambda g: hashlib.sha256(f"structural-pilot-v1:{g}".encode()).hexdigest())
        rows.extend({"season": year, "game_id": g} for g in ids[:20])
    return rows


def box_pilot(data, sample):
    """Retain every requested game for both clock and box families."""
    if len(sample) != 60 or len({(s["season"], s["game_id"]) for s in sample}) != 60:
        raise ValueError("Pilot requires the registered 60 distinct requests")
    rows = []
    for q in sample:
        gid = _id(q["game_id"])
        s = data.schedule[data.schedule.game_id.map(_id).eq(gid)]
        p = data.player_box[data.player_box.game_id.map(_id).eq(gid)]
        t = data.team_box[data.team_box.game_id.map(_id).eq(gid)]
        try:
            clock = len(s) == 1 and schedule_tip(s.iloc[0]["date"], s.iloc[0].game_date_time) is not None
        except (ValueError, TypeError):
            clock = False
        rows.append({**q, "family": "schedule_clocks", "matched": bool(clock),
                     "reason": "" if clock else "missing_or_conflicting_clock"})
        valid = False
        if len(p) and len(t):
            teams = set(t.team_id.map(_id))
            played = p.minutes.fillna(0).gt(0)
            counts = p.loc[played, list(COUNTS.values())].apply(pd.to_numeric, errors="coerce")
            tf = t[["field_goals_attempted", "free_throws_attempted", "offensive_rebounds",
                    "total_turnovers"]].apply(pd.to_numeric, errors="coerce")
            valid = (len(t) == 2 and len(teams) == 2 and None not in teams and
                     set(p.team_id.map(_id)) == teams and set(p.opponent_team_id.map(_id)) == teams and
                     not p.duplicated(["game_id", "athlete_id"]).any() and
                     not t.duplicated(["game_id", "team_id"]).any() and
                     p.minutes.fillna(0).between(0, 60).all() and
                     (p.minutes.notna() | p.did_not_play.fillna(False)).all() and
                     np.isfinite(counts).all().all() and (counts >= 0).all().all() and
                     (counts == np.floor(counts)).all().all() and
                     np.isfinite(tf).all().all() and (tf >= 0).all().all())
        rows.append({**q, "family": "box_identity_counts", "matched": bool(valid),
                     "reason": "" if valid else "missing_invalid_identity_minutes_or_counts",
                     "player_rows": len(p), "team_rows": len(t)})
    families = {}
    for family, floor in (("schedule_clocks", 1.), ("box_identity_counts", .98)):
        selected = [r for r in rows if r["family"] == family]
        usable = sum(r["matched"] for r in selected)
        families[family] = {"requested": len(selected), "usable": usable,
                            "coverage": usable / len(selected), "floor": floor,
                            "passed": usable / len(selected) >= floor}
    return {"status": "PASS_UNDER_ASSUMPTION" if all(v["passed"] for v in families.values()) else "FAIL",
            "time_basis": "assumed", "families": families, "rows": rows}


def game_clocks(data, availability_hours=8):
    if availability_hours not in (8, 24):
        raise ValueError("Only the registered 8h and 24h assumptions are supported")
    clocks = {}
    excluded = []
    for row in data.schedule.to_dict("records"):
        # All-Star games are season_type=2 in ESPN; that field alone is unsafe.
        abbreviation = str(row.get("type_abbreviation", "")).upper()
        if row.get("season_type") not in (2, 3) or abbreviation in ("ALLSTAR", "EXHIBITION", "EXH", "PRE"):
            excluded.append(_id(row["game_id"]))
            continue
        tip = schedule_tip(row["date"], row["game_date_time"])
        available = tip + timedelta(hours=availability_hours)
        if row.get("completed_at") is not None and pd.notna(row.get("completed_at")):
            available = max(available, parse("wehoop.completed_at", row["completed_at"]) + timedelta(hours=1))
        clocks[_id(row["game_id"])] = (tip, available, int(row["season"]))
    if "wallclock" in data.pbp:
        for gid, values in data.pbp.groupby("game_id", sort=False).wallclock:
            if _id(gid) not in clocks:
                continue
            instants = [parse("wehoop.wallclock", v) for v in values.dropna().unique() if str(v).strip()]
            if instants:
                tip, available, season = clocks[_id(gid)]
                clocks[_id(gid)] = (tip, max(available, max(instants) + timedelta(hours=1)), season)
    data.quality["excluded_noncompetitive_game_ids"] = sorted(excluded)
    return clocks


def observations(data, availability_hours=8):
    """Make market-free historical records with every dependency available.

    Possessions are the explicitly labeled box estimate FGA + .44 FTA - ORB +
    total turnovers. They are not reconstructed PBP possessions. Missing/invalid
    performance records and conflicting player identities are counted and omitted.
    """
    clocks = game_clocks(data, availability_hours)
    retrieved = parse("source.retrieved_at", data.manifest["recorded_at_utc"])
    source = "wehoop:" + SOURCE_COMMIT
    note = "Corrected historical archive; availability assumed as max(tip delay, last play+1h, reliable completion+1h)."
    result, teams = [], {}
    skipped = {"player_conflicting_identity": 0, "player_invalid_values": 0, "team_invalid_values": 0,
               "missing_schedule": 0, "noncompetitive_game_rows": 0}
    excluded = set(data.quality.get("excluded_noncompetitive_game_ids", []))
    periods = {_id(r["game_id"]): r.get("status_period") for r in data.schedule.to_dict("records")}
    duration_fallback = _normalise(data.player_box).groupby(["game_id", "team_id"]).minutes.sum()
    roster_counts = _normalise(data.player_box).groupby(["game_id", "team_id"]).athlete_id.nunique()
    scheduled = {_id(r["game_id"]): {"game_id": _id(r["game_id"]),
        "scheduled_team_ids": [_id(r.get("home_id")), _id(r.get("away_id"))],
        "tip_at": schedule_tip(r["date"], r["game_date_time"]).isoformat()}
        for r in data.schedule.to_dict("records")}

    def observation(entity, gid, payload, kind):
        tip, available, season = clocks[gid]
        if season > 2025 or tip.year > 2025:
            raise ValueError("Protected outcomes cannot enter development")
        return Observation(source_id=source, record_id=f"{kind}:{gid}:{entity}", entity_id=entity,
            event_id=gid, season=season, kind=ObservationKind.HISTORICAL_OUTCOME,
            effective_at=tip, payload=payload, retrieved_at=retrieved,
            time_basis=TimeBasis.ASSUMED, assumed_available_at=available, time_note=note)

    for r in _normalise(data.team_box).to_dict("records"):
        gid, team = r["game_id"], r["team_id"]
        if gid not in clocks:
            skipped["noncompetitive_game_rows" if gid in excluded else "missing_schedule"] += 1
            continue
        fields = [r.get(c) for c in ("field_goals_attempted", "free_throws_attempted", "offensive_rebounds", "total_turnovers")]
        if any(v is None or not np.isfinite(float(v)) or float(v) < 0 for v in fields):
            skipped["team_invalid_values"] += 1
            continue
        period = periods[gid]
        if period is None or pd.isna(period) or float(period) < 4:
            # Rounded past team minutes establish overtime only when schedule lacks it.
            duration = float(5 * round(duration_fallback.get((gid, team), 0) / 25))
        else:
            duration = float(40 + 5 * (int(period) - 4))
        poss = float(fields[0]) + .44 * float(fields[1]) - float(fields[2]) + float(fields[3])
        if duration <= 0 or poss <= 0:
            skipped["team_invalid_values"] += 1
            continue
        payload = {"record_type": "team_box", "team_id": team, "opponent_id": r["opponent_team_id"],
                   "possessions_estimate": poss, "possession_basis": "box_estimate",
                   "duration_minutes": duration, "points": float(r["team_score"]),
                   "roster_count": int(roster_counts.get((gid, team), 0)),
                   "roster_count_basis": "players listed in this completed game's box"}
        if (gid, team) in teams:
            raise ValueError("Conflicting team/game identity")
        teams[(gid, team)] = payload
        result.append(observation(team, gid, payload, "team_box"))
    players = _normalise(data.player_box)
    conflict = players.duplicated(["game_id", "athlete_id"], keep=False)
    skipped["player_conflicting_identity"] = int(conflict.sum())
    for r in players.loc[~conflict].to_dict("records"):
        gid, player, team = r["game_id"], r["athlete_id"], r["team_id"]
        if gid not in clocks:
            skipped["noncompetitive_game_rows" if gid in excluded else "missing_schedule"] += 1
            continue
        tp = teams.get((gid, team))
        minutes = r.get("minutes")
        if minutes is None or pd.isna(minutes):
            minutes = 0. if r.get("did_not_play") is True else None
        count = {k: r.get(v) for k, v in COUNTS.items()}
        if minutes == 0:
            count = {k: 0. for k in COUNTS}
        valid = (player is not None and tp is not None and minutes is not None and
                 np.isfinite(float(minutes)) and 0 <= float(minutes) <= 60 and
                 all(v is not None and pd.notna(v) and np.isfinite(float(v)) and
                     float(v) >= 0 and float(v).is_integer() for v in count.values()))
        if not valid:
            skipped["player_invalid_values"] += 1
            continue
        position = r.get("athlete_position_abbreviation")
        payload = {"record_type": "player_box", "team_id": team, "opponent_id": r["opponent_team_id"],
                   "minutes": float(minutes), "starter": bool(r.get("starter", False)),
                   "did_not_play": minutes == 0, "position": None if pd.isna(position) else str(position),
                   "counts": {k: float(v) for k, v in count.items()},
                   "possessions_estimate": tp["possessions_estimate"],
                   "duration_minutes": tp["duration_minutes"], "possession_basis": "box_estimate",
                   "team_source_payload_hash": payload_digest(tp),
                   "scheduled_team_ids": scheduled[gid]["scheduled_team_ids"],
                   "schedule_source_payload_hash": payload_digest(scheduled[gid])}
        result.append(observation(player, gid, payload, "player_box"))
    data.quality["observation_omissions"] = skipped
    return sorted(result, key=lambda o: (o.available_at, o.event_id, o.record_id))


def load_frozen_quotes(root=INPUTS, scenario="8h"):
    if scenario not in ("8h", "24h"):
        raise ValueError("Unknown frozen PR2 timing scenario")
    path = Path(root) / f"forecasts_{scenario}.csv.gz"
    if digest(path) != FROZEN_HASHES[scenario]:
        raise ValueError("Frozen PR2 forecast bytes changed")
    frame = pd.read_csv(path, dtype={"event_id": str, "offer_id": str, "bp_player_id": str,
                                   "game_id": str, "athlete_id": str, "team_id": str,
                                   "opponent_team_id": str})
    expected_n, expected_settled = (7604, 7473) if scenario == "8h" else (7601, 7470)
    if len(frame) != expected_n or not frame.season.eq(2025).all() or frame.offer_id.duplicated().any():
        raise ValueError("Frozen quote population changed")
    if frame.void.sum() != 131 or ((~frame.void) & frame.actual.ne(frame.open_line)).sum() != expected_settled:
        raise ValueError("Frozen settlement population changed")
    for c in ("forecast_at", "tip_at", "open_created_over", "open_created_under"):
        frame[c] = pd.to_datetime(frame[c], utc=True, errors="raise")
    if not (frame.forecast_at < frame.tip_at).all():
        raise ValueError("Frozen quote is not pre-tip")
    frame.attrs["source_sha256"] = digest(path)
    frame.attrs["source_commit"] = PR2_COMMIT
    return frame
