"""Registered pre-2025 rate-variance pilot; no market or count probabilities."""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import timedelta
import gzip
import hashlib
import json
import math
from pathlib import Path
import sys
from time import perf_counter
from zipfile import ZipFile

import numpy as np
import pandas as pd

from research.clocks import parse, schedule_tip
from research.engine import model, sources
from research.engine.store import PointInTimeStore, payload_digest
from research.diagnostics.structural_failure import digest, encoded, registration_identity

ROOT = Path(__file__).resolve().parents[2]
MARKETS = model.MARKETS
YEARS = tuple(range(2015, 2025))
BUDGET_SECONDS = 600


def require(condition, message):
    if not condition:
        raise ValueError(message)


def check_time(deadline):
    require(perf_counter() <= deadline, "Registered ten-minute budget exhausted")


def write(path, value):
    with Path(path).open("xb") as stream:
        stream.write(encoded(value))


def write_rows(path, rows):
    with Path(path).open("xb") as target:
        with gzip.GzipFile(fileobj=target, mode="wb", filename="", mtime=0) as stream:
            for row in rows:
                stream.write((json.dumps(row, sort_keys=True, allow_nan=False) + "\n").encode())


def close(left, right, label):
    if isinstance(left, dict):
        require(isinstance(right, dict) and set(left) == set(right), label)
        for key in left:
            close(left[key], right[key], label + ":" + key)
    elif isinstance(left, (list, tuple)):
        require(isinstance(right, (list, tuple)) and len(left) == len(right), label)
        for a, b in zip(left, right):
            close(a, b, label)
    elif isinstance(left, (float, int)) and not isinstance(left, bool):
        require(math.isclose(left, right, rel_tol=1e-10, abs_tol=1e-10), label)
    else:
        require(left == right, label)


def finite(value, label, lower=None):
    require(isinstance(value, (int, float)) and not isinstance(value, bool) and
            math.isfinite(value), label + " must be finite")
    require(lower is None or value >= lower, label + " below allowed range")
    return float(value)


def retained_assets(manifest):
    """Filter on manifest metadata BEFORE opening any asset, including hashes."""
    require(manifest.get("source_commit") == sources.SOURCE_COMMIT and manifest.get("complete"),
            "Complete pinned manifest required")
    selected, identities = [], set()
    for asset in manifest["assets"]:
        year = asset.get("season")
        require(type(year) is int, "Invalid asset season")
        if year >= 2025:
            continue
        key = asset.get("kind"), year
        name = asset.get("filename", "")
        require(key[0] in sources.KINDS and 2003 <= year <= 2024, "Unexpected old source asset")
        require(name and Path(name).name == name and name.endswith(".parquet"), "Unsafe source name")
        require(key not in identities, "Duplicate source asset")
        require(f"/{sources.SOURCE_COMMIT}/wnba/" in asset.get("url", ""), "Source URL is not pinned")
        identities.add(key)
        selected.append(asset)
    expected = {(kind, year) for kind in sources.KINDS
                for year in range(2010 if kind == "pbp" else 2003, 2025)}
    require(identities == expected, "Incomplete pre-2025 source assets")
    return selected


def load_sources(raw, output, deadline):
    manifest_path = Path(raw) / "manifest.json"
    require(digest(manifest_path) == sources.SOURCE_MANIFEST_SHA256, "Original manifest changed")
    manifest = json.loads(manifest_path.read_text())
    assets = retained_assets(manifest)
    # Schedule IDs are opened first, and the source sample is frozen before
    # opening player/team/PBP values. Year selection never uses those values.
    tables = {kind: [] for kind in sources.KINDS}
    for kind in ("schedule", "player_box", "team_box", "pbp"):
        for asset in assets:
            if asset["kind"] != kind:
                continue
            check_time(deadline)
            path = Path(raw) / asset["filename"]
            require(not path.is_symlink() and path.is_file(), "Missing or symlink source")
            require(path.stat().st_size == asset["bytes"] and digest(path) == asset["sha256"], "Old source bytes changed")
            columns = None
            if kind == "pbp":
                import pyarrow.parquet as pq
                available = set(pq.read_schema(path).names)
                columns = [x for x in ("game_id", "season", "wallclock") if x in available]
            frame = pd.read_parquet(path, columns=columns)
            years = pd.to_numeric(frame.season, errors="raise")
            require(years.eq(asset["season"]).all() and years.le(2024).all(), "Asset contains an excluded season")
            tables[kind].append(sources._normalise(frame))
        tables[kind] = pd.concat(tables[kind], ignore_index=True)
        if kind == "schedule":
            require(not tables[kind].game_id.duplicated().any(), "Conflicting schedule identity")
            sample = sample_games(tables[kind].to_dict("records"))
            write(output / "sample.json", sample)
    tables["schedule"]["tip_at"] = [schedule_tip(d, e) for d, e in
        zip(tables["schedule"].date, tables["schedule"].game_date_time)]
    retained = dict(manifest, assets=assets)
    write(output / "source-manifest.json", retained)
    return sources.HistoricalSources(tables["player_box"], tables["team_box"], tables["pbp"],
                                    tables["schedule"], retained, {"verified_source_files": len(assets)}), sample


def sample_games(schedule):
    result = []
    for year in (2023, 2024):
        ids = [str(row["game_id"]) for row in schedule if row["season"] == year]
        require(len(ids) >= 20 and len(ids) == len(set(ids)), "Insufficient/duplicate sample schedule")
        ids.sort(key=lambda key: hashlib.sha256(("rate-uncertainty-source-v1:" + key).encode()).hexdigest())
        result.extend({"season": year, "game_id": key} for key in ids[:20])
    return result


def scheduled_sides(row):
    return {sources._id(row["home_id"]), sources._id(row["away_id"])}


def frozen_recipe(fit_directory, bundle, seal_path):
    fit_directory = Path(fit_directory)
    seal = json.loads(Path(seal_path).read_text())
    require(seal["schema"] == "prospective-props-private-seal-v1", "Unknown original private seal")
    require(digest(bundle) == seal["bundle_sha256"] and seal.get("library_file_id"), "Original private freeze differs")
    with ZipFile(bundle) as archive:
        for name in ("validation_1.json", "fit.json", "recipe_1.json"):
            member = "research/work/structural-fit-final/" + name
            require(archive.read(member) == (fit_directory / name).read_bytes(), "Original frozen fit file changed: " + name)
    fit = json.loads((fit_directory / "fit.json").read_text())
    validation = json.loads((fit_directory / "validation_1.json").read_text())
    recipe = validation["initial_recipe"]
    require(recipe["through_season"] == 2022 and recipe["shrinkage"] == 1 and recipe["variant"] == "box",
            "Pilot requires the original through-2022 box recipe 1")
    copy = dict(recipe)
    claimed = copy.pop("recipe_hash")
    require(payload_digest(copy) == claimed, "Initial recipe content hash differs")
    require(recipe["training_cutoff"] == "24 hours before scheduled tip" and recipe["rate_q_multiplier"] == .0003,
            "Rate process or training request changed")
    require(fit["status"] == "frozen_before_2025" and
            fit["source_manifest_sha256"] == sources.SOURCE_MANIFEST_SHA256 and
            fit["recipes"]["1"] == digest(fit_directory / "recipe_1.json"), "Original fit provenance differs")
    identities = {row["path"]: row["sha256"] for row in fit["implementation"]}
    for relative in ("research/engine/model.py", "research/engine/sources.py", "research/engine/store.py", "research/clocks.py"):
        require(digest(ROOT / relative) == identities[relative], "Frozen rate/source implementation changed")
    return recipe, {"validation_sha256": digest(fit_directory / "validation_1.json"),
                    "fit_sha256": digest(fit_directory / "fit.json"),
                    "original_private_bundle_sha256": seal["bundle_sha256"]}


def exposure(payload):
    minutes = finite(payload["minutes"], "minutes", 0)
    duration = finite(payload["duration_minutes"], "duration", 0)
    possessions = finite(payload["possessions_estimate"], "possessions", 0)
    require(duration > 0 and possessions > 0 and minutes <= 60, "Invalid exposure units")
    return minutes * possessions / duration


def independent_state(records, recipe):
    """Scalar reconstruction; does not call the frozen model's state updater."""
    p = recipe["priors"]
    rates = list(p["rates"]["all"])
    variance = [v / (60 * recipe["shrinkage"]) for v in p["rate_rvar"]]
    previous_season, played = None, 0
    records = sorted(records, key=lambda o: (o.available_at, o.effective_at, o.source_id, o.record_id))
    for row in records:
        if previous_season is not None and row.season != previous_season:
            variance = [v + .003 * rvar for v, rvar in zip(variance, p["rate_rvar"])]
        previous_season = row.season
        e = exposure(row.payload)
        if e == 0:
            require(all(row.payload["counts"][m] == 0 for m in MARKETS), "DNP has positive counts")
            continue
        if played == 0:
            position = str(row.payload.get("position") or "unknown")[0].upper()
            rates = list(p["rates"].get(position, p["rates"]["all"]))
        played += 1
        for i, market in enumerate(MARKETS):
            variance[i] += .0003 * p["rate_rvar"][i]
            gain = variance[i] / (variance[i] + p["rate_rvar"][i] / e)
            rates[i] = max(1e-4, rates[i] + gain * (row.payload["counts"][market] / e - rates[i]))
            variance[i] *= 1 - gain
    return rates, variance


def rate_checkpoint(engine, request, outcome):
    require(2015 <= request.season <= 2024 and request.tip_at.year <= 2024, "Pilot cannot inspect excluded years")
    history = [o for o in engine.index.select_entity(request.entity_id, request)
               if o.payload.get("record_type") == "player_box"]
    for row in history:
        require(row.available_at < request.as_of and row.effective_at < request.as_of and
                row.event_id != request.event_id and row.season <= 2024, "State consumes future/target evidence")
    state = engine._player_state(request.entity_id, history)
    rates = [finite(float(x), "rate", 0) for x in state["rates"]]
    variance = [finite(float(x), "rate variance", 0) for x in state["variance"]]
    require(all(r > 0 for r in rates), "Rate mean must be positive")
    e = exposure(outcome.payload)
    counts = [finite(outcome.payload["counts"][m], "count", 0) for m in MARKETS]
    require(all(x == int(x) for x in counts), "Count must be integer")
    require(e > 0 or all(x == 0 for x in counts), "DNP has positive counts")
    record = {"season": request.season, "game_id": request.event_id, "player_id": request.entity_id,
              "team_id": outcome.payload["team_id"], "source_record_id": outcome.record_id,
              "as_of": request.as_of.isoformat(), "tip_at": request.tip_at.isoformat(),
              "history_rows": len(history),
              "last_history_available_at": max((o.available_at for o in history), default=None),
              "rate_mean": rates, "rate_variance": variance, "actual_exposure": e,
              "actual_minutes": outcome.payload["minutes"], "actual_counts": counts,
              "dnp": e == 0}
    if record["last_history_available_at"] is not None:
        record["last_history_available_at"] = record["last_history_available_at"].isoformat()
    return record, history


def independent_clocks(data):
    result = {}
    for row in data.schedule.to_dict("records"):
        if row.get("season_type") not in (2, 3) or str(row.get("type_abbreviation", "")).upper() in {"ALLSTAR", "EXHIBITION", "EXH", "PRE"}:
            continue
        tip = schedule_tip(row["date"], row["game_date_time"])
        clocks = [tip + timedelta(hours=8)]
        completion = row.get("completed_at")
        if completion is not None and pd.notna(completion):
            clocks.append(parse("wehoop.completed_at", completion) + timedelta(hours=1))
        result[row["game_id"]] = [tip, max(clocks)]
    if "wallclock" in data.pbp:
        for game, times in data.pbp.groupby("game_id", sort=False).wallclock:
            if game not in result:
                continue
            for value in times.dropna().unique():
                if str(value).strip():
                    result[game][1] = max(result[game][1], parse("wehoop.wallclock", value) + timedelta(hours=1))
    return result


def appearance_inventory(data, observations):
    normalized = {(o.event_id, o.entity_id): o for o in observations if o.payload.get("record_type") == "player_box"}
    excluded = set(data.quality["excluded_noncompetitive_game_ids"])
    inventory = []
    for index, row in enumerate(data.player_box.to_dict("records")):
        if row["season"] not in YEARS:
            continue
        key = row["game_id"], row["athlete_id"]
        outcome = normalized.get(key)
        status = "normalized" if outcome else "excluded_noncompetitive" if key[0] in excluded else "missing_or_invalid"
        # Preserve the raw denominator and detect impossible raw grades before
        # the old normalizer's explicit-DNP zero fill can conceal a contradiction.
        minutes = row.get("minutes")
        if pd.isna(minutes) and row.get("did_not_play") is True:
            minutes = 0.
        impossible = False
        if pd.notna(minutes):
            impossible = not math.isfinite(float(minutes)) or not 0 <= float(minutes) <= 60
            impossible |= row.get("did_not_play") is True and minutes != 0
            for name in sources.COUNTS.values():
                value = row.get(name)
                if pd.notna(value):
                    impossible |= not math.isfinite(float(value)) or value < 0 or value != int(value) or (minutes == 0 and value != 0)
        if impossible:
            status = "impossible_raw_grade"
        inventory.append({"raw_row_index": index, "season": int(row["season"]), "game_id": key[0],
            "player_id": key[1], "team_id": row["team_id"], "opponent_id": row["opponent_team_id"],
            "status": status, "source_record_id": outcome.record_id if outcome else None,
            "dnp": outcome.payload["minutes"] == 0 if outcome else None})
    return inventory


def denominator(inventory, checkpoints=()):
    result = {}
    for year in YEARS:
        rows = [r for r in inventory if r["season"] == year]
        run = [r for r in checkpoints if r["season"] == year]
        result[str(year)] = {
            "raw_requested": len(rows), "distinct_player_games": len({(r["game_id"], r["player_id"]) for r in rows}),
            "normalized": sum(r["status"] == "normalized" for r in rows),
            "excluded_noncompetitive": sum(r["status"] == "excluded_noncompetitive" for r in rows),
            "missing_or_invalid": sum(r["status"] == "missing_or_invalid" for r in rows),
            "impossible_raw_grade": sum(r["status"] == "impossible_raw_grade" for r in rows),
            "explicit_dnp": sum(r["status"] == "normalized" and r["dnp"] for r in rows),
            "normalized_played": sum(r["status"] == "normalized" and not r["dnp"] for r in rows),
            "rate_checkpoints": len(run), "played_checkpoints": sum(not r["dnp"] for r in run),
            "missing_requests": sum(r["status"] == "normalized" for r in rows) - len(run)}
    return result


def source_check(data, observations, inventory, sample, recipe, deadline):
    selected = {q["game_id"] for q in sample}
    records = {(o.event_id, o.entity_id): o for o in observations if o.payload.get("record_type") == "player_box"}
    requests, setup_failures = {}, []
    for year in (2023, 2024):
        try:
            for request, outcome in model.training_examples(observations, year):
                if request.event_id in selected:
                    requests[(request.event_id, request.entity_id)] = request
        except ValueError as error:
            setup_failures.append({"season": year, "family": "request_identity", "reason": str(error)})
    try:
        clock = independent_clocks(data)
    except ValueError as error:
        clock = {}
        setup_failures.append({"family": "source_clocks", "reason": str(error)})
    teams = {r["game_id"]: scheduled_sides(r) for r in data.schedule.to_dict("records")}
    engine, rows = model.StructuralModel(observations, recipe), []
    for row in inventory:
        if row["game_id"] not in selected:
            continue
        check_time(deadline)
        evidence = dict(row, usable=False, hard_failure=False, reason=row["status"])
        if row["status"] == "normalized":
            try:
                key = row["game_id"], row["player_id"]
                require(key in requests and key in records, "Normalized sample appearance lacks a request or source")
                require(key[0] in teams and key[0] in clock, "Sample schedule or source clock is missing")
                outcome, request = records[key], requests[key]
                require({row["team_id"], row["opponent_id"]} == teams[key[0]], "Raw sample teams differ from schedule")
                require(outcome.payload["team_id"] == row["team_id"] and outcome.record_id == row["source_record_id"], "Sample source identity differs")
                require([outcome.effective_at, outcome.available_at] == clock[key[0]], "Target source clock differs")
                checkpoint, history = rate_checkpoint(engine, request, outcome)
                for prior in history:
                    require(prior.event_id in clock and [prior.effective_at, prior.available_at] == clock[prior.event_id], "Consumed source clock differs")
                rates, variance = independent_state(history, recipe)
                close(rates, checkpoint["rate_mean"], "Independent rate means differ")
                close(variance, checkpoint["rate_variance"], "Independent rate variance differs")
                close(exposure(outcome.payload), model._exposure(outcome), "Exposure units differ")
                evidence.update(usable=True, reason="reconciled", checkpoint=checkpoint,
                                independent_rate_mean=rates, independent_rate_variance=variance)
            except ValueError as error:
                # Retain every requested row, and never let >=99% coverage
                # waive a broken clock, state, identity or exposure contract.
                evidence.update(hard_failure=True, reason=str(error))
        rows.append(evidence)
    coverage = {}
    for year in (2023, 2024):
        requested = [r for r in rows if r["season"] == year]
        usable = sum(r["usable"] for r in requested)
        coverage[str(year)] = {"requested": len(requested), "usable": usable,
                               "coverage": usable / len(requested) if requested else 0.}
    impossible = sum(r["status"] == "impossible_raw_grade" for r in inventory)
    hard_failures = sum(r["hard_failure"] for r in rows)
    passed = all(c["coverage"] >= .99 for c in coverage.values()) and impossible == 0 and hard_failures == 0 and not setup_failures
    return {"status": "PASS_UNDER_ASSUMPTION" if passed else "FAIL", "coverage": coverage,
            "impossible_raw_grades_all_requested_years": impossible, "rows": rows,
            "hard_failures": hard_failures, "setup_failures": setup_failures,
            "meaning": "Historical clocks are assumed. All requested rows are retained; failed state/clock/identity/grade checks cannot be waived by aggregate coverage."}


def checkpoints(observations, recipe, years, deadline):
    engine, rows = model.StructuralModel(observations, recipe), []
    for year in years:
        for request, outcome in model.training_examples(observations, year):
            check_time(deadline)
            row, _ = rate_checkpoint(engine, request, outcome)
            rows.append(row)
    return rows


def innovations(rows, market):
    i = MARKETS.index(market)
    result = []
    for row in rows:
        e = finite(row["actual_exposure"], "exposure", 0)
        if row["dnp"]:
            require(e == 0 and all(y == 0 for y in row["actual_counts"]), "Invalid DNP innovation")
            continue
        require(e > 0, "Played exposure must be positive")
        r = finite(row["rate_mean"][i], "rate", 0)
        v = finite(row["rate_variance"][i], "variance", 0)
        y = finite(row["actual_counts"][i], "actual count", 0)
        require(r > 0 and y == int(y), "Invalid innovation")
        mu, uncertainty = e * r, e * e * v
        result.append((mu, y - mu, uncertainty, v))
    require(result, "No played innovation rows")
    return np.asarray(result, dtype=float)


def freeze_scalars(rows, recipe):
    require(all(2015 <= r["season"] <= 2022 for r in rows), "Noise calibration cannot use check seasons")
    result = {}
    for i, market in enumerate(MARKETS):
        a = innovations(rows, market)
        mu, residual, uncertainty = a[:, 0], a[:, 1], a[:, 2]
        total = float(np.sum(residual ** 2) / np.sum(mu))
        noise = float((np.sum(residual ** 2) - np.sum(uncertainty)) / np.sum(mu))
        close(min(2., max(1., total)), recipe["count_fano"][i], "Original Fano calibration does not reproduce")
        result[market] = {"n": len(a), "raw_total": total, "raw_noise": noise,
            "F_total": max(1., total), "F_noise": max(1., noise), "total_floor": total < 1.,
            "noise_floor": noise < 1., "original_Fano": recipe["count_fano"][i],
            "sum_mu": float(np.sum(mu)), "sum_squared_residual": float(np.sum(residual ** 2)),
            "sum_rate_uncertainty": float(np.sum(uncertainty))}
    return result


def quantiles(values):
    return {"min": float(np.min(values)), "median": float(np.quantile(values, .5)),
            "p90": float(np.quantile(values, .9)), "max": float(np.max(values))}


def variance_report(rows, scalars):
    result = {}
    for market in MARKETS:
        a = innovations(rows, market)
        mu, residual, uncertainty, posterior = (a[:, i] for i in range(4))
        scalar = scalars[market]
        variance = {"old": scalar["original_Fano"] * mu, "constant": scalar["F_total"] * mu,
                    "rate": scalar["F_noise"] * mu + uncertainty}
        scores, summaries = {}, {}
        for name, values in variance.items():
            require(np.isfinite(values).all() and (values > 0).all(), "Nonpositive predictive variance")
            scores[name] = np.log(values) + residual ** 2 / values
            summaries[name] = {"mean_variance": float(values.mean()),
                "squared_error_to_variance": float(np.sum(residual ** 2) / np.sum(values)),
                "gaussian_variance_score": float(scores[name].mean())}
        share = uncertainty / variance["rate"]
        result[market] = {"n": len(a), "mean_residual_actual_minus_predicted": float(residual.mean()),
            "mean_squared_residual": float(np.mean(residual ** 2)), "mean_mu": float(mu.mean()),
            "variances": summaries,
            "rate_minus_constant_score": float(np.mean(scores["rate"] - scores["constant"])),
            "rate_minus_old_score": float(np.mean(scores["rate"] - scores["old"])),
            "mean_rate_uncertainty_share": float(share.mean()),
            "aggregate_rate_uncertainty_share": float(uncertainty.sum() / variance["rate"].sum()),
            "posterior_variance": quantiles(posterior), "rate_uncertainty": quantiles(uncertainty),
            "rate_share": quantiles(share),
            "variance_identity_max_residual": float(np.max(np.abs(variance["rate"] - scalar["F_noise"] * mu - uncertainty)))}
    return result


def decision(source, scalars=None, checks=None):
    if source["status"] != "PASS_UNDER_ASSUMPTION":
        return {"status": "FAIL", "decision": "collect named missing source", "gates": {"source": False},
                "reason": "Source/identity/grade reconciliation failed; no noise scalar or check-year variance comparison is released."}
    p, a, b = scalars["points"], checks["2023"]["points"], checks["2024"]["points"]
    gates = {"source": True, "unfloored_points_scalars": not p["total_floor"] and not p["noise_floor"],
        "rate_uncertainty_magnitude": a["aggregate_rate_uncertainty_share"] >= .05 and b["aggregate_rate_uncertainty_share"] >= .05,
        "useful_variance_score": a["rate_minus_constant_score"] <= -.01 and b["rate_minus_constant_score"] <= 0,
        "variance_calibration": .90 <= a["variances"]["rate"]["squared_error_to_variance"] <= 1.10 and
                                .85 <= b["variances"]["rate"]["squared_error_to_variance"] <= 1.15}
    passed = all(gates.values())
    return {"status": "PASS" if passed else "FAIL", "decision": "register one variance-aware points candidate" if passed else "stop this variance change",
            "gates": gates, "reason": "All registered mechanism screens passed; count probabilities and market performance remain untested." if passed else
            "At least one registered mechanism screen failed. Keep the original structural failures closed and do not tune this pilot."}


def render(result):
    lines = ["# Rate uncertainty pilot", "", "Decision: **" + result["decision"]["decision"] + "**.", "",
             result["decision"]["reason"], "",
             "This is a market-free variance check on reused pre-2025 data. Actual exposure is an analysis denominator, not a pregame input. "
             "It does not evaluate a count distribution, betting advantage or independent test.", ""]
    if result.get("checks"):
        lines += ["| Season / market | Rate share | Rate-minus-constant score | Squared error / rate-aware variance |",
                  "| --- | ---: | ---: | ---: |"]
        for year in ("2023", "2024"):
            for market in MARKETS:
                r = result["checks"][year][market]
                lines.append(f"| {year} / {market} | {100*r['aggregate_rate_uncertainty_share']:.2f}% | {r['rate_minus_constant_score']:+.5f} | {r['variances']['rate']['squared_error_to_variance']:.3f} |")
        lines += ["", "Only points can satisfy the registered primary mechanism. Other markets cannot replace it. "
            "The uncapped constant control separates posterior uncertainty from the effect of relaxing the old Fano cap. "
            "Lower variance score is better; the rate-aware calculation estimates observation noise after subtracting the posterior term, avoiding its automatic double addition.", ""]
    lines += ["No rate/minutes model was fitted, no old forecast replaced, and no 2025/2026 asset or market price was opened. "
              "Historical availability remains assumed. The frozen 2022 recipe and the two predetermined scalar estimates are retained with all denominators and checkpoints.", ""]
    return "\n".join(lines)


def run_pilot(raw, fit_directory, bundle, seal, output, registration, commit, *,
              budget_seconds=BUDGET_SECONDS, correction=None, correction_commit=None):
    require(type(budget_seconds) is int and 0 < budget_seconds <= BUDGET_SECONDS, "Budget must be within the original600 seconds")
    reg = registration_identity(registration, commit)
    amendment = registration_identity(correction, correction_commit) if correction else None
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    deadline = perf_counter() + budget_seconds
    recipe, provenance = frozen_recipe(fit_directory, bundle, seal)
    write(output / "recipe.json", recipe)
    data, sample = load_sources(raw, output, deadline)
    print("Verified and loaded only pre-2025 source assets; source sample is frozen.", flush=True)
    observations = sources.observations(data, availability_hours=8)
    require(all(o.season <= 2024 and o.effective_at.year <= 2024 for o in observations), "Excluded source observation")
    eligible = [o for o in PointInTimeStore(observations).observations
                if o.season <= 2022 and o.effective_at.year <= 2022 and o.available_at.year <= 2022]
    require(model._fit_hash(eligible) == recipe["fit_input_hash"], "Original 2022 fit input population differs")
    close(model._priors(eligible, "box"), recipe["priors"], "Pre-2015 priors do not reproduce")
    inventory = appearance_inventory(data, observations)
    write_rows(output / "appearance-inventory.jsonl.gz", inventory)
    source = source_check(data, observations, inventory, sample, recipe, deadline)
    write(output / "source-check.json", source)
    print("Completed independent source/state checks: " + source["status"], flush=True)
    result = {"schema": "rate-uncertainty-pilot-v1", "evidence_kind": "market-free-reused-development",
              "registration": reg, "recipe_provenance": provenance, "source_status": source["status"],
              "software_correction": amendment,
              "source_quality": data.quality, "new_rate_or_minutes_fits": 0, "excluded_assets_opened": 0,
              "market_prices_read": 0, "prior_verification": "Exact 2022 fit input hash and independently recomputed pre-2015 priors match."}
    if source["status"] == "PASS_UNDER_ASSUMPTION":
        calibration = checkpoints(observations, recipe, range(2015, 2023), deadline)
        write_rows(output / "calibration-checkpoints.jsonl.gz", calibration)
        scalars = freeze_scalars(calibration, recipe)
        freeze = {"schema": "rate-noise-scalar-freeze-v1", "calibration_years": [2015, 2022],
                  "calibration_sha256": digest(output / "calibration-checkpoints.jsonl.gz"),
                  "recipe_sha256": digest(output / "recipe.json"), "scalars": scalars}
        write(output / "scalar-freeze.json", freeze)
        print("Calibration checkpoints and noise scalars frozen before check-year aggregates.", flush=True)
        # No check-year aggregate is opened before this immutable scalar file.
        freeze_hash = digest(output / "scalar-freeze.json")
        check_reports, all_rows = {}, list(calibration)
        for year in (2023, 2024):
            rows = checkpoints(observations, recipe, [year], deadline)
            write_rows(output / f"checkpoints-{year}.jsonl.gz", rows)
            require(digest(output / "scalar-freeze.json") == freeze_hash, "Scalar freeze changed")
            check_reports[str(year)] = variance_report(rows, scalars)
            all_rows.extend(rows)
            print(f"Completed fixed {year} variance check.", flush=True)
        result.update(scalars=scalars, calibration=variance_report(calibration, scalars), checks=check_reports,
                      denominators=denominator(inventory, all_rows), scalar_freeze_sha256=freeze_hash,
                      decision=decision(source, scalars, check_reports))
        require(all(d["missing_requests"] == 0 for d in result["denominators"].values()), "Normalized requested appearances were dropped")
    else:
        result.update(denominators=denominator(inventory), decision=decision(source))
    check_time(deadline)
    write(output / "results.json", result)
    with (output / "decision.md").open("x") as stream:
        stream.write(render(result))
    code = [Path(__file__), ROOT / "research/diagnostics/structural_failure.py", ROOT / "research/engine/model.py",
            ROOT / "research/engine/sources.py", ROOT / "research/engine/store.py", ROOT / "research/clocks.py"]
    receipt = {"schema": "rate-uncertainty-pilot-receipt-v1", "status": "complete", "registration": reg,
        "budget_seconds": budget_seconds, "software_correction": amendment,
        "implementation": [{"path": p.resolve().relative_to(ROOT).as_posix(), "sha256": digest(p)} for p in code],
        "artifacts": [{"path": p.name, "sha256": digest(p)} for p in sorted(output.iterdir()) if p.is_file()],
        "checks": "Original private-fit bytes, 2022 input fingerprint, pre-2015 priors, selected source/state identities, past-only states, scalar-before-check ordering and complete denominators verified."}
    write(output / "receipt.json", receipt)
    print(json.dumps(result["decision"], sort_keys=True), flush=True)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for argument in ("raw", "fit-directory", "bundle", "seal", "output", "registration"):
        parser.add_argument("--" + argument, type=Path, required=True)
    parser.add_argument("--registration-commit", required=True)
    parser.add_argument("--budget-seconds", type=int, default=BUDGET_SECONDS)
    parser.add_argument("--correction", type=Path)
    parser.add_argument("--correction-commit")
    a = parser.parse_args(argv)
    started = perf_counter()
    try:
        run_pilot(a.raw, a.fit_directory, a.bundle, a.seal, a.output, a.registration, a.registration_commit,
                  budget_seconds=a.budget_seconds, correction=a.correction, correction_commit=a.correction_commit)
    finally:
        print(json.dumps({"invocation_elapsed_seconds": perf_counter() - started,
                          "computation_budget_seconds": a.budget_seconds}), file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
