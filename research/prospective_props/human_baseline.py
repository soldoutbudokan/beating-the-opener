"""Append-only human news baseline, unlocked only by both completed props scores.

Identity and team matching use boxes available before each entry/game cutoff.
An absent outcome record is missing evidence, never an observed did-not-play.
This component does not release game or cricket research windows.
"""
from collections import Counter
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import re
import unicodedata

import pandas as pd

from research.clocks import parse, schedule_tip
from .review import review as review_prospective_score

ARMS = ("fp-prospective-1", "fp-prospective-2")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _name(value):
    return re.sub("[^a-z]", "", unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode().lower())


def _id(value):
    return None if pd.isna(value) else str(value).removesuffix(".0")


def validate_releases(release_receipts):
    """Each arm maps to receipt_path and prepared_dir; verify durable file bytes."""
    if set(release_receipts) != set(ARMS):
        raise ValueError("Both completed props receipts are required before human accuracy")
    endpoints, hashes = [], {}
    for arm in ARMS:
        spec = release_receipts[arm]
        receipt_path = Path(spec["receipt_path"])
        root = Path(spec["prepared_dir"])
        receipt = json.loads(receipt_path.read_text())
        result_path = receipt_path.with_name(f"{arm}.results.json")
        prepared_path = root / "prepared.json"
        prepared = json.loads(prepared_path.read_text())
        if receipt.get("status") != "complete" or receipt.get("arm") != arm:
            raise ValueError("An incomplete or mismatched arm cannot release outcomes")
        for field in ("registration_commit", "registration_sha256", "recipe_sha256",
                      "forecasts_sha256", "implementation_sha256", "results_sha256"):
            length = 40 if field == "registration_commit" else 64
            if not re.fullmatch(f"[0-9a-f]{{{length}}}", str(receipt.get(field, ""))):
                raise ValueError(f"Missing valid release provenance: {field}")
        if digest(result_path) != receipt["results_sha256"]:
            raise ValueError("Release result bytes changed")
        forecast = prepared["forecasts"][arm]
        relative = Path(forecast["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Unsafe release forecast path")
        if (digest(root / relative) != receipt["forecasts_sha256"] or
                forecast["sha256"] != receipt["forecasts_sha256"] or
                digest(root / "recipe.json") != receipt["recipe_sha256"] or
                prepared["recipe_sha256"] != receipt["recipe_sha256"] or
                prepared["implementation_sha256"] != receipt.get("generation_implementation_sha256")):
            raise ValueError("Release forecast, recipe or implementation hashes disagree")
        results = json.loads(result_path.read_text())
        review_prospective_score(results, receipt, results_bytes=result_path.read_bytes())
        if results.get("arm") != arm or results["population"] != prepared["population"]:
            raise ValueError("Release endpoint population disagrees")
        endpoint = date.fromisoformat(results["population"]["endpoint_date"])
        if endpoint.year != 2026 or int(results["population"]["endpoint_n"]) < 3000:
            raise ValueError("Props threshold endpoint has not arrived")
        endpoints.append(endpoint)
        hashes[arm] = {"receipt_sha256": digest(receipt_path), "results_sha256": digest(result_path),
                       "prepared_sha256": digest(prepared_path), "endpoint_date": endpoint.isoformat(),
                       "forecasts_sha256": receipt["forecasts_sha256"]}
    return max(endpoints), hashes


def _metric_population(rows):
    return {"entry_game_pairs": len(rows), "entries": len({r["entry_index"] for r in rows}),
            "player_games": len({(r["game_id"], r["athlete_id"]) for r in rows})}


def _adequate(rows):
    n = _metric_population(rows)
    return n["entries"] >= 30 and n["player_games"] >= 10


def summarize_rows(rows):
    """Pure saved-row arithmetic; also used by the independent output verifier."""
    eligible = [r for r in rows if r["resolution"] == "resolved"]
    result = {}
    for status in ("out", "in", "questionable"):
        group = [r for r in eligible if r["status"] == status]
        item = {**_metric_population(group), "status": "estimated" if _adequate(group) else "insufficient",
                "played": sum(r["actual_minutes"] > 0 for r in group),
                "out_played_rate": None, "minutes_est": None, "minutes_range": None}
        if status == "out" and _adequate(group):
            item["out_played_rate"] = item["played"] / len(group)
        if status in ("in", "questionable"):
            estimated = [r for r in group if r["minutes_est"] is not None]
            e = {**_metric_population(estimated), "status": "estimated" if _adequate(estimated) else "insufficient",
                 "mae": None, "bias_forecast_minus_actual": None}
            if _adequate(estimated):
                errors = [r["minutes_est"] - r["actual_minutes"] for r in estimated]
                e.update(mae=sum(abs(x) for x in errors) / len(errors),
                         bias_forecast_minus_actual=sum(errors) / len(errors))
            item["minutes_est"] = e
            ranged = [r for r in group if r["minutes_range"] is not None]
            c = {**_metric_population(ranged), "status": "estimated" if _adequate(ranged) else "insufficient",
                 "coverage": None}
            if _adequate(ranged):
                c["coverage"] = sum(r["minutes_range"][0] <= r["actual_minutes"] <= r["minutes_range"][1] for r in ranged) / len(ranged)
            item["minutes_range"] = c
        result[status] = item
    return result


def _last_pre_tip(rows):
    last = {}
    for row in rows:
        if row["resolution"] != "resolved":
            continue
        key = (row["game_id"], row["athlete_id"])
        if key not in last or (row["added"], row["entry_index"]) > (last[key]["added"], last[key]["entry_index"]):
            last[key] = row
    return list(last.values())


def evaluate(entries, player_box, schedule, release_receipts):
    endpoint, release_hashes = validate_releases(release_receipts)
    entries = entries["entries"] if isinstance(entries, dict) else entries
    if not isinstance(entries, list):
        raise ValueError("Append-only entries must be a list")
    schedule = schedule.copy()
    schedule["_tip"] = [schedule_tip(d, e) for d, e in zip(schedule.date, schedule.game_date_time)]
    schedule["_id"] = schedule.game_id.map(_id)
    if schedule._id.duplicated().any():
        raise ValueError("Schedule identity is ambiguous")
    schedule["_date"] = schedule._tip.map(lambda t: pd.Timestamp(t).tz_convert("America/New_York").date())
    if not schedule.season.eq(2026).all() or not pd.to_datetime(schedule._tip, utc=True).dt.year.eq(2026).all():
        raise ValueError("Human baseline expects explicitly released 2026 schedules only")
    games = sorted(schedule.to_dict("records"), key=lambda g: (g["_tip"], g["_id"]))
    clock = {g["_id"]: g["_tip"] for g in games}
    box = player_box.copy()
    box["_game"] = box.game_id.map(_id)
    box["_athlete"] = box.athlete_id.map(_id)
    box["_team"] = box.team_id.map(_id)
    box["_name"] = box.athlete_display_name.map(_name)
    box["_available"] = box._game.map(lambda g: clock.get(g))
    box = box[box._available.notna()].copy()
    box["_available"] = pd.to_datetime(box._available, utc=True) + pd.Timedelta(hours=8)
    box["_name"] = box["_name"].astype(str)
    by_name = {k: v for k, v in box.groupby("_name", sort=False)}
    source_hashes = {"entries_sha256": hashlib.sha256(canonical(entries).encode()).hexdigest(),
                     "player_box_sha256": hashlib.sha256(player_box.to_json(orient="records", date_format="iso").encode()).hexdigest(),
                     "schedule_sha256": hashlib.sha256(schedule.drop(columns=["_tip", "_id", "_date"]).to_json(orient="records", date_format="iso").encode()).hexdigest()}
    output, entry_log = [], []
    for i, entry in enumerate(entries):
        base = {"entry_index": i, "player": entry.get("player"), "status": entry.get("status"),
                "added": entry.get("added"), "game_date": entry.get("game_date"),
                "superseded_by": entry.get("superseded_by"), "minutes_est": entry.get("minutes_est"),
                "minutes_range": entry.get("minutes_range"), "game_id": None, "athlete_id": None,
                "tip_at": None, "actual_minutes": None}
        log = {"entry_index": i, "source_entry": entry, "row_indices": []}
        entry_log.append(log)
        def append(reason, **extra):
            log["row_indices"].append(len(output))
            output.append({**base, **extra, "resolution": reason})
        try:
            added = parse("override.added", entry.get("added"))
            target = date.fromisoformat(entry["game_date"]) if entry.get("game_date") else None
            if target and target <= endpoint:
                append("excluded_before_or_at_endpoint")
                continue
            if target and target.year != 2026:
                append("excluded_wrong_season")
                continue
            if entry.get("status") not in ("out", "in", "questionable"):
                append("unrecognized_status")
                continue
            if base["minutes_est"] is not None and not 0 <= float(base["minutes_est"]) <= 60:
                raise ValueError("invalid minutes estimate")
            if base["minutes_range"] is not None:
                lo, hi = base["minutes_range"]
                if not 0 <= float(lo) <= float(hi) <= 60:
                    raise ValueError("invalid minutes range")
        except (ValueError, TypeError, KeyError):
            append("invalid_entry")
            continue
        until = None
        pointer = entry.get("superseded_by")
        if target is None and pointer is not None:
            try:
                if type(pointer) is not int or pointer <= i or pointer >= len(entries):
                    raise ValueError("invalid pointer")
                next_entry = entries[pointer]
                until = parse("override.added", next_entry["added"])
                if until <= added or _name(next_entry["player"]) != _name(entry["player"]):
                    raise ValueError("invalid supersession identity or time")
            except (ValueError, TypeError, KeyError):
                append("unresolved_supersession")
                continue
        candidates = [g for g in games if g["_date"] > endpoint and
                      (g["_date"] == target if target else g["_tip"] > added and (until is None or g["_tip"] < until))]
        matches = []
        history = by_name.get(_name(entry.get("player")), box.iloc[:0])
        for game in candidates:
            cutoff = min(added, game["_tip"]) if target else game["_tip"]
            prior = history[history._available < cutoff]
            identities = prior._athlete.dropna().unique()
            if len(identities) != 1:
                continue
            latest = prior[prior._available == prior._available.max()]
            teams = latest._team.dropna().unique()
            if len(teams) != 1 or teams[0] not in {_id(game.get("home_id")), _id(game.get("away_id"))}:
                continue
            matches.append((game, str(identities[0])))
        if not matches or (target is not None and len(matches) != 1):
            append("unresolved_identity_or_game" if not matches else "ambiguous_game")
            continue
        for game, athlete in matches:
            extra = {"game_id": game["_id"], "athlete_id": athlete, "tip_at": game["_tip"].isoformat(),
                     "game_date": game["_date"].isoformat(), "added": added.isoformat()}
            if added >= game["_tip"]:
                append("late_at_or_after_tip", **extra)
                continue
            observed = box[box._game.eq(game["_id"]) & box._athlete.eq(athlete)]
            if len(observed) != 1:
                append("missing_player_record" if not len(observed) else "ambiguous_player_record", **extra)
                continue
            record = observed.iloc[0]
            if record._team not in {_id(game.get("home_id")), _id(game.get("away_id"))}:
                append("outcome_team_mismatch", **extra)
                continue
            minutes = record.minutes
            if pd.isna(minutes) and record.get("did_not_play") == True:
                minutes = 0.
            if pd.isna(minutes) or not 0 <= float(minutes) <= 60:
                append("missing_or_invalid_minutes", **extra)
                continue
            append("resolved", actual_minutes=float(minutes), **extra)
    result = {"schema": "human-news-baseline-v1", "scope": "owner-released props outcomes only",
              "endpoint_date": endpoint.isoformat(), "timing_basis": "Historical team identity available at assumed tip+8h; entry itself strictly before target tip",
              "release_hashes": release_hashes, "source_hashes": source_hashes,
              "entries_requested": len(entries), "entry_log": entry_log, "rows": output,
              "row_status_counts": dict(sorted(Counter(r["resolution"] for r in output).items())),
              "primary_all_entries": summarize_rows(output),
              "last_pre_tip_sensitivity": summarize_rows(_last_pre_tip(output))}
    result["saved_rows_sha256"] = hashlib.sha256(canonical(output).encode()).hexdigest()
    return result


def verify_saved(result):
    if result["schema"] != "human-news-baseline-v1":
        raise ValueError("Unsupported human baseline schema")
    rows = result["rows"]
    if hashlib.sha256(canonical(rows).encode()).hexdigest() != result["saved_rows_sha256"]:
        raise ValueError("Saved human baseline rows changed")
    if len(result["entry_log"]) != result["entries_requested"]:
        raise ValueError("Requested entries disappeared")
    indices = [index for e in result["entry_log"] for index in e["row_indices"]]
    if sorted(indices) != list(range(len(rows))):
        raise ValueError("Entry/row coverage changed")
    for r in rows:
        if r["resolution"] == "resolved":
            if date.fromisoformat(r["game_date"]) <= date.fromisoformat(result["endpoint_date"]):
                raise ValueError("Protected date entered human baseline")
            if parse("override.added", r["added"]) >= parse("quote.tip_at", r["tip_at"]):
                raise ValueError("Late entry entered human baseline")
    if summarize_rows(rows) != result["primary_all_entries"] or summarize_rows(_last_pre_tip(rows)) != result["last_pre_tip_sensitivity"]:
        raise ValueError("Human baseline report arithmetic changed")
    if dict(sorted(Counter(r["resolution"] for r in rows).items())) != result["row_status_counts"]:
        raise ValueError("Human baseline coverage arithmetic changed")
    return {"status": "PASS", "entries_verified": result["entries_requested"], "rows_verified": len(rows)}


def render_note(result):
    verify_saved(result)
    resolved = result["row_status_counts"].get("resolved", 0)
    lines = [f"The human news record has {resolved} resolved entry/game pairs after {result['endpoint_date']}.",
             f"All {result['entries_requested']} original entries remain in the evidence, including late and unresolved entries.",
             "The primary analysis keeps every eligible entry. A separate sensitivity keeps the final eligible entry before each game.",
             "Estimates require 30 distinct entries across 10 player-games for each reported status or measurement."]
    for status, m in result["primary_all_entries"].items():
        lines.append(f"{status}: {m['entries']} entries, {m['player_games']} player-games; {m['status']}.")
        if m["out_played_rate"] is not None:
            lines.append(f"Players marked out played in {m['out_played_rate']:.1%} of resolved entry/game pairs.")
        if m["minutes_est"] and m["minutes_est"]["mae"] is not None:
            e = m["minutes_est"]
            lines.append(f"For {status} entries, minutes estimates missed by {e['mae']:.2f} minutes on average; the signed forecast-minus-actual bias was {e['bias_forecast_minus_actual']:+.2f} minutes.")
        if m["minutes_range"] and m["minutes_range"]["coverage"] is not None:
            lines.append(f"The stated {status} minutes ranges contained actual minutes in {m['minutes_range']['coverage']:.1%} of resolved pairs.")
    lines.append("This describes the human layer on released props data. It is not an automated-extractor test and does not release the game or cricket experiments.")
    return "\n\n".join(lines) + "\n"


def save_evaluation(result, output):
    """Create a new evidence directory; repeated calls never overwrite a run."""
    checked = verify_saved(result)
    path = Path(output)
    path.mkdir(parents=True, exist_ok=False)
    (path / "results.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    (path / "note.md").write_text(render_note(result))
    (path / "verification.json").write_text(json.dumps(checked, indent=2) + "\n")
    receipt = {"schema": "human-news-baseline-receipt-v1", "status": "complete",
               "implementation_sha256": digest(__file__), "results_sha256": digest(path / "results.json"),
               "saved_rows_sha256": result["saved_rows_sha256"], "source_hashes": result["source_hashes"],
               "release_hashes": result["release_hashes"]}
    (path / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt
