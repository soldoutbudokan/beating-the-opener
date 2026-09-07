"""Market-free WNBA minutes × possession-rate forecasts.

All inputs are immutable ``store.Observation`` records. Parameters are learned
only through 2024, and historical state advances only after source availability.
The two registered recipes differ solely in prior effective sample size. The
pricing helper consumes a completed forecast; no line enters model fitting or
prediction. Count components, minute masses and input records are serializable
so a saved-row verifier does not need to refit the model.
"""
from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import math

import numpy as np
from scipy.special import expit, gammaln, ndtr
from scipy.stats import nbinom, poisson

from .store import (ForecastRequest, Observation, ObservationKind,
                    PointInTimeStore, ProtectedDataError, canonical_json,
                    payload_digest)

MARKETS = ("points", "rebounds", "assists", "threes")
MINUTES = np.arange(1., 61.)
FEATURES = ("intercept", "fast_minutes", "slow_minutes", "season_minutes",
            "starter", "log_appearances", "back_to_back", "rest_week",
            "minute_sd", "recent_dnp", "roster_count")
RECIPE_VERSION = "structural-compound-v1"


def _iso(value):
    return value.isoformat().replace("+00:00", "Z")


def _record_key(item):
    return (item.source_id, item.record_id, item.available_at, item.payload_hash)


def _record_type(item):
    return item.payload.get("record_type")


def _number(payload, key, default=0.):
    value = payload.get(key, default)
    if value is None:
        return float(default)
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{key} must be finite")
    return result


def _exposure(item, variant="box"):
    p = item.payload
    minutes = _number(p, "minutes")
    if variant == "pbp" and p.get("pbp_possessions") is not None:
        value = _number(p, "pbp_possessions")
        if value > 0:
            return value
    duration = _number(p, "duration_minutes", 40.)
    possessions = _number(p, "possessions_estimate", 80.)
    if duration <= 0 or possessions <= 0:
        raise ValueError("historical possession exposure must be positive")
    return minutes * possessions / duration


def _validate_player(item):
    p = item.payload
    minutes = _number(p, "minutes")
    if not 0 <= minutes <= 60:
        raise ValueError("player minutes outside 0..60")
    if bool(p.get("did_not_play", minutes == 0)) != (minutes == 0):
        raise ValueError("DNP and minutes contradict")
    for market in MARKETS:
        count = _number(p.get("counts", {}), market)
        if count < 0 or count != int(count):
            raise ValueError("counts must be nonnegative integers")
    if minutes == 0 and any(_number(p.get("counts", {}), m) for m in MARKETS):
        raise ValueError("DNP record has nonzero production")
    _exposure(item)


def _fit_hash(items):
    entries = [item.manifest_entry() for item in sorted(items, key=_record_key)]
    return payload_digest({"observations": entries})


def _priors(items, variant):
    """Position priors and observation noise use pre-2015 history only."""
    prior = [o for o in items if _record_type(o) == "player_box"
             and o.season < 2015 and o.effective_at.year < 2015
             and o.available_at.year < 2015]
    if not prior:
        raise ValueError("pre-2015 history is required for rate/minute priors")
    totals = defaultdict(lambda: np.zeros(5))
    played_minutes, all_dnp = [], []
    player_totals = defaultdict(lambda: np.zeros(5))
    for item in prior:
        _validate_player(item)
        minutes = _number(item.payload, "minutes")
        all_dnp.append(minutes == 0)
        if minutes <= 0:
            continue
        played_minutes.append(minutes)
        exposure = _exposure(item, variant)
        values = np.array([_number(item.payload["counts"], m) for m in MARKETS])
        pos = str(item.payload.get("position") or "unknown")[0].upper()
        contribution = np.r_[exposure, values]
        totals["all"] += contribution
        totals[pos] += contribution
        player_totals[item.entity_id] += contribution
    if not played_minutes:
        raise ValueError("pre-2015 history contains no played appearances")
    global_rates = totals["all"][1:] / totals["all"][0]
    rates = {key: ((value[1:] + global_rates * 1000.) /
                  (value[0] + 1000.)).tolist() for key, value in totals.items()}
    residual = np.zeros(4)
    n_residual = 0
    for item in prior:
        exposure = _exposure(item, variant)
        if exposure <= 0:
            continue
        aggregate = player_totals[item.entity_id]
        own_mean = aggregate[1:] / aggregate[0]
        observed = np.array([_number(item.payload["counts"], m) for m in MARKETS]) / exposure
        residual += exposure * (observed - own_mean) ** 2
        n_residual += 1
    rvar = np.maximum(residual / max(n_residual, 1), global_rates * .5)
    team = [o for o in items if _record_type(o) == "team_box"
            and o.season < 2015 and o.effective_at.year < 2015
            and o.available_at.year < 2015]
    paces = [_number(o.payload, "possessions_estimate", 80.) /
             _number(o.payload, "duration_minutes", 40.) for o in team]
    return {"rates": rates, "rate_rvar": rvar.tolist(),
            "minutes_mean": float(np.mean(played_minutes)),
            "minutes_variance": float(np.var(played_minutes)),
            "p_dnp": float(np.mean(all_dnp)),
            "pace": float(np.mean(paces)) if paces else 2.,
            "prior_input_hash": _fit_hash(prior + team), "prior_rows": len(prior)}


class _HistoryIndex:
    """Indexed equivalent of PointInTimeStore selection, including revisions."""
    def __init__(self, observations):
        self.store = PointInTimeStore(observations)
        self.by_entity = defaultdict(list)
        for item in self.store.observations:
            self.by_entity[item.entity_id].append(item)
        self.clocks = {}
        self._entity_cache = {}
        for entity, records in self.by_entity.items():
            records.sort(key=lambda x: (x.available_at, x.source_id, x.record_id))
            self.clocks[entity] = [x.available_at for x in records]

    def select_entity(self, entity, request):
        if request.season >= 2026 or request.tip_at.year >= 2026:
            raise ProtectedDataError("model development stops before 2026")
        cache_key = (request.as_of, request.event_id, request.season)
        cached = self._entity_cache.get(entity)
        if cached is not None and cached[0] == cache_key:
            return cached[1]
        latest = {}
        completed = (ObservationKind.HISTORICAL_OUTCOME, ObservationKind.FINAL_ROSTER)
        stop = bisect_left(self.clocks.get(entity, []), request.as_of)
        for item in self.by_entity.get(entity, [])[:stop]:
            if item.kind in completed and (item.event_id == request.event_id or
                                            item.effective_at >= request.as_of):
                continue
            if item.season >= 2026 or (item.kind in completed and item.effective_at.year >= 2026):
                raise ProtectedDataError("protected evidence cannot enter model state")
            latest[(item.source_id, item.record_id)] = item
        result = tuple(latest[key] for key in sorted(latest))
        self._entity_cache[entity] = (cache_key, result)
        return result

    def select(self, request):
        selected = {}
        for entity in sorted({request.entity_id, request.team_id, request.opponent_id}):
            for item in self.select_entity(entity, request):
                selected[(item.source_id, item.record_id)] = item
        return tuple(selected[key] for key in sorted(selected))


class StructuralModel:
    """Frozen learned coefficients with causal, post-game latent state updates."""
    def __init__(self, observations, recipe):
        # Canonical serialization detaches mutable caller-owned dictionaries.
        import json
        self.recipe = json.loads(canonical_json(recipe))
        if self.recipe.get("schema") != RECIPE_VERSION:
            raise ValueError("unsupported structural recipe")
        if self.recipe.get("through_season", 9999) > 2024:
            raise ProtectedDataError("learned parameters must freeze before 2025")
        expected = self.recipe.pop("recipe_hash", None)
        self.recipe_hash = payload_digest(self.recipe)
        if expected is not None and expected != self.recipe_hash:
            raise ValueError("recipe hash mismatch")
        self.recipe["recipe_hash"] = self.recipe_hash
        self.index = _HistoryIndex(observations)
        self.variant = self.recipe.get("variant", "box")
        if self.variant not in ("box", "pbp"):
            raise ValueError("unknown model variant")
        self._player_cache = {}
        self._team_cache = {}
        self._team_context_cache = {}

    def _new_state(self):
        p = self.recipe["priors"]
        return {"n": 0, "played": 0, "fast": p["minutes_mean"],
                "slow": p["minutes_mean"], "second": p["minutes_mean"] ** 2 + p["minutes_variance"],
                "starter": .5, "dnp": p["p_dnp"], "season": None,
                "season_sum": 0., "season_n": 0, "prior_season": p["minutes_mean"],
                "last_tip": None, "rates": np.array(p["rates"]["all"]),
                "variance": np.array(p["rate_rvar"]) / (60. * self.recipe["shrinkage"]),
                "pbp_rows": 0, "foul_risk": 0., "blowout_risk": 0., "stint_minutes": 0.}

    def _update(self, state, item):
        _validate_player(item)
        p = item.payload
        minutes = _number(p, "minutes")
        if state["season"] is not None and item.season != state["season"]:
            if state["season_n"]:
                state["prior_season"] = state["season_sum"] / state["season_n"]
            state["season_sum"], state["season_n"] = 0., 0
            state["variance"] += np.array(self.recipe["priors"]["rate_rvar"]) * .003
        state["season"] = item.season
        state["n"] += 1
        state["dnp"] = .18 * (minutes == 0) + .82 * state["dnp"]
        state["starter"] = .18 * bool(p.get("starter", False)) + .82 * state["starter"]
        state["last_tip"] = item.effective_at
        if minutes <= 0:
            return
        if state["played"] == 0:
            pos = str(p.get("position") or "unknown")[0].upper()
            state["rates"] = np.array(self.recipe["priors"]["rates"].get(pos,
                                                   self.recipe["priors"]["rates"]["all"]))
        state["played"] += 1
        state["fast"] = .18 * minutes + .82 * state["fast"]
        state["slow"] = .05 * minutes + .95 * state["slow"]
        state["second"] = .18 * minutes ** 2 + .82 * state["second"]
        state["season_sum"] += minutes
        state["season_n"] += 1
        exposure = _exposure(item, self.variant)
        rvar = np.array(self.recipe["priors"]["rate_rvar"])
        state["variance"] += .0003 * rvar
        gain = state["variance"] / (state["variance"] + rvar / exposure)
        counts = np.array([_number(p["counts"], m) for m in MARKETS])
        state["rates"] = np.maximum(1e-4, state["rates"] + gain * (counts / exposure - state["rates"]))
        state["variance"] *= 1. - gain
        if self.variant == "pbp" and p.get("pbp_possessions") is not None:
            state["pbp_rows"] += 1
            for key in ("foul_risk", "blowout_risk", "stint_minutes"):
                if p.get(key) is not None:
                    state[key] = .18 * _number(p, key) + .82 * state[key]

    def _player_state(self, entity, records):
        selected = sorted((o for o in records if o.entity_id == entity and
                           _record_type(o) == "player_box" and
                           o.kind is ObservationKind.HISTORICAL_OUTCOME),
                          key=lambda o: (o.available_at, o.effective_at, o.source_id, o.record_id))
        keys = tuple(_record_key(o) for o in selected)
        old_keys, state = self._player_cache.get(entity, ((), self._new_state()))
        if len(old_keys) > len(keys) or old_keys != keys[:len(old_keys)]:
            old_keys, state = (), self._new_state()
        for item in selected[len(old_keys):]:
            self._update(state, item)
        self._player_cache[entity] = keys, state
        return state

    def _team_state(self, entity, records):
        cached = self._team_context_cache.get(entity)
        if cached is not None and cached[0] is records:
            return cached[1]
        selected = sorted((o for o in records if o.entity_id == entity and
                           _record_type(o) == "team_box" and
                           o.kind is ObservationKind.HISTORICAL_OUTCOME),
                          key=lambda o: (o.available_at, o.effective_at, o.source_id, o.record_id))
        keys = tuple(_record_key(o) for o in selected)
        old_keys, state = self._team_cache.get(entity, ((), [self.recipe["priors"]["pace"], 12.]))
        if len(old_keys) > len(keys) or old_keys != keys[:len(old_keys)]:
            old_keys, state = (), [self.recipe["priors"]["pace"], 12.]
        for item in selected[len(old_keys):]:
            p = item.payload
            pace = (_number(p, "pbp_possessions", _number(p, "possessions_estimate", 80.))
                    if self.variant == "pbp" else _number(p, "possessions_estimate", 80.))
            pace /= _number(p, "duration_minutes", 40.)
            state[0] = .12 * pace + .88 * state[0]
            state[1] = _number(p, "roster_count", state[1])
        self._team_cache[entity] = keys, state
        self._team_context_cache[entity] = records, state
        return state

    def _features(self, request, *, include_records=True):
        histories = {entity: self.index.select_entity(entity, request)
                     for entity in {request.entity_id, request.team_id, request.opponent_id}}
        state = self._player_state(request.entity_id, histories[request.entity_id])
        own = self._team_state(request.team_id, histories[request.team_id])
        opponent = self._team_state(request.opponent_id, histories[request.opponent_id])
        records = ()
        if include_records:
            combined = {(o.source_id, o.record_id): o for rows in histories.values() for o in rows}
            records = tuple(combined[key] for key in sorted(combined))
        n, shrink = state["played"], self.recipe["shrinkage"]
        prior = state["prior_season"]
        fast = (n * state["fast"] + 6. * shrink * prior) / (n + 6. * shrink)
        slow = (n * state["slow"] + 20. * shrink * prior) / (n + 20. * shrink)
        variance = max(1., state["second"] - state["fast"] ** 2)
        rest = 7. if state["last_tip"] is None else max(0., (request.tip_at - state["last_tip"]).total_seconds() / 86400.)
        features = np.array([1., fast, slow, prior, state["starter"],
                             math.log1p(min(state["n"], 100)), float(rest < 1.6),
                             min(rest, 7.) / 7., math.sqrt(variance), state["dnp"], own[1]])
        pace = max(.5, min(4., (own[0] + opponent[0]) / 2.))
        return features, state, pace, records

    def predict(self, request, *, include_manifest=True):
        if not isinstance(request, ForecastRequest):
            raise TypeError("predict requires a market-free ForecastRequest")
        if (request.season <= self.recipe["through_season"] or
                request.tip_at.year <= self.recipe["through_season"]):
            raise ValueError("forecast season must follow the learned-parameter cutoff")
        x, state, pace, records = self._features(request)
        center = np.array(self.recipe["feature_center"])
        scale = np.array(self.recipe["feature_scale"])
        z = (x - center) / scale
        mean = float(np.clip(z @ np.array(self.recipe["minutes_coefficients"]), 1., 45.))
        p_dnp = float(np.clip(expit(z @ np.array(self.recipe["dnp_coefficients"])), .001, .995))
        role = min(3, int(mean // 10.))
        role_var = self.recipe["role_variance"][role]
        observed_var = max(1., state["second"] - state["fast"] ** 2)
        weight = state["played"] / (state["played"] + 12. * self.recipe["shrinkage"])
        variance = max(2.25, weight * observed_var + (1 - weight) * role_var)
        if self.variant == "pbp" and state["pbp_rows"]:
            # Prior foul/blowout evidence broadens uncertainty, never target-game labels.
            variance *= 1. + .15 * min(1., state["foul_risk"]) + .15 * min(1., state["blowout_risk"])
        sigma = math.sqrt(variance)
        probs = ndtr((MINUTES + .5 - mean) / sigma) - ndtr((MINUTES - .5 - mean) / sigma)
        probs /= probs.sum()
        components = {market: {"means": (MINUTES * pace * state["rates"][i]).tolist(),
                                "fano": self.recipe["count_fano"][i]}
                      for i, market in enumerate(MARKETS)}
        # All required source clocks remain explicit; retrieval metadata is excluded.
        manifest = None
        if include_manifest:
            manifest = {"schema": "structural-inputs-v1", "request": request.manifest_entry(),
                        "observations": [item.manifest_entry() for item in records]}
        output = {"schema": "structural-forecast-v1", "recipe_hash": self.recipe_hash,
                  "request": request.manifest_entry(), "p_dnp": p_dnp,
                  "minutes_values": MINUTES.tolist(), "minutes_probs": probs.tolist(),
                  "count_components": components,
                  "input_manifest_hash": payload_digest(manifest) if manifest is not None else None,
                  "input_max_available_at": _iso(max(o.available_at for o in records)) if records else None,
                  "history_rows": state["n"], "pbp_history_rows": state["pbp_rows"],
                  "fallback": "league_prior" if not state["played"] else None,
                  "pace_possessions_per_minute": pace,
                  "usage_redistribution_applied": False,
                  "usage_limitation": "No independently known upcoming teammate absence is supplied.",
                  "time_basis": "assumed" if any(o.time_basis.value == "assumed" for o in records) else "observed"}
        if include_manifest:
            output["input_manifest"] = manifest
        return output


def training_examples(observations, season, *, lead_hours=24):
    """Observed appearance outcomes for market-free component development.

    Explicit DNP box records remain in this population. It is an appearance-row
    validation population, not a claim of complete pregame roster coverage.
    Target row identities define labels only; final starter/minutes/counts never
    enter ForecastRequest, and candidate quote forecasts have their own IDs.
    """
    if season >= 2026:
        raise ProtectedDataError("protected seasons cannot supply development labels")
    players = defaultdict(list)
    event_sides = defaultdict(set)
    for observation in observations:
        if _record_type(observation) == "player_box":
            players[observation.entity_id].append(observation)
        elif _record_type(observation) == "team_box":
            # The source pilot independently reconciles these event identities
            # with schedule home/away IDs. No target player roster is consulted.
            event_sides[observation.event_id].update((str(observation.payload["team_id"]),
                                                     str(observation.payload["opponent_id"])))
    for rows in players.values():
        rows.sort(key=lambda o: (o.available_at, o.effective_at, o.record_id))
    clocks = {key: [o.available_at for o in value] for key, value in players.items()}
    rows = [o for o in observations if _record_type(o) == "player_box" and o.season == season]
    rows.sort(key=lambda o: (o.effective_at, o.event_id, o.entity_id, o.source_id, o.record_id))
    for row in rows:
        p = row.payload
        # The event's two teams are independently verified event identities. Resolve which side the
        # player belongs to using prior available appearances, never this row's
        # final team. Unresolved players receive deterministic event-side context.
        sides = sorted(event_sides[row.event_id])
        if len(sides) != 2:
            raise ValueError("training event requires two independently verified team identities")
        as_of = row.effective_at - timedelta(hours=lead_hours)
        stop = bisect_left(clocks[row.entity_id], as_of)
        prior = players[row.entity_id][:stop]
        team = next((str(o.payload["team_id"]) for o in reversed(prior)
                     if o.event_id != row.event_id and o.effective_at < as_of), sides[0])
        if team not in sides:
            team = sides[0]
        opponent = next(side for side in sides if side != team)
        yield ForecastRequest(row.entity_id, row.event_id, team, opponent, season,
                              row.effective_at, as_of), row


def _linear_fit(x, y, ridge=20.):
    penalty = np.eye(x.shape[1]) * ridge
    penalty[0, 0] = 1e-8
    return np.linalg.solve(x.T @ x + penalty, x.T @ y)


def _logistic_fit(x, y, ridge=20.):
    beta = np.zeros(x.shape[1])
    beta[0] = math.log((y.sum() + 1.) / (len(y) - y.sum() + 1.))
    penalty = np.eye(x.shape[1]) * ridge
    penalty[0, 0] = 1e-8
    for _ in range(30):
        probabilities = expit(x @ beta)
        weights = np.maximum(1e-5, probabilities * (1 - probabilities))
        gradient = x.T @ (y - probabilities) - penalty @ beta
        step = np.linalg.solve((x.T * weights) @ x + penalty, gradient)
        beta += step
        if np.max(np.abs(step)) < 1e-8:
            break
    return beta


def fit(observations, *, through_season=2022, shrinkage=1, variant="box", verbose=False):
    """Fit one of the two fixed recipes; no 2025 row can affect coefficients."""
    if isinstance(through_season, bool) or not 2015 <= through_season <= 2024:
        raise ProtectedDataError("fit cutoff must be a season from 2015 through 2024")
    if shrinkage not in (1, 2) or variant not in ("box", "pbp"):
        raise ValueError("only registered shrinkage recipes 1/2 and box/pbp variants are allowed")
    items = tuple(PointInTimeStore(observations).observations)
    eligible = [o for o in items if o.season <= through_season
                and o.effective_at.year <= through_season and o.available_at.year <= through_season]
    priors = _priors(eligible, variant)
    recipe = {"schema": RECIPE_VERSION, "through_season": through_season,
              "shrinkage": shrinkage, "variant": variant, "priors": priors,
              "features": list(FEATURES), "feature_center": [0.] * len(FEATURES),
              "feature_scale": [1.] * len(FEATURES),
              "minutes_coefficients": [priors["minutes_mean"]] + [0.] * (len(FEATURES) - 1),
              "dnp_coefficients": [math.log((priors["p_dnp"] + .001) / (1.001 - priors["p_dnp"]))] + [0.] * (len(FEATURES) - 1),
              "role_variance": [priors["minutes_variance"]] * 4, "count_fano": [1.] * 4,
              "training_cutoff": "24 hours before scheduled tip", "fit_input_hash": _fit_hash(eligible),
              "fit_input_rows": len(eligible), "rate_q_multiplier": .0003,
              "conditional_fano_bounds": [1., 2.], "ridge": 20.,
              "usage_policy": "No adjustment without independently known prior teammate absence"}
    engine = StructuralModel(eligible, recipe)
    features, minutes, counts, conditional_means = [], [], [], []
    for season in range(2015, through_season + 1):
        for request, row in training_examples(eligible, season):
            _validate_player(row)
            x, state, pace, _ = engine._features(request, include_records=False)
            observed_minutes = _number(row.payload, "minutes")
            features.append(x)
            minutes.append(observed_minutes)
            counts.append([_number(row.payload["counts"], m) for m in MARKETS])
            # Count residuals are one-step-ahead latent rates with actual exposure;
            # actual minutes are a training denominator, never a forecast input.
            exposure = _exposure(row, variant)
            conditional_means.append(state["rates"].copy() * exposure)
        if verbose:
            print(f"  {variant} recipe {shrinkage}: processed through {season}, {len(minutes)} training appearances", flush=True)
    if len(features) < 20:
        raise ValueError("at least 20 chronological training appearances required")
    x, y = np.array(features), np.array(minutes)
    played = y > 0
    if played.sum() < 10:
        raise ValueError("at least 10 played training appearances required")
    center, scale = x.mean(axis=0), x.std(axis=0)
    center[0], scale[0] = 0., 1.
    scale = np.where(scale < 1e-8, 1., scale)
    z = (x - center) / scale
    beta = _linear_fit(z[played], y[played])
    dnp_beta = _logistic_fit(z, (~played).astype(float))
    fitted = np.clip(z @ beta, 1., 45.)
    residual = (y - fitted) ** 2
    global_variance = float(residual[played].mean())
    role_variance = []
    for role in range(4):
        mask = played & (np.minimum(3, fitted.astype(int) // 10) == role)
        role_variance.append(float((residual[mask].sum() + 100. * global_variance) / (mask.sum() + 100.)))
    mu = np.maximum(np.array(conditional_means)[played], 1e-6)
    observed = np.array(counts)[played]
    fano = np.clip(((observed - mu) ** 2).sum(axis=0) / mu.sum(axis=0), 1., 2.)
    recipe.update(feature_center=center.tolist(), feature_scale=scale.tolist(),
                  minutes_coefficients=beta.tolist(), dnp_coefficients=dnp_beta.tolist(),
                  role_variance=role_variance, count_fano=fano.tolist(),
                  training_rows=len(y), training_played=int(played.sum()),
                  training_dnp=int((~played).sum()),
                  training_population="Observed box appearance rows including explicit DNP; not complete pregame rosters")
    recipe["recipe_hash"] = payload_digest(recipe)
    return recipe


def _distribution(components):
    means = np.asarray(components["means"], dtype=float)
    fano = float(components["fano"])
    if means.shape != (60,) or np.any(~np.isfinite(means)) or np.any(means <= 0):
        raise ValueError("count mixture requires sixty positive finite means")
    if not 1. <= fano <= 2.:
        raise ValueError("conditional fano outside registered range")
    if abs(fano - 1.) < 1e-12:
        return poisson(means)
    return nbinom(means / (fano - 1.), 1. / fano)


def count_probability(forecast, market, actual):
    if isinstance(actual, bool) or actual < 0 or actual != int(actual):
        raise ValueError("actual count must be a nonnegative integer")
    weights = np.asarray(forecast["minutes_probs"], dtype=float)
    components = forecast["count_components"][market]
    means = np.asarray(components["means"], dtype=float)
    fano = float(components["fano"])
    if means.shape != (60,) or np.any(~np.isfinite(means)) or np.any(means <= 0):
        raise ValueError("count mixture requires sixty positive finite means")
    if not 1. <= fano <= 2.:
        raise ValueError("conditional fano outside registered range")
    k = int(actual)
    if abs(fano - 1.) < 1e-12:
        log_mass = k * np.log(means) - means - gammaln(k + 1.)
    else:
        shape, p = means / (fano - 1.), 1. / fano
        log_mass = (gammaln(k + shape) - gammaln(shape) - gammaln(k + 1.) +
                    shape * math.log(p) + k * math.log1p(-p))
    return float(weights @ np.exp(log_mass))


def price_forecast(forecast, market, line, actual=None):
    """Price a completed market-free forecast, conditional on participation."""
    if market not in MARKETS or not math.isfinite(float(line)) or line < 0:
        raise ValueError("unsupported market or line")
    weights = np.asarray(forecast["minutes_probs"], dtype=float)
    components = forecast["count_components"][market]
    distribution = _distribution(components)
    integer = float(line).is_integer()
    p_under = float(weights @ distribution.cdf(math.ceil(line) - 1))
    p_push = float(weights @ distribution.pmf(int(line))) if integer else 0.
    p_over = max(0., 1. - p_under - p_push)
    mean = float(weights @ np.array(components["means"]))
    lo, hi = 0, max(8, int(mean * 2 + 20))
    while float(weights @ distribution.cdf(hi)) < .5:
        hi *= 2
    while lo < hi:
        mid = (lo + hi) // 2
        if float(weights @ distribution.cdf(mid)) >= .5:
            hi = mid
        else:
            lo = mid + 1
    return {"p_over": p_over, "p_push": p_push, "p_under": p_under,
            "model_mean": mean, "model_median": float(lo),
            "count_p_actual": None if actual is None else count_probability(forecast, market, actual)}
