"""Isolated reconstruction of the two originally registered WNBA props arms.

Commands: freeze (pre-2026 fitting only), prepare (counts and saved forecasts),
score (saved-forecast arithmetic after the root agent publishes the freeze),
verify (score reproduction in a different new directory). Each output directory
must be new. All pinned source modules are extracted using git show, with their
bytes hashed; existing source, raw and live files are never edited.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.util
import io
import json
import math
from pathlib import Path
import pickle
import re
import subprocess
import sys
import zipfile

import numpy as np
import pandas as pd

ARM1 = "5f6e021f97e20fdd9d86a97e1cedc24b4da633ad"
ARM2 = "3d2251ed2c7024b1afe15f7303d6e28cfed3f3c8"
ASG = "0e420df01a17840dc3bcc4e589ce9081777cb7b9"
REGISTRATION_PATH = "research/experiments/2026-09-overdue-props-reconstruction.md"
PRIVATE_EXECUTION_PATH = "research/experiments/2026-09-props-private-evidence.md"
EVIDENCE_VISIBILITY = "private_pending_owner_publication_approval"
PUBLICATION_APPROVAL_REASON = "Automatic approval review rejected both public forecast contents and subsequent public SHA256 metadata for private fitted artifacts; scoring and PR authorization did not authorize that public disclosure."
MODULES = ("features", "fp_model", "fp_benchmark", "talent", "build_modelset", "build_props", "grade_props", "odds_utils", "dist_utils")
FROZEN_ROOT = "research/prospective_props/evidence/frozen"
ATTESTATION_PATH = FROZEN_ROOT + "/attestation.json"
EVALUATION_CODE = ("research/prospective_props/runner.py", "research/prospective_props/review.py", "research/engine/scoring.py")
FROZEN_FILES = ("recipe.json", "parameters.json", "parameters.pkl", "prepared.json", "population.json",
                "fp-prospective-1.forecasts.json.gz", "fp-prospective-2.forecasts.json.gz")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path = Path(path)
    with path.open("x") as handle:
        json.dump(value, handle, sort_keys=True, indent=2, allow_nan=False)
        handle.write("\n")


def new_directory(path):
    path = Path(path).resolve()
    path.mkdir(parents=True, exist_ok=False)
    return path


def attest(repo):
    """Create the publication manifest after governance-only changes, no scores."""
    repo = Path(repo).resolve()
    public_paths = (*EVALUATION_CODE, REGISTRATION_PATH, PRIVATE_EXECUTION_PATH)
    private_paths = tuple(FROZEN_ROOT + "/" + name for name in FROZEN_FILES)
    paths = (*public_paths, *private_paths)
    prepared = json.loads((repo / FROZEN_ROOT / "prepared.json").read_text())
    attestation = {"schema": "prospective-props-published-freeze-v1",
                   "files": {path: digest(repo / path) for path in paths},
                   "public_files": sorted(public_paths), "private_files": sorted(private_paths),
                   "artifact_visibility": EVIDENCE_VISIBILITY,
                   "publication_approval_reason": PUBLICATION_APPROVAL_REASON,
                   "generation_runner_sha256": prepared["implementation_sha256"],
                   "note": "Post-preparation changes only harden publication, one-run receipts and verification; forecast and parameter bytes are unchanged."}
    write_json(repo / ATTESTATION_PATH, attestation)
    return attestation


def verify_published_freeze(repo, prepared_path, freeze_commit, private_seal_path=None):
    """Validate public code and the privately saved immutable evidence snapshot.

    The orchestrator saves the ZIP to the user's private Library and supplies
    its returned file/version identifiers in the private seal. This offline
    verifier checks snapshot bytes and publication code, not Library credentials.
    Neither the evidence contents nor their hashes are requested from Git.
    """
    repo, prepared_path = Path(repo).resolve(), Path(prepared_path).resolve()
    if prepared_path != repo / FROZEN_ROOT or not re.fullmatch(r"[0-9a-f]{40}", freeze_commit or ""):
        raise ValueError("Use the published evidence directory and full freeze commit")
    if private_seal_path is None:
        raise ValueError("An authenticated private Library save and its seal are required")
    seal_path = Path(private_seal_path)
    seal = json.loads(seal_path.read_text())
    if seal.get("schema") != "prospective-props-private-seal-v1" or any(
            not isinstance(seal.get(key), str) or not seal[key].strip() for key in ("library_file_id", "library_version_id", "bundle_path")):
        raise ValueError("Incomplete private Library seal")
    for key in ("bundle_sha256", "attestation_sha256"):
        if not re.fullmatch(r"[0-9a-f]{64}", seal.get(key, "")):
            raise ValueError("Invalid private snapshot checksum")
    bundle = Path(seal["bundle_path"])
    if not bundle.is_absolute():
        bundle = seal_path.parent / bundle
    if digest(bundle) != seal["bundle_sha256"]:
        raise ValueError("Private Library snapshot bytes changed")
    def committed(path):
        return subprocess.check_output(["git", "show", f"{freeze_commit}:{path}"], cwd=repo)
    with zipfile.ZipFile(bundle) as archive:
        if any(Path(name).is_absolute() or ".." in Path(name).parts for name in archive.namelist()):
            raise ValueError("Unsafe private snapshot member")
        raw = archive.read(ATTESTATION_PATH)
        snapshot = {name: archive.read(name) for name in json.loads(raw).get("files", {})}
    if hashlib.sha256(raw).hexdigest() != seal["attestation_sha256"] or (repo / ATTESTATION_PATH).read_bytes() != raw:
        raise ValueError("Private attestation differs from sealed Library snapshot")
    attestation = json.loads(raw)
    public_paths = set((*EVALUATION_CODE, REGISTRATION_PATH, PRIVATE_EXECUTION_PATH))
    private_paths = {FROZEN_ROOT + "/" + name for name in FROZEN_FILES}
    required = public_paths | private_paths
    if (attestation.get("schema") != "prospective-props-published-freeze-v1" or set(attestation.get("files", {})) != required or
            set(attestation.get("public_files", [])) != public_paths or set(attestation.get("private_files", [])) != private_paths or
            attestation.get("artifact_visibility") != EVIDENCE_VISIBILITY or not attestation.get("publication_approval_reason")):
        raise ValueError("Incomplete or unexpected published freeze manifest")
    for path, expected in attestation["files"].items():
        if (not re.fullmatch(r"[0-9a-f]{64}", expected) or path not in snapshot or hashlib.sha256(snapshot[path]).hexdigest() != expected or digest(repo / path) != expected or
                path in public_paths and hashlib.sha256(committed(path)).hexdigest() != expected):
            raise ValueError(f"Published/local frozen artifact changed: {path}")
    prepared = json.loads((prepared_path / "prepared.json").read_text())
    recipe = json.loads((prepared_path / "recipe.json").read_text())
    population = json.loads((prepared_path / "population.json").read_text())
    from research.prospective_props.review import validate_population
    validate_population(population)
    if prepared["population"] != population or prepared["recipe_sha256"] != digest(prepared_path / "recipe.json"):
        raise ValueError("Prepared recipe or population differs from published artifacts")
    if recipe["parameters_sha256"] != digest(prepared_path / "parameters.pkl") or recipe["parameters_json_sha256"] != digest(prepared_path / "parameters.json"):
        raise ValueError("Published parameter hash mismatch")
    if set(prepared["forecasts"]) != {"fp-prospective-1", "fp-prospective-2"}:
        raise ValueError("Exactly the two registered arms are required")
    for arm, forecast in prepared["forecasts"].items():
        if forecast["path"] != f"{arm}.forecasts.json.gz" or forecast["sha256"] != digest(prepared_path / forecast["path"]):
            raise ValueError("Published forecast path/hash mismatch")
    return prepared, hashlib.sha256(raw).hexdigest(), hashlib.sha256(committed(REGISTRATION_PATH)).hexdigest()


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


def materialize(repo, directory):
    sources = directory / "src"
    sources.mkdir()
    manifest = {}
    for name in MODULES:
        pin = ARM2 if name in ("talent", "fp_model") else ARM1
        contents = subprocess.check_output(["git", "show", f"{pin}:wnba/src/{name}.py"], cwd=repo)
        path = sources / f"{name}.py"
        path.write_bytes(contents)
        manifest[path.name] = {"commit": pin, "sha256": digest(path)}
    for name, pin in (("fp_model_arm1", ARM1), ("features_clean", ASG)):
        original = "fp_model" if name.startswith("fp_model") else "features"
        path = sources / f"{name}.py"
        path.write_bytes(subprocess.check_output(["git", "show", f"{pin}:wnba/src/{original}.py"], cwd=repo))
        manifest[path.name] = {"commit": pin, "sha256": digest(path)}
    sys.path.insert(0, str(sources))
    return manifest


def link_sources(directory, source_directories, *, allow_2026):
    destination = directory / "data" / "wehoop"
    destination.mkdir(parents=True)
    manifest = {}
    for source in source_directories:
        for path in sorted(Path(source).glob("*.parquet")):
            if not path.name.startswith(("player_box_", "team_box_", "wnba_schedule_")):
                continue
            year = int(path.stem[-4:])
            if year > (2026 if allow_2026 else 2025):
                continue
            if path.name in manifest:
                raise ValueError(f"Duplicate source: {path.name}")
            (destination / path.name).symlink_to(path.resolve())
            manifest[path.name] = {"sha256": digest(path), "bytes": path.stat().st_size, "source_path": str(path.resolve())}
    for prefix in ("player_box", "team_box", "wnba_schedule"):
        expected = range(2003, 2027 if allow_2026 else 2026)
        missing = [year for year in expected if f"{prefix}_{year}.parquet" not in manifest]
        if missing:
            raise ValueError(f"Missing {prefix} years: {missing}")
    return manifest


def build_panel(directory, *, clean=False):
    name = "features_clean" if clean else "features"
    features = module(directory / "src" / f"{name}.py", name)
    return features.build_panel(features.load_player_box(), features.load_team_box())


def talent_frame(talent, panel, parameters):
    played = talent.load_played(panel)
    out = played[["athlete_id", "game_id"]].copy()
    for stat in talent.RAW:
        q, p0 = parameters["best"][stat]
        out[f"talent_{stat}"] = talent.run_filter(played, parameters["curves"], parameters["rvar"], q, p0, stat)
    return out


def freeze(repo, historical_sources, output):
    """The only fitting stage; the loader cannot include a 2026 file."""
    directory = new_directory(output)
    implementations = materialize(repo, directory)
    inputs = link_sources(directory, [historical_sources], allow_2026=False)
    panel = build_panel(directory)
    if not (pd.to_datetime(panel.game_date) < "2026-01-01").all():
        raise ValueError("Protected year in calibration panel")
    print(f"Pre-2026 original panel: {len(panel)} played rows", flush=True)
    arm1 = module(directory / "src" / "fp_model_arm1.py", "fp_model_arm1")
    arm2 = module(directory / "src" / "fp_model.py", "fp_model")
    talent = module(directory / "src" / "talent.py", "talent")
    played = talent.load_played(panel)
    training = played[played.game_date < "2025-01-01"].copy()
    curves, rvar = talent.fit_curves(training, "2025-01-01"), talent.fit_rvar(training, "2025-01-01")
    best = {}
    for stat in talent.RAW:
        best_mse, best_pair = math.inf, None
        for q in talent.GRID_Q:
            for p0 in talent.GRID_P0:
                pred = talent.run_filter(training, curves, rvar, q, p0, stat)
                mse = talent.one_step_mse(training, pred, stat, "2005-01-01", "2025-01-01")
                if mse < best_mse:
                    best_mse, best_pair = mse, (q, p0)
        if best_pair is None:
            raise ValueError("Pinned talent tuning has no finite candidate")
        best[stat] = best_pair
        print(f"Frozen {stat} talent setting {best_pair} from pre-2025 rows", flush=True)
    parameters = {"curves": curves, "rvar": rvar, "best": best}
    talent_states = talent_frame(talent, panel, parameters)
    parameters["calibration_arm1"] = arm1.fit_play_cal(panel, "2026-01-01")
    parameters["calibration_arm2"] = arm2.fit_play_cal(panel.merge(talent_states, on=["athlete_id", "game_id"], how="left"), "2026-01-01")
    with (directory / "parameters.pkl").open("xb") as handle:
        pickle.dump(parameters, handle, protocol=4)
    # JSON audit copy of every fitted scalar and career curve; pickle is local
    # trusted state only and is hash-checked before subsequent loading.
    json_parameters = {**parameters, "curves": {f"{pos}|{stat}": [base, increments.tolist()]
                        for (pos, stat), (base, increments) in curves.items()}}
    write_json(directory / "parameters.json", json_parameters)
    recipe = {"schema": "prospective-props-freeze-v1", "arm1_commit": ARM1, "arm2_commit": ARM2,
              "asg_commit": ASG, "inputs": inputs, "pinned_implementations": implementations,
              "runner_sha256": digest(__file__), "parameters_sha256": digest(directory / "parameters.pkl"),
              "parameters_json_sha256": digest(directory / "parameters.json"),
              "calibration_before": "2026-01-01", "talent_fit_before": "2025-01-01",
              "calibration_panel_rows": len(panel), "talent_training_rows": len(training),
              "original_forecast_bytes_recovered": False}
    write_json(directory / "recipe.json", recipe)
    return recipe


def endpoint_population(modelset, minimum=3000):
    """Count original eligibility only, without reading any model probability."""
    population = modelset[modelset.matched & ~modelset.void & modelset.open_coherent &
                          (modelset.actual != modelset.open_line) & (modelset.date >= "2026-08-01")].copy()
    counts = population.groupby("date", sort=True).size()
    cumulative = counts.cumsum()
    reached = cumulative[cumulative >= minimum]
    if reached.empty:
        raise ValueError("Prospective n threshold has not been reached")
    endpoint = str(reached.index[0])
    cohort = population[population.date <= endpoint].copy()
    info = {"endpoint_date": endpoint, "endpoint_n": len(cohort), "minimum": minimum,
            "endpoint_rule": "entire_first_ET_date_reaching_threshold", "available_eligible_n": len(population),
            "daily_counts": [{"date": str(day), "n": int(counts[day]), "cumulative": int(cumulative[day])} for day in counts.index]}
    return cohort, info


def _prepare_archive(repo, directory):
    """Read the same sorted native archive, bounded to eligible calendar dates."""
    build = module(directory / "src" / "build_props.py", "build_props")
    build.RAW = str(Path(repo) / "wnba" / "data" / "raw" / "bp")
    events = build.parse_events()
    selected = events[(events.season == 2026) & (events.date >= "2026-08-01")]
    ids, rows, inputs = set(selected.event_id), [], {}
    for path in sorted((Path(build.RAW) / "offers").glob("*.json.gz")):
        event, market = map(int, path.name.replace(".json.gz", "").split("_"))
        if event in ids and market in build.PROP_MARKETS:
            build.parse_offer_file(str(path), build.PROP_MARKETS[market], rows)
            inputs[str(path.relative_to(repo))] = digest(path)
    for path in sorted(Path(build.RAW).glob("events_*.json.gz")):
        inputs[str(path.relative_to(repo))] = digest(path)
    props = pd.DataFrame(rows).join(events.set_index("event_id")[["season", "date", "home", "visitor"]], on="event_id")
    props.to_pickle(directory / "data" / "props.pkl")
    grader = module(directory / "src" / "grade_props.py", "grade_props")
    with redirect_stdout(io.StringIO()):
        grader.main()
    return inputs


def _native_modelset(directory, panel):
    panel.to_pickle(directory / "data" / "panel.pkl")
    builder = module(directory / "src" / "build_modelset.py", "build_modelset")
    with redirect_stdout(io.StringIO()):
        builder.main()
    return pd.read_pickle(directory / "data" / "modelset.pkl")


def _attach_talent(directory, panel, modelset, parameters):
    talent = module(directory / "src" / "talent.py", "talent")
    builder = module(directory / "src" / "build_modelset.py", "build_modelset")
    states = talent_frame(talent, panel, parameters)
    merged = panel.merge(states, on=["athlete_id", "game_id"], how="left")
    tmap = merged.assign(nname=merged.athlete_display_name.map(builder.norm),
                         dstr=pd.to_datetime(merged.game_date).dt.strftime("%Y-%m-%d"))
    columns = [column for column in states if column.startswith("talent_")]
    tmap = tmap.groupby(["nname", "dstr"])[columns].max().reset_index()
    return modelset.assign(dstr=pd.to_datetime(modelset.date).dt.strftime("%Y-%m-%d")).merge(tmap, on=["nname", "dstr"], how="left")


def identity_audit(directory, cohort):
    """Count legacy grading identity weaknesses without repairing its cohort."""
    grader = module(directory / "src" / "grade_props.py", "grade_props")
    box = grader.load_box()
    lookup = {(row.nname, row.date): row for row in box.itertuples()}
    offsets, mismatched_teams = {"0": 0, "-1": 0, "1": 0}, 0
    for row in cohort.itertuples():
        for offset in (0, -1, 1):
            day = str((pd.Timestamp(row.date) + pd.Timedelta(days=offset)).date())
            source = lookup.get((grader.norm(row.player), day))
            if source is not None:
                offsets[str(offset)] += 1
                expected = grader.BP2WH.get(row.team, row.team)
                mismatched_teams += int(expected != source.team_abbreviation)
                break
        else:
            raise ValueError("Native matched cohort cannot reproduce name/date lookup")
    return {"grade_source_day_offsets": offsets, "grade_source_team_mismatches": mismatched_teams,
            "nonfinite_actual_counts": int((~np.isfinite(cohort.actual.to_numpy(float))).sum()),
            "policy": "diagnostic_only_original_matching_preserved"}


def prepare(repo, historical_sources, current_sources, frozen, output):
    """Save forecasts but never calculate protected probability scores or ROI."""
    frozen = Path(frozen)
    recipe = json.loads((frozen / "recipe.json").read_text())
    if digest(frozen / "parameters.pkl") != recipe["parameters_sha256"]:
        raise ValueError("Frozen parameters changed")
    with (frozen / "parameters.pkl").open("rb") as handle:
        parameters = pickle.load(handle)
    directory = new_directory(output)
    implementations = materialize(repo, directory)
    if implementations != recipe["pinned_implementations"]:
        raise ValueError("Pinned source bytes differ from fitted recipe")
    inputs = link_sources(directory, [historical_sources, current_sources], allow_2026=True)
    for name, original in recipe["inputs"].items():
        if inputs[name]["sha256"] != original["sha256"]:
            raise ValueError("Historical input changed after calibration freeze")
    archive_inputs = _prepare_archive(repo, directory)
    panels = {"original": build_panel(directory), "clean": build_panel(directory, clean=True)}
    modelsets = {name: _native_modelset(directory, panel) for name, panel in panels.items()}
    cohort, population = endpoint_population(modelsets["original"])
    clean_cohort, clean_population = endpoint_population(modelsets["clean"])
    if population != clean_population:
        raise ValueError("ASG feature repair altered eligibility")
    key = ["event_id", "market", "player"]
    if cohort[key].to_dict("records") != clean_cohort[key].to_dict("records"):
        raise ValueError("ASG cohorts are not identical")
    # Eligibility is frozen before prediction. Whole-date splitting preserves
    # the native defective daily offered-row normalization exactly.
    arms = {"fp-prospective-1": module(directory / "src" / "fp_model_arm1.py", "fp_model_arm1"),
            "fp-prospective-2": module(directory / "src" / "fp_model.py", "fp_model")}
    rows_by_arm = {}
    for arm, native in arms.items():
        parts = []
        for name, sub in (("original", cohort[cohort.date <= "2026-08-03"].copy()),
                          ("clean", clean_cohort[clean_cohort.date >= "2026-08-04"].copy())):
            if arm == "fp-prospective-2":
                sub = _attach_talent(directory, panels[name], sub, parameters)
            cal = parameters["calibration_arm1" if arm == "fp-prospective-1" else "calibration_arm2"]
            sub["mu_model"] = native.predict(sub, cal)
            if sub.mu_model.isna().any():
                raise ValueError("Pinned model lacks forecasts for eligible rows; investigate before scoring")
            sub["p_model"] = [native.p_over(market, mean, line, cal) for market, mean, line in zip(sub.market, sub.mu_model, sub.open_line)]
            sub["panel_segment"] = name
            parts.append(sub)
        columns = ["event_id", "date", "market", "player", "open_line", "open_over_cost", "open_under_cost",
                   "open_book", "open_created", "p_open", "actual", "p_model", "mu_model", "p_close", "coh_close",
                   "line_close", "panel_segment"]
        rows = pd.concat(parts).sort_values(["date", *key], kind="stable")[columns]
        rows_by_arm[arm] = json.loads(rows.to_json(orient="records", date_format="iso"))
        payload = json.dumps(rows_by_arm[arm], sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        (directory / f"{arm}.forecasts.json.gz").write_bytes(gzip.compress(payload, mtime=0))
    write_json(directory / "population.json", population)
    prepared = {"schema": "prospective-props-prepared-v1", "population": population,
                "identity_audit": identity_audit(directory, cohort),
                "recipe_sha256": digest(frozen / "recipe.json"), "parameters_sha256": recipe["parameters_sha256"],
                "implementation_sha256": digest(__file__), "inputs": inputs, "archive_inputs": archive_inputs,
                "pinned_implementations": implementations,
                "forecasts": {arm: {"path": f"{arm}.forecasts.json.gz", "sha256": digest(directory / f"{arm}.forecasts.json.gz")} for arm in arms},
                "original_forecast_bytes_recovered": False,
                "probability_scores_computed": False}
    write_json(directory / "prepared.json", prepared)
    return prepared


def registered_cluster(values, dates):
    """Exact native fp_benchmark clustered mean/t, including its small-n convention."""
    if not len(values):
        return {"estimate": None, "registered_t": None}
    grouped = pd.DataFrame({"d": values, "date": dates}).groupby("date")["d"]
    means, sizes = grouped.mean(), grouped.size()
    weights = sizes / sizes.sum()
    mean = float((means * weights).sum())
    variance = float(((means - mean) ** 2 * weights ** 2).sum())
    return {"estimate": mean, "registered_t": mean / math.sqrt(variance) if variance > 0 else None}


def _interval(values, dates):
    from research.engine.scoring import date_bootstrap
    return {**date_bootstrap(dates, values), **registered_cluster(values, dates)}


def _loss(probability, outcome):
    probability = np.clip(np.asarray(probability, float), 1e-9, 1 - 1e-9)
    return -(outcome * np.log(probability) + (1 - outcome) * np.log(1 - probability))


def native_economics(rows, probability):
    """Original all-props selection and binary EV; intentionally no new repairs."""
    if rows.empty:
        return {"bets": 0, "profit": 0., "stake": 0., "roi": None, "registered_t": None, "ci95": None, "selections": []}
    cost_over, cost_under = rows.open_over_cost.to_numpy(float), rows.open_under_cost.to_numpy(float)
    dec_over = np.where(cost_over > 0, 1 + cost_over / 100, 1 + 100 / np.maximum(-cost_over, 1e-9))
    dec_under = np.where(cost_under > 0, 1 + cost_under / 100, 1 + 100 / np.maximum(-cost_under, 1e-9))
    ev_over, ev_under = probability * dec_over - 1, (1 - probability) * dec_under - 1
    over = ev_over >= ev_under
    edges = np.maximum(ev_over, ev_under)
    take = edges > .05
    outcome = rows.actual.to_numpy(float) > rows.open_line.to_numpy(float)
    profit = np.where(over == outcome, np.where(over, dec_over, dec_under) - 1, -1.)
    selected = []
    for index in np.flatnonzero(take):
        row = rows.iloc[index]
        selected.append({"event_id": int(row.event_id), "market": row.market, "player": row.player,
                         "date": row.date, "side": "over" if over[index] else "under", "claimed_ev": float(edges[index]),
                         "stake": 1., "refund": 0., "profit": float(profit[index])})
    interval = _interval(profit[take], rows.date.to_numpy()[take])
    return {"bets": int(take.sum()), "profit": float(profit[take].sum()), "stake": float(take.sum()),
            "roi": interval["estimate"], "registered_t": interval["registered_t"], "ci95": interval["ci95"], "selections": selected}


def score_segment(rows):
    frame = pd.DataFrame(rows)
    if frame.empty:
        frame = pd.DataFrame(columns=["date", "actual", "open_line", "p_model", "p_open", "p_close", "coh_close", "line_close"])
    y = (frame.actual.to_numpy(float) > frame.open_line.to_numpy(float)).astype(float)
    model, opener = _loss(frame.p_model, y), _loss(frame.p_open, y)
    close_mask = frame.coh_close.fillna(False).astype(bool) & frame.line_close.eq(frame.open_line) & frame.p_close.notna()
    close = _loss(frame.loc[close_mask, "p_close"], y[close_mask])
    return {"n": len(frame), "log_loss_model": float(model.mean()) if len(frame) else None,
            "log_loss_opener": float(opener.mean()) if len(frame) else None,
            "model_minus_opener": _interval(model - opener, frame.date),
            "model_minus_close": {"n": int(close_mask.sum()), **_interval(model[close_mask] - close, frame.loc[close_mask, "date"])},
            "calibration_gap": float(frame.p_model.mean() - y.mean()) if len(frame) else None,
            "economics": {"candidate": native_economics(frame, frame.p_model.to_numpy(float)),
                          "placebo": native_economics(frame, frame.p_open.to_numpy(float))}}


def score(prepared_path, output, registration_commit, repo, *, freeze_commit=None, private_seal_path=None, reproduction_of=None):
    from research.prospective_props.review import render
    prepared_path = Path(prepared_path)
    prepared, attestation_sha256, frozen_registration_sha256 = verify_published_freeze(repo, prepared_path, freeze_commit, private_seal_path)
    seal = json.loads(Path(private_seal_path).read_text())
    registration = subprocess.check_output(["git", "show", f"{registration_commit}:{REGISTRATION_PATH}"], cwd=repo)
    if hashlib.sha256(registration).hexdigest() != frozen_registration_sha256:
        raise ValueError("Registered amendment differs from the published freeze")
    directory = new_directory(output)
    if reproduction_of is None:
        # Reserve the sole primary evaluation before reading any probabilities.
        # A failure retains the claim and must be investigated explicitly.
        write_json(prepared_path / "evaluation_claim.json", {
            "registration_commit": registration_commit, "output": str(directory),
            "prepared_sha256": digest(prepared_path / "prepared.json")})
    rows_by_arm, identities = {}, None
    for arm, forecast in prepared["forecasts"].items():
        path = prepared_path / forecast["path"]
        if digest(path) != forecast["sha256"]:
            raise ValueError("Frozen forecast hash mismatch")
        rows = json.loads(gzip.decompress(path.read_bytes()))
        current_identities = [(row["date"], row["event_id"], row["market"], row["player"]) for row in rows]
        if len(rows) != prepared["population"]["endpoint_n"] or len(set(current_identities)) != len(rows) or (identities is not None and current_identities != identities):
            raise ValueError("Frozen arm forecast populations differ")
        days = pd.Series([row["date"] for row in rows]).value_counts().to_dict()
        expected_days = {row["date"]: row["n"] for row in prepared["population"]["daily_counts"] if row["date"] <= prepared["population"]["endpoint_date"]}
        if days != expected_days:
            raise ValueError("Frozen forecasts differ from endpoint daily populations")
        identities, rows_by_arm[arm] = current_identities, rows
    for arm, rows in rows_by_arm.items():
        forecast = prepared["forecasts"][arm]
        results = {"schema": "prospective-props-reconstruction-v1", "arm": arm,
                   "artifact_visibility": EVIDENCE_VISIBILITY,
                   "original_forecast_bytes_recovered": False, "population": prepared["population"],
                   "identity_audit": prepared.get("identity_audit", {}),
                   "segments": {"overall": score_segment(rows),
                                "august_1_3": score_segment([r for r in rows if r["date"] <= "2026-08-03"]),
                                "august_4_onward": score_segment([r for r in rows if r["date"] >= "2026-08-04"])}}
        result_path = directory / f"{arm}.results.json"
        write_json(result_path, results)
        receipt = {"status": "complete", "arm": arm, "registration_commit": registration_commit,
                   "artifact_visibility": EVIDENCE_VISIBILITY, "publication_approval_reason": PUBLICATION_APPROVAL_REASON,
                   "registration_sha256": hashlib.sha256(registration).hexdigest(),
                   "recipe_sha256": prepared["recipe_sha256"], "forecasts_sha256": forecast["sha256"],
                   "implementation_sha256": digest(__file__), "generation_implementation_sha256": prepared["implementation_sha256"],
                   "freeze_commit": freeze_commit, "attestation_sha256": attestation_sha256,
                   "freeze_location": "private_user_library", "private_seal_sha256": digest(private_seal_path),
                   "library_file_id": seal["library_file_id"], "library_version_id": seal["library_version_id"],
                   "results_sha256": digest(result_path),
                   "completed_at": datetime.now(timezone.utc).isoformat(), "reconstructed": True}
        write_json(directory / f"{arm}.receipt.json", receipt)
        (directory / f"{arm}.decision.md").write_text(render(results, receipt, results_bytes=result_path.read_bytes()))
        if reproduction_of is not None:
            original = Path(reproduction_of)
            original_receipt = json.loads((original / f"{arm}.receipt.json").read_text())
            if (original_receipt["forecasts_sha256"] != forecast["sha256"] or
                    original_receipt["implementation_sha256"] != digest(__file__) or
                    original_receipt["freeze_commit"] != freeze_commit or
                    digest(original / f"{arm}.results.json") != original_receipt["results_sha256"] or
                    original_receipt["results_sha256"] != receipt["results_sha256"]):
                raise ValueError("Saved prospective result fails independent arithmetic reproduction")
    return {"status": "complete", "arms": list(prepared["forecasts"])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("freeze", "prepare", "attest", "score", "verify"))
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--historical-sources", type=Path)
    parser.add_argument("--current-sources", type=Path)
    parser.add_argument("--frozen", type=Path)
    parser.add_argument("--prepared", type=Path)
    parser.add_argument("--registration-commit")
    parser.add_argument("--freeze-commit")
    parser.add_argument("--private-seal", type=Path)
    parser.add_argument("--reproduction-of", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.command != "attest" and args.output is None:
        parser.error("--output is required")
    if args.command == "attest":
        result = attest(args.repo)
    elif args.command == "freeze":
        result = freeze(args.repo, args.historical_sources, args.output)
    elif args.command == "prepare":
        result = prepare(args.repo, args.historical_sources, args.current_sources, args.frozen, args.output)
    else:
        if args.command == "verify" and args.reproduction_of is None:
            parser.error("verify requires --reproduction-of")
        result = score(args.prepared, args.output, args.registration_commit, args.repo,
                       freeze_commit=args.freeze_commit,
                       private_seal_path=args.private_seal,
                       reproduction_of=args.reproduction_of if args.command == "verify" else None)
    print(json.dumps({key: result[key] for key in ("schema", "status", "population", "parameters_sha256") if key in result}, sort_keys=True))


if __name__ == "__main__":
    main()
