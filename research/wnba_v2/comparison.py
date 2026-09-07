"""One registered source repair and fixed variance/count comparison, pre-2025."""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import json
import math
from pathlib import Path
from time import perf_counter

import numpy as np
import pandas as pd

from research.diagnostics import rate_uncertainty as previous
from research.diagnostics.structural_failure import digest, registration_identity
from research.engine import model as original
from research.engine.store import ObservationKind, payload_digest
from . import model

MARKETS = model.MARKETS
BUDGET_SECONDS = 600


def independent_state(records, recipe):
    """Scalar implementation, independent of the production state updater."""
    p = recipe["priors"]
    result = {"rates": list(p["rates"]["all"]),
              "variance": [v / (60 * recipe["shrinkage"]) for v in p["rate_rvar"]],
              "n": 0, "played": 0, "minute_measured": 0, "unknown_participation": 0,
              "unmeasured_played": 0, "fast": p["minutes_mean"], "slow": p["minutes_mean"],
              "second": p["minutes_mean"] ** 2 + p["minutes_variance"],
              "dnp": p["p_dnp"], "starter": .5, "season": None, "season_sum": 0.,
              "season_n": 0, "prior_season": p["minutes_mean"], "last_tip": None}
    for row in sorted(records, key=lambda o: (o.available_at, o.effective_at, o.source_id, o.record_id)):
        q = row.payload
        if result["season"] is not None and result["season"] != row.season:
            if result["season_n"]:
                result["prior_season"] = result["season_sum"] / result["season_n"]
            result["season_sum"], result["season_n"] = 0., 0
            result["variance"] = [v + .003 * r for v, r in zip(result["variance"], p["rate_rvar"])]
        result["season"] = row.season
        result["n"] += 1
        result["last_tip"] = row.effective_at
        status = q["participation"]
        if status == "unknown":
            result["unknown_participation"] += 1
            continue
        result["dnp"] = .18 * (status == "dnp") + .82 * result["dnp"]
        result["starter"] = .18 * bool(q.get("starter")) + .82 * result["starter"]
        if status == "dnp":
            continue
        if result["played"] == 0:
            position = str(q.get("position") or "unknown")[0].upper()
            result["rates"] = list(p["rates"].get(position, p["rates"]["all"]))
        result["played"] += 1
        result["variance"] = [v + .0003 * r for v, r in zip(result["variance"], p["rate_rvar"])]
        if q["minute_measurement_eligible"]:
            m = q["minutes"]
            result["minute_measured"] += 1
            result["fast"] = .18 * m + .82 * result["fast"]
            result["slow"] = .05 * m + .95 * result["slow"]
            result["second"] = .18 * m * m + .82 * result["second"]
            result["season_sum"] += m
            result["season_n"] += 1
        else:
            result["unmeasured_played"] += 1
        if q["rate_measurement_eligible"]:
            e = q["minutes"] * q["possessions_estimate"] / q["duration_minutes"]
            for i, market in enumerate(MARKETS):
                gain = result["variance"][i] / (result["variance"][i] + p["rate_rvar"][i] / e)
                result["rates"][i] = max(1e-4, result["rates"][i] + gain * (q["counts"][market] / e - result["rates"][i]))
                result["variance"][i] *= 1 - gain
    return result


def seed_configuration(recipe):
    return model.configuration(recipe, {m: {"F_total": 1., "F_noise": 1.} for m in MARKETS})


def rate_checkpoint(engine, request, outcome):
    previous.require(2015 <= request.season <= 2024 and request.tip_at.year <= 2024,
                     "Development may not open 2025/2026")
    history = [o for o in engine.index.select_entity(request.entity_id, request)
               if o.payload.get("record_type") == "player_box" and o.kind is ObservationKind.HISTORICAL_OUTCOME]
    for row in history:
        previous.require(row.available_at < request.as_of and row.effective_at < request.as_of and
                         row.event_id != request.event_id and row.season <= 2024, "Future/target source in state")
    state = engine._player_state(request.entity_id, history)
    p = outcome.payload
    record = {"season": request.season, "game_id": request.event_id, "player_id": request.entity_id,
              "source_record_id": outcome.record_id, "as_of": request.as_of.isoformat(),
              "tip_at": request.tip_at.isoformat(), "rate_mean": state["rates"].tolist(),
              "rate_variance": state["variance"].tolist(), "actual_exposure": model.measured_exposure(p),
              "actual_minutes": model.measured_minutes(p), "actual_counts": [p["counts"].get(m) for m in MARKETS],
              "participation": p["participation"], "rate_measurement_eligible": p["rate_measurement_eligible"],
              "minute_measurement_eligible": p["minute_measurement_eligible"],
              "dnp": p["participation"] == "dnp", "history_rows": len(history)}
    return record, history, state


def checkpoints(observations, recipe, years, deadline):
    engine = model.ParticipationModel(observations, seed_configuration(recipe))
    rows = []
    for year in years:
        for request, outcome in original.training_examples(observations, year):
            previous.check_time(deadline)
            rows.append(rate_checkpoint(engine, request, outcome)[0])
    return rows


def innovation_rows(rows):
    return [row for row in rows if row["rate_measurement_eligible"]]


def freeze_scalars(rows, recipe):
    previous.require(all(2015 <= r["season"] <= 2022 for r in rows), "Calibration includes check years")
    result = {}
    for i, market in enumerate(MARKETS):
        a = previous.innovations(innovation_rows(rows), market)
        mu, residual, uncertainty = (a[:, i] for i in range(3))
        total = float(np.sum(residual ** 2) / np.sum(mu))
        noise = float((np.sum(residual ** 2) - np.sum(uncertainty)) / np.sum(mu))
        result[market] = {"n": len(a), "raw_total": total, "raw_noise": noise,
                          "F_total": max(1., total), "F_noise": max(1., noise),
                          "total_floor": total < 1, "noise_floor": noise < 1,
                          "original_Fano": recipe["count_fano"][i],
                          "sum_mu": float(mu.sum()), "sum_squared_residual": float((residual ** 2).sum()),
                          "sum_rate_uncertainty": float(uncertainty.sum())}
    return result


def raw_reconciler(data, competitive_games=None, *, omitted_player_keys=(), omitted_team_keys=()):
    """Independent raw-to-observation arithmetic, without the source adapter."""
    players = {}
    minutes_by_team = {}
    roster_by_team = {}
    for row in data.player_box.to_dict("records"):
        if competitive_games is not None and row["game_id"] not in competitive_games:
            continue
        key = row["game_id"], row["athlete_id"]
        roster_by_team.setdefault((row["game_id"], row["team_id"]), set())
        if row["athlete_id"] is not None and pd.notna(row["athlete_id"]):
            roster_by_team[(row["game_id"], row["team_id"])].add(row["athlete_id"])
        minutes = pd.to_numeric(row.get("minutes"), errors="coerce")
        if pd.notna(minutes):
            team = row["game_id"], row["team_id"]
            minutes_by_team[team] = minutes_by_team.get(team, 0.) + float(minutes)
        if key in omitted_player_keys:
            continue
        previous.require(key not in players, "Duplicate raw player identity")
        players[key] = row
    teams = {}
    for row in data.team_box.to_dict("records"):
        if competitive_games is not None and row["game_id"] not in competitive_games:
            continue
        key = row["game_id"], row["team_id"]
        if key in omitted_team_keys:
            continue
        previous.require(key not in teams, "Duplicate raw team identity")
        teams[key] = row
    schedules = {row["game_id"]: row for row in data.schedule.to_dict("records")}
    verified = set()

    def nullable(value):
        return None if value is None or pd.isna(value) else value.item() if isinstance(value, np.generic) else value

    def flag(value):
        return bool(value) if isinstance(value, (bool, np.bool_)) else None

    def team_values(game, team):
        raw, schedule = teams[(game, team)], schedules[game]
        period = nullable(schedule.get("status_period"))
        duration = float(5 * round(minutes_by_team.get((game, team), 0.) / 25)) if period is None or period < 4 else float(40 + 5 * (int(period) - 4))
        possessions = float(raw["field_goals_attempted"] + .44 * raw["free_throws_attempted"] -
                            raw["offensive_rebounds"] + raw["total_turnovers"])
        return {"team_id": team, "opponent_id": raw["opponent_team_id"],
                "possessions_estimate": possessions, "duration_minutes": duration}

    def validate(observation):
        identity = observation.source_id, observation.record_id, observation.payload_hash
        if identity in verified:
            return
        p, game = observation.payload, observation.event_id
        team = p["team_id"]
        previous.require(observation.season == schedules[game]["season"] == observation.effective_at.year,
                         "Observation season differs from schedule/event year")
        previous.require(observation.season == teams[(game, team)]["season"], "Raw team season differs")
        previous.require((game, team) not in omitted_team_keys, "Quarantined team key was consumed")
        if p["record_type"] == "player_box":
            previous.require((game, observation.entity_id) not in omitted_player_keys, "Quarantined player key was consumed")
        expected = team_values(game, team)
        for key, value in expected.items():
            previous.close(p[key], value, "Raw team measurement differs: " + key)
        if p["record_type"] == "team_box":
            previous.require(observation.entity_id == team, "Team entity differs from payload team")
            previous.close(p["points"], float(teams[(game, team)]["team_score"]), "Raw team score differs")
            previous.close(p["roster_count"], len(roster_by_team.get((game, team), set())), "Raw roster count differs")
        else:
            raw = players[(game, observation.entity_id)]
            previous.require(observation.season == raw["season"], "Raw player season differs")
            previous.close(raw["team_id"], team, "Raw player team differs")
            previous.close(raw["opponent_team_id"], p["opponent_id"], "Raw player opponent differs")
            minutes, dnp = nullable(raw.get("minutes")), flag(raw.get("did_not_play"))
            positive = minutes is not None and 0 < minutes <= 60
            status = "dnp" if dnp is True else "played" if dnp is False or positive else "unknown"
            expected_minutes = float(minutes) if status == "played" and positive else 0. if status == "dnp" else None
            expected_counts = {m: None if nullable(raw.get(column)) is None else float(raw[column]) for m, column in previous.sources.COUNTS.items()}
            previous.close(p["participation"], status, "Raw participation differs")
            previous.close(p["minutes"], expected_minutes, "Raw minutes differ")
            previous.close(dict(p["counts"]), expected_counts, "Raw counts differ")
            previous.close(p["did_not_play"], True if status == "dnp" else False if status == "played" else None, "Raw DNP differs")
            previous.close(p["starter"], flag(raw.get("starter")), "Raw starter differs")
            position = nullable(raw.get("athlete_position_abbreviation"))
            previous.close(p["position"], None if position is None else str(position), "Raw position differs")
            minute_eligible = status == "played" and positive
            rate_eligible = minute_eligible and all(v is not None for v in expected_counts.values())
            previous.close(p["minute_measurement_eligible"], minute_eligible, "Minute eligibility differs")
            previous.close(p["rate_measurement_eligible"], rate_eligible, "Rate eligibility differs")
        verified.add(identity)
    return validate


def quarantine_inventory(data, observations, *, enabled):
    """Identify only fully omitted legacy keys; never deduplicate a used row."""
    audit = data.quality["wnba_v2_source_audit"]
    raw_players, raw_teams = data.player_box.to_dict("records"), data.team_box.to_dict("records")
    previous.require(len(audit["player_rows"]) == len(raw_players) and len(audit["team_rows"]) == len(raw_teams),
                     "Full raw source audit is required")
    groups = {"player": {}, "team": {}}
    for kind, raw, rows, entity in (("player", raw_players, audit["player_rows"], "athlete_id"),
                                    ("team", raw_teams, audit["team_rows"], "team_id")):
        previous.require({r["source_row"] for r in rows} == set(range(len(raw))), "Raw audit ordinals are incomplete")
        for row in rows:
            original_row = raw[row["source_row"]]
            key = original_row["game_id"], original_row[entity]
            previous.require(key == (row["game_id"], row["player_id" if kind == "player" else "team_id"]), "Audit identity differs from raw row")
            previous.require(original_row["team_id"] == row["team_id"], "Audit team differs from raw row")
            groups[kind].setdefault(key, []).append((original_row["season"], row))
    allowed = {"player": set(), "team": set()}
    if enabled:
        for kind in allowed:
            for key, rows in groups[kind].items():
                if all(int(year) < 2015 and row["status"] == "omitted" for year, row in rows):
                    allowed[kind].add(key)
        # Omitted players still appear in raw roster counts and may contribute
        # to team-duration fallbacks. They are not fully quarantined when their
        # associated team context is admitted to the model.
        admitted_teams = {(o.event_id, o.entity_id) for o in observations if o.payload.get("record_type") == "team_box"}
        allowed["player"] = {key for key in allowed["player"] if all(
            (row["game_id"], row["team_id"]) not in admitted_teams for _, row in groups["player"][key])}
    for row in observations:
        kind = "player" if row.payload.get("record_type") == "player_box" else "team"
        key = row.event_id, row.entity_id
        previous.require(key not in allowed[kind], "Quarantined key appears in normalized observations")
        previous.require(key in groups[kind] and len(groups[kind][key]) == 1 and
                         groups[kind][key][0][1]["status"] == "included", "Included identity is ambiguous or absent from audit")
    expected = {(kind, key) for kind in groups for key, rows in groups[kind].items()
                if any(row["status"] == "included" for _, row in rows)}
    actual = [("player" if o.payload.get("record_type") == "player_box" else "team", (o.event_id, o.entity_id)) for o in observations]
    previous.require(len(actual) == len(set(actual)) and set(actual) == expected,
                     "Included raw audit and normalized observations are not a bijection")
    breakdown = {}
    for kind in groups:
        rows = [(year, row) for key in allowed[kind] for year, row in groups[kind][key]]
        breakdown[kind] = {str(year): {"rows": sum(y == year for y, _ in rows),
            "causes": dict(Counter(reason for y, row in rows if y == year
                                   for reason in row["omissions" if kind == "player" else "issues"]))}
            for year in sorted({year for year, _ in rows})}
    return {"enabled": enabled, "player_keys": sorted([list(k) for k in allowed["player"]]),
            "team_keys": sorted([list(k) for k in allowed["team"]]),
            "player_rows": sum(len(groups["player"][k]) for k in allowed["player"]),
            "team_rows": sum(len(groups["team"][k]) for k in allowed["team"]),
            "quarantined_keys_consumed": 0,
            "quarantined_player_team_lineage_used": 0,
            "by_season_and_cause": breakdown,
            "meaning": "Only all-omitted pre-2015 keys are quarantined; raw records and audit remain intact."}


def verify_admitted_sources(data, observations, deadline=float("inf"), *, quarantine_pre2015=False):
    """Source-only verification also reusable for the post-selection 2025 seed.

    This does not run predictions, estimate parameters or permit 2025 labels in
    the comparison. Its caller must enforce candidate selection before seed use.
    """
    for family in ("player_box", "team_box", "pbp", "schedule"):
        frame = getattr(data, family)
        if not frame.empty:
            previous.require("season" in frame, "Source family lacks explicit seasons")
            years = pd.to_numeric(frame.season, errors="raise")
            previous.require(years.le(2025).all() and years.ge(2003).all() and years.eq(np.floor(years)).all(),
                             "Source verification cannot inspect 2026 history")
    previous.require(all(o.season <= 2025 and o.effective_at.year <= 2025 for o in observations),
                     "Source verification cannot inspect 2026 history")
    previous.require(all(o.kind is ObservationKind.HISTORICAL_OUTCOME and
                         o.payload.get("record_type") in ("player_box", "team_box") for o in observations),
                     "Admitted source must be a historical player or team box")
    clocks = previous.independent_clocks(data)
    quarantine = quarantine_inventory(data, observations, enabled=quarantine_pre2015)
    omitted_players = {tuple(k) for k in quarantine["player_keys"]}
    omitted_teams = {tuple(k) for k in quarantine["team_keys"]}
    audit = data.quality["wnba_v2_source_audit"]
    # A modern competitive omission cannot become an implicit quarantine.
    hard_players = [r for r in audit["player_rows"] if r["game_id"] in clocks and r["status"] != "included" and
                    (r["game_id"], r["player_id"]) not in omitted_players]
    hard_teams = [r for r in audit["team_rows"] if r["game_id"] in clocks and r["issues"] and
                  (r["game_id"], r["team_id"]) not in omitted_teams]
    previous.require(not hard_players and not hard_teams, "Nonquarantined competitive source omissions or conflicts remain")
    validate = raw_reconciler(data, clocks, omitted_player_keys=omitted_players, omitted_team_keys=omitted_teams)
    checked = 0
    for row in observations:
        previous.check_time(deadline)
        previous.require(row.event_id in clocks and [row.effective_at, row.available_at] == clocks[row.event_id],
                         "Admitted source clock differs")
        validate(row)
        checked += 1
    return {"status": "PASS_UNDER_ASSUMPTION", "quarantine": quarantine,
            "admitted_records_reconciled": checked, "historical_timing": "assumed",
            "max_source_season": max((o.season for o in observations), default=None)}


def source_check(data, observations, sample, recipe, deadline, *, quarantine_pre2015=False):
    selected = {r["game_id"] for r in sample}
    clock = previous.independent_clocks(data)
    sides = {r["game_id"]: previous.scheduled_sides(r) for r in data.schedule.to_dict("records")}
    engine = model.ParticipationModel(observations, seed_configuration(recipe))
    try:
        admitted = verify_admitted_sources(data, observations, deadline, quarantine_pre2015=quarantine_pre2015)
    except ValueError as error:
        return {"status": "FAIL", "setup_failure": str(error),
                "meaning": "Admitted-source verification failed before scalar fitting"}
    omitted_players = {tuple(k) for k in admitted["quarantine"]["player_keys"]}
    omitted_teams = {tuple(k) for k in admitted["quarantine"]["team_keys"]}
    reconcile = raw_reconciler(data, clock, omitted_player_keys=omitted_players, omitted_team_keys=omitted_teams)
    team_outcomes = {(o.event_id, o.entity_id): o for o in observations if o.payload.get("record_type") == "team_box"}
    rows = []
    for year in (2023, 2024):
        for request, outcome in original.training_examples(observations, year):
            if request.event_id not in selected:
                continue
            previous.check_time(deadline)
            evidence = {"season": year, "game_id": request.event_id, "player_id": request.entity_id}
            try:
                previous.require(outcome.event_id in clock and
                                 [outcome.effective_at, outcome.available_at] == clock[outcome.event_id], "Target clock differs")
                previous.require({outcome.payload["team_id"], outcome.payload["opponent_id"]} == sides[outcome.event_id], "Source teams differ")
                reconcile(outcome)
                for team in sides[outcome.event_id]:
                    previous.require((outcome.event_id, team) in team_outcomes, "Target team source is missing")
                    team_row = team_outcomes[(outcome.event_id, team)]
                    previous.require([team_row.effective_at, team_row.available_at] == clock[outcome.event_id], "Target team clock differs")
                    reconcile(team_row)
                point, history, actual = rate_checkpoint(engine, request, outcome)
                independent = independent_state(history, recipe)
                for key, value in independent.items():
                    left = actual[key].tolist() if isinstance(actual[key], np.ndarray) else actual[key]
                    previous.close(left, value, "Independent state differs: " + key)
                for row in history:
                    previous.require([row.effective_at, row.available_at] == clock[row.event_id], "Historical clock differs")
                    reconcile(row)
                for team in (request.team_id, request.opponent_id):
                    for row in engine.index.select_entity(team, request):
                        previous.require([row.effective_at, row.available_at] == clock[row.event_id], "Historical team clock differs")
                        reconcile(row)
                evidence.update(status="PASS", checkpoint=point)
            except (ValueError, KeyError) as error:
                evidence.update(status="FAIL", reason=str(error))
            rows.append(evidence)
    audit = data.quality["wnba_v2_source_audit"]["player_rows"]
    indexed = {(r["game_id"], r["player_id"]): r for r in rows}
    sample_rows = []
    for row in audit:
        if row["game_id"] in selected:
            existing = indexed.get((row["game_id"], row["player_id"]))
            sample_rows.append(existing if existing is not None else dict(row, status="FAIL", reason="Requested raw row has no reconciled observation"))
    hard_causes = {"conflicting_player_identity", "invalid_player_identity", "player_schedule_season_conflict",
                   "explicit_dnp_conflict", "invalid_player_measurements", "player_team_opponent_conflict"}
    hard_conflicts = [r for r in audit if r["game_id"] in clock and hard_causes.intersection(r["omissions"]) and
                      (r["game_id"], r["player_id"]) not in omitted_players]
    hard_team_conflicts = [r for r in data.quality["wnba_v2_source_audit"]["team_rows"]
                           if r["game_id"] in clock and r["issues"] and (r["game_id"], r["team_id"]) not in omitted_teams]
    coverage = {}
    denominators = {}
    for year in range(2015, 2025):
        population = [r for r in audit if r["season"] == year]
        eligible = [r for r in population if r["game_id"] in clock]
        denominators[str(year)] = {"raw": len(population), "competitive": len(eligible),
             "included": sum(r["status"] == "included" for r in eligible),
             "omitted": sum(r["status"] != "included" for r in eligible),
             "played": sum(r["participation"] == "played" for r in eligible),
             "dnp": sum(r["participation"] == "dnp" for r in eligible),
             "unknown": sum(r["participation"] == "unknown" for r in eligible),
             "minute_eligible": sum(r["minute_measurement_eligible"] for r in eligible),
             "rate_eligible": sum(r["rate_measurement_eligible"] for r in eligible)}
        if year in (2023, 2024):
            requested = [r for r in sample_rows if r["season"] == year]
            usable = sum(r["status"] == "PASS" for r in requested)
            coverage[str(year)] = {"requested": len(requested), "usable": usable,
                                   "coverage": usable / len(requested) if requested else 0.}
    passed = (sample_rows and all(r["status"] == "PASS" for r in rows) and not hard_conflicts and not hard_team_conflicts and
              all(r["coverage"] >= .99 for r in coverage.values()))
    return {"status": "PASS_UNDER_ASSUMPTION" if passed else "FAIL", "coverage": coverage,
            "rows": sample_rows, "hard_conflicts": hard_conflicts, "hard_team_conflicts": hard_team_conflicts, "denominators": denominators,
            "admitted_source_verification": admitted,
            "independent_state_tolerance": 1e-10,
            "meaning": "State, identities and clocks reconcile; historical source availability is assumed."}


def actual_count_valid(payload, market):
    value = payload.get("counts", {}).get(market)
    return (isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and
            value >= 0 and int(value) == value)


def capped_control(forecast, recipe):
    result = deepcopy(forecast)
    for i, market in enumerate(MARKETS):
        component = result["count_components"][market]
        component["variances"] = (np.array(component["means"]) * recipe["count_fano"][i]).tolist()
    return result


def count_comparison(observations, recipe, scalars, year, deadline):
    engines = {name: model.ParticipationModel(observations, model.configuration(recipe, scalars, name))
               for name in ("constant", "rate")}
    rows, denominators = [], {"requested": 0, "played": 0, "dnp": 0, "unknown": 0,
                              "count_scored": 0, "invalid_counts": 0, "unknown_exposure_scored": 0,
                              "by_market": {m: {"scored": 0, "missing_or_invalid": 0, "unknown_exposure_scored": 0} for m in MARKETS}}
    distributions_valid = True
    for request, outcome in original.training_examples(observations, year):
        previous.check_time(deadline)
        denominators["requested"] += 1
        status = outcome.payload["participation"]
        denominators[status] += 1
        forecasts = {name: engine.predict(request, include_manifest=False) for name, engine in engines.items()}
        previous.close(forecasts["constant"]["rate_mean"], forecasts["rate"]["rate_mean"], "Controls have different rate state")
        previous.close(forecasts["constant"]["minutes_probs"], forecasts["rate"]["minutes_probs"], "Controls have different minute state")
        forecasts["old"] = capped_control(forecasts["constant"], recipe)
        record = {"season": year, "game_id": request.event_id, "player_id": request.entity_id,
                  "participation": status, "as_of": request.as_of.isoformat(),
                  "rate_measurement_eligible": outcome.payload["rate_measurement_eligible"],
                  "exposure_status": outcome.payload["exposure_status"],
                  "count_scores": {}, "participation_log_loss": None}
        if status != "unknown":
            p = forecasts["constant"]["p_dnp"]
            record["participation_log_loss"] = -math.log(p if status == "dnp" else 1 - p)
        if status == "played":
            valid = [m for m in MARKETS if actual_count_valid(outcome.payload, m)]
            unknown_exposure = outcome.payload["exposure_status"] != "observed_positive"
            denominators["count_scored"] += bool(valid)
            denominators["invalid_counts"] += len(valid) != len(MARKETS)
            denominators["unknown_exposure_scored"] += bool(valid) and unknown_exposure
            for market in MARKETS:
                if market not in valid:
                    denominators["by_market"][market]["missing_or_invalid"] += 1
                    continue
                denominators["by_market"][market]["scored"] += 1
                denominators["by_market"][market]["unknown_exposure_scored"] += unknown_exposure
                actual = outcome.payload["counts"][market]
                record["count_scores"][market] = {name: -model.count_log_probability(f, market, actual)
                                                   for name, f in forecasts.items()}
                # Distribution normalization is algebraic (normalized finite
                # weights and proper NB/Poisson components); also verify a
                # concrete under/push/over partition at the observed integer.
                for forecast in forecasts.values():
                    parts = model.price_forecast(forecast, market, actual)
                    distributions_valid &= all(math.isfinite(parts[k]) and 0 <= parts[k] <= 1 for k in ("p_over", "p_under", "p_push"))
                    distributions_valid &= math.isclose(sum(parts[k] for k in ("p_over", "p_under", "p_push")), 1, abs_tol=1e-10)
            record["actual_counts"] = {m: outcome.payload["counts"].get(m) for m in MARKETS}
        rows.append(record)
    summary = {}
    for market in MARKETS:
        values = [r["count_scores"][market] for r in rows if market in r["count_scores"]]
        previous.require(values, "No actual count grades")
        summary[market] = {"n": len(values), "mean_nll": {name: float(np.mean([v[name] for v in values])) for name in ("old", "constant", "rate")},
                           "rate_minus_constant_nll": float(np.mean([v["rate"] - v["constant"] for v in values])),
                           "constant_minus_old_nll": float(np.mean([v["constant"] - v["old"] for v in values]))}
        summary[market]["by_exposure"] = {}
        for eligible, label in ((True, "known_positive"), (False, "unknown")):
            subset = [r["count_scores"][market] for r in rows if market in r["count_scores"] and
                      (r["exposure_status"] == "observed_positive") == eligible]
            summary[market]["by_exposure"][label] = {"n": len(subset),
                "mean_nll": {name: float(np.mean([v[name] for v in subset])) if subset else None for name in ("old", "constant", "rate")}}
    previous.require(distributions_valid and all(math.isfinite(s) for r in rows for m in r["count_scores"].values() for s in m.values()),
                     "Actual count distribution validation failed")
    return rows, {"denominators": denominators, "markets": summary, "distributions_valid": distributions_valid}


def decision(source, scalars=None, variances=None, counts=None):
    if source.get("status") != "PASS_UNDER_ASSUMPTION":
        return {"status": "STOP", "selected_variance_mode": None, "reason": "Source/state validation failed"}
    old = previous.decision(source, scalars, variances)
    gates = dict(old["gates"])
    gates["actual_points_count_gain"] = (counts["2023"]["markets"]["points"]["rate_minus_constant_nll"] <= -.005 and
                                          counts["2024"]["markets"]["points"]["rate_minus_constant_nll"] <= 0)
    gates["count_distributions_valid"] = all(counts[y]["distributions_valid"] for y in ("2023", "2024"))
    if not gates["count_distributions_valid"]:
        return {"status": "STOP", "selected_variance_mode": None, "gates": gates, "reason": "Distribution checks failed"}
    selected = "rate" if all(gates.values()) else "constant"
    return {"status": "RESEARCH_ONLY", "selected_variance_mode": selected, "gates": gates,
            "reason": "Rate change met all fixed development gates" if selected == "rate" else
                      "Rate change rejected; constant model retained as an unqualified prospective research benchmark",
            "market_advantage_established": False, "independent_validation_complete": False}


def render(results):
    chosen = results["decision"]
    lines = ["# Participation repair and uncertainty comparison", "", chosen["reason"], "",
             "These are reused development seasons, with assumed historical source clocks. No market prices, 2025 or 2026 assets were opened.", ""]
    if results.get("counts"):
        lines += ["| Season | Market | Old count loss | Constant loss | Rate loss | Rate minus constant |",
                  "| --- | --- | ---: | ---: | ---: | ---: |"]
        for year in ("2023", "2024"):
            for market, row in results["counts"][year]["markets"].items():
                n = row["mean_nll"]
                lines.append(f"| {year} | {market} | {n['old']:.6f} | {n['constant']:.6f} | {n['rate']:.6f} | {row['rate_minus_constant_nll']:+.6f} |")
    lines += ["", "Lower count log loss is better. The old capped distribution uses the same repaired histories here; this is a new comparison, not a replacement for earlier frozen results.",
              "", "A frozen research candidate is not qualified for betting. Prospective prices, outcomes, costs and uncertainty must be evaluated separately.", ""]
    return "\n".join(lines)


def run(raw, fit_directory, bundle, seal, output, registration, commit, *, budget_seconds=600,
        source_policy=None, source_policy_commit=None):
    previous.require(type(budget_seconds) is int and 0 < budget_seconds <= BUDGET_SECONDS, "Budget exceeds fixed600 seconds")
    reg = registration_identity(registration, commit)
    previous.require(bool(source_policy) == bool(source_policy_commit), "Source policy requires an exact registration and commit")
    policy = registration_identity(source_policy, source_policy_commit) if source_policy else None
    previous.require(policy is None or policy["path"] == "research/experiments/2026-09-wnba-v2-source-quarantine.md",
                     "Unrecognized source-policy registration")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    deadline = perf_counter() + budget_seconds
    recipe, provenance = previous.frozen_recipe(fit_directory, bundle, seal)
    previous.write(output / "inherited-recipe.json", recipe)
    data, sample = previous.load_sources(raw, output, deadline)
    from . import sources
    cohort = sources.repair_cohort(data.player_box)
    previous.write(output / "repair-cohort.json", {"rows": cohort, "selection_uses_counts": False})
    try:
        observations = sources.observations(data, availability_hours=8)
        previous.require(all(o.season <= 2024 and o.effective_at.year <= 2024 for o in observations), "Excluded source observation")
        check = source_check(data, observations, sample, recipe, deadline, quarantine_pre2015=policy is not None)
    except ValueError as error:
        check = {"status": "FAIL", "setup_failure": str(error), "meaning": "Source validation stopped before calibration"}
    previous.write(output / "source-quality.json", data.quality)
    previous.write(output / "source-check.json", check)
    results = {"schema": "wnba-participation-comparison-v1", "registration": reg,
               "source_policy": policy,
               "inherited_recipe_provenance": provenance, "source_status": check["status"],
               "new_rate_or_minutes_fits": 0, "source_asset_max_season": 2024, "market_prices_read": 0}
    if check["status"] == "PASS_UNDER_ASSUMPTION":
        calibration = checkpoints(observations, recipe, range(2015, 2023), deadline)
        previous.write_rows(output / "calibration-checkpoints.jsonl.gz", calibration)
        scalars = freeze_scalars(calibration, recipe)
        freeze = {"schema": "wnba-v2-noise-freeze-v1", "scalars": scalars,
                  "calibration_sha256": digest(output / "calibration-checkpoints.jsonl.gz"),
                  "inherited_recipe_sha256": digest(output / "inherited-recipe.json")}
        previous.write(output / "scalar-freeze.json", freeze)
        freeze_hash = digest(output / "scalar-freeze.json")
        print("Calibration scalar freeze written before check-year summaries.", flush=True)
        variances, counts = {}, {}
        for year in (2023, 2024):
            check_rows = checkpoints(observations, recipe, [year], deadline)
            previous.write_rows(output / f"checkpoints-{year}.jsonl.gz", check_rows)
            variances[str(year)] = previous.variance_report(innovation_rows(check_rows), scalars)
            count_rows, counts[str(year)] = count_comparison(observations, recipe, scalars, year, deadline)
            previous.write_rows(output / f"count-scores-{year}.jsonl.gz", count_rows)
            previous.require(digest(output / "scalar-freeze.json") == freeze_hash, "Scalar freeze changed")
            print(f"Completed fixed {year} variance and actual count comparison.", flush=True)
        results.update(scalars=scalars, variances=variances, counts=counts,
                       calibration_denominators={"requested": len(calibration), "eligible": len(innovation_rows(calibration))},
                       decision=decision(check, scalars, variances, counts))
        candidate = model.configuration(recipe, scalars, results["decision"]["selected_variance_mode"])
        previous.write(output / "candidate-recipe.json", candidate)
    else:
        results["decision"] = decision(check)
    previous.check_time(deadline)
    previous.write(output / "results.json", results)
    with (output / "decision.md").open("x") as stream:
        stream.write(render(results))
    code = [Path(__file__), Path(model.__file__), Path(sources.__file__)]
    receipt = {"schema": "wnba-v2-comparison-receipt-v1", "status": "complete", "registration": reg,
               "source_policy": policy,
               "budget_seconds": budget_seconds, "implementation": [{"path": p.name, "sha256": digest(p)} for p in code],
               "artifacts": [{"path": p.name, "sha256": digest(p)} for p in sorted(output.iterdir()) if p.is_file()]}
    previous.write(output / "receipt.json", receipt)
    print(json.dumps(results["decision"], sort_keys=True), flush=True)
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("raw", "fit-directory", "bundle", "seal", "output", "registration"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--registration-commit", required=True)
    parser.add_argument("--budget-seconds", type=int, default=600)
    parser.add_argument("--source-policy", type=Path)
    parser.add_argument("--source-policy-commit")
    a = parser.parse_args(argv)
    run(a.raw, a.fit_directory, a.bundle, a.seal, a.output, a.registration, a.registration_commit,
        budget_seconds=a.budget_seconds, source_policy=a.source_policy, source_policy_commit=a.source_policy_commit)


if __name__ == "__main__":
    main()
