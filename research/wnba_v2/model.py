"""Separate participation-aware recipe with auditable count distributions.

The original structural engine and its results remain unchanged. This module
inherits its fixed coefficients, but distinguishes participation from measured
exposure and can include posterior rate uncertainty in points probabilities.
"""
from __future__ import annotations

import json
import math

import numpy as np
from scipy.special import expit, gammaln, logsumexp, ndtr
from scipy.stats import nbinom, poisson

from research.engine import model as original
from research.engine.store import ForecastRequest, ProtectedDataError, canonical_json, payload_digest

MARKETS = original.MARKETS
MINUTES = original.MINUTES
SCHEMA = "wnba-participation-v2"


def configuration(base_recipe, scalars, variance_mode="constant"):
    if variance_mode not in ("constant", "rate"):
        raise ValueError("Unsupported variance mode")
    result = {"schema": SCHEMA, "base_recipe": base_recipe,
              "variance_mode": variance_mode, "noise_scalars": scalars,
              "rate_uncertainty_markets": ["points"],
              "participation_policy": "explicit; unknown exposure omits numeric measurements",
              "distribution": "minute mixture of moment-matched negative binomials",
              "parameter_refits": 0}
    result = json.loads(canonical_json(result))
    result["recipe_hash"] = payload_digest(result)
    return result


def participation(payload):
    value = payload.get("participation")
    if value not in ("played", "dnp", "unknown"):
        raise ValueError("Explicit participation state required")
    flag = payload.get("did_not_play")
    if ((value == "played" and flag is not False) or
            (value == "dnp" and flag is not True) or
            (value == "unknown" and flag is not None)):
        raise ValueError("Participation and DNP flag disagree")
    if value == "dnp" and (payload.get("minutes") not in (None, 0) or
            any(isinstance(c, (int, float)) and c > 0 for c in payload.get("counts", {}).values())):
        raise ValueError("Explicit DNP contradicts production or minutes")
    return value


def measured_exposure(payload):
    """Return a usable exposure, or None; never manufacture elapsed seconds."""
    played = participation(payload) == "played"
    eligible = payload.get("rate_measurement_eligible")
    if type(eligible) is not bool:
        raise ValueError("Explicit measurement eligibility required")
    if not eligible:
        return None
    if not played or payload.get("exposure_status") != "observed_positive":
        raise ValueError("Numerical measurement contradicts participation/exposure")
    values = [payload.get(k) for k in ("minutes", "duration_minutes", "possessions_estimate")]
    if any(isinstance(x, bool) or not isinstance(x, (float, int)) or not math.isfinite(x) or x <= 0 for x in values):
        raise ValueError("Measured exposure requires positive finite units")
    minutes, duration, possessions = values
    if minutes > 60:
        raise ValueError("Minutes exceed supported range")
    for market in MARKETS:
        value = payload.get("counts", {}).get(market)
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0 or int(value) != value:
            raise ValueError("Measured count must be a nonnegative integer")
    return minutes * possessions / duration


def measured_minutes(payload):
    eligible = payload.get("minute_measurement_eligible")
    if type(eligible) is not bool:
        raise ValueError("Explicit minute measurement eligibility required")
    if not eligible:
        return None
    value = payload.get("minutes")
    if (participation(payload) != "played" or isinstance(value, bool) or
            not isinstance(value, (float, int)) or not math.isfinite(value) or not 0 < value <= 60):
        raise ValueError("Invalid measured minutes")
    return float(value)


class ParticipationModel(original.StructuralModel):
    """Frozen coefficients and corrected causal state; no fitting interface."""
    def __init__(self, observations, recipe):
        wrapper = json.loads(canonical_json(recipe))
        if wrapper.get("schema") != SCHEMA:
            raise ValueError("Unsupported participation recipe")
        claimed = wrapper.pop("recipe_hash", None)
        identity = payload_digest(wrapper)
        if claimed != identity:
            raise ValueError("Participation recipe hash mismatch")
        if wrapper.get("variance_mode") not in ("constant", "rate") or wrapper.get("rate_uncertainty_markets") != ["points"]:
            raise ValueError("Unregistered variance configuration")
        base = wrapper["base_recipe"]
        if base.get("through_season") != 2022 or base.get("variant") != "box" or base.get("shrinkage") != 1:
            raise ValueError("Only the inherited 2022 box recipe is supported")
        for market in MARKETS:
            for key in ("F_total", "F_noise"):
                x = wrapper["noise_scalars"][market][key]
                if isinstance(x, bool) or not isinstance(x, (float, int)) or not math.isfinite(x) or x < 1:
                    raise ValueError("Count noise must be finite and at least Poisson")
        super().__init__(observations, base)
        self.configuration = dict(wrapper, recipe_hash=identity)
        self.recipe_hash = identity

    def _new_state(self):
        state = super()._new_state()
        state.update(minute_measured=0, unknown_participation=0, unmeasured_played=0)
        return state

    def _update(self, state, item):
        p = item.payload
        status = participation(p)
        exposure = measured_exposure(p)
        minutes = measured_minutes(p)
        rvar = np.array(self.recipe["priors"]["rate_rvar"])
        if state["season"] is not None and item.season != state["season"]:
            if state["season_n"]:
                state["prior_season"] = state["season_sum"] / state["season_n"]
            state["season_sum"], state["season_n"] = 0., 0
            state["variance"] += rvar * .003
        state["season"] = item.season
        state["n"] += 1
        state["last_tip"] = item.effective_at
        if status == "unknown":
            state["unknown_participation"] += 1
            return
        state["dnp"] = .18 * (status == "dnp") + .82 * state["dnp"]
        state["starter"] = .18 * bool(p.get("starter", False)) + .82 * state["starter"]
        if status == "dnp":
            return
        if state["played"] == 0:
            pos = str(p.get("position") or "unknown")[0].upper()
            state["rates"] = np.array(self.recipe["priors"]["rates"].get(pos, self.recipe["priors"]["rates"]["all"]))
        state["played"] += 1
        state["variance"] += .0003 * rvar
        if minutes is None:
            state["unmeasured_played"] += 1
        else:
            state["minute_measured"] += 1
            state["fast"] = .18 * minutes + .82 * state["fast"]
            state["slow"] = .05 * minutes + .95 * state["slow"]
            state["second"] = .18 * minutes ** 2 + .82 * state["second"]
            state["season_sum"] += minutes
            state["season_n"] += 1
        if exposure is None:
            return
        gain = state["variance"] / (state["variance"] + rvar / exposure)
        counts = np.array([p["counts"][m] for m in MARKETS])
        state["rates"] = np.maximum(1e-4, state["rates"] + gain * (counts / exposure - state["rates"]))
        state["variance"] *= 1 - gain

    def _features(self, request, *, include_records=True):
        x, state, pace, records = super()._features(request, include_records=include_records)
        # Observing participation without minutes does not increase numerical
        # minutes information. All other inherited features remain unchanged.
        n, shrink, prior = state["minute_measured"], self.recipe["shrinkage"], state["prior_season"]
        x[1] = (n * state["fast"] + 6 * shrink * prior) / (n + 6 * shrink)
        x[2] = (n * state["slow"] + 20 * shrink * prior) / (n + 20 * shrink)
        return x, state, pace, records

    def predict(self, request, *, include_manifest=True):
        if not isinstance(request, ForecastRequest):
            raise TypeError("Market-free ForecastRequest required")
        if request.season <= 2022 or request.tip_at.year <= 2022:
            raise ValueError("Prediction must follow frozen coefficient cutoff")
        x, state, pace, records = self._features(request, include_records=include_manifest)
        z = (x - np.array(self.recipe["feature_center"])) / np.array(self.recipe["feature_scale"])
        mean = float(np.clip(z @ np.array(self.recipe["minutes_coefficients"]), 1, 45))
        p_dnp = float(np.clip(expit(z @ np.array(self.recipe["dnp_coefficients"])), .001, .995))
        role = min(3, int(mean // 10))
        measured = state["minute_measured"]
        weight = measured / (measured + 12 * self.recipe["shrinkage"])
        variance = max(2.25, weight * max(1., state["second"] - state["fast"] ** 2) +
                       (1 - weight) * self.recipe["role_variance"][role])
        sigma = math.sqrt(variance)
        probs = ndtr((MINUTES + .5 - mean) / sigma) - ndtr((MINUTES - .5 - mean) / sigma)
        probs /= probs.sum()
        components = {}
        for i, market in enumerate(MARKETS):
            scalar = self.configuration["noise_scalars"][market]
            e = MINUTES * pace
            mu = e * state["rates"][i]
            is_rate = self.configuration["variance_mode"] == "rate" and market == "points"
            var = scalar["F_noise"] * mu + e ** 2 * state["variance"][i] if is_rate else scalar["F_total"] * mu
            components[market] = {"means": mu.tolist(), "variances": var.tolist()}
        manifest = {"schema": "wnba-v2-inputs-v1", "request": request.manifest_entry(),
                    "observations": [o.manifest_entry() for o in records]} if include_manifest else None
        result = {"schema": "wnba-v2-forecast-v1", "recipe_hash": self.recipe_hash,
                  "request": request.manifest_entry(), "p_dnp": p_dnp,
                  "minutes_values": MINUTES.tolist(), "minutes_probs": probs.tolist(),
                  "count_components": components, "rate_mean": state["rates"].tolist(),
                  "rate_variance": state["variance"].tolist(), "history_rows": state["n"],
                  "measured_minutes_rows": measured, "participated_rows": state["played"],
                  "unmeasured_played_rows": state["unmeasured_played"],
                  "unknown_participation_rows": state["unknown_participation"],
                  "pace_possessions_per_minute": pace,
                  "input_manifest_hash": payload_digest(manifest) if manifest else None,
                  "input_max_available_at": original._iso(max(o.available_at for o in records)) if records else None,
                  "fallback": "league_prior" if not state["played"] else None,
                  "usage_redistribution_applied": False,
                  "time_basis": "assumed" if any(o.time_basis.value == "assumed" for o in records) else "observed"}
        if include_manifest:
            result["input_manifest"] = manifest
        return result


def distribution_arrays(forecast, market):
    if market not in MARKETS or forecast.get("schema") != "wnba-v2-forecast-v1":
        raise ValueError("Unsupported count forecast")
    weights = np.asarray(forecast["minutes_probs"], dtype=float)
    components = forecast["count_components"][market]
    means, variances = (np.asarray(components[k], dtype=float) for k in ("means", "variances"))
    if (any(a.shape != (60,) or np.any(~np.isfinite(a)) for a in (weights, means, variances)) or
            np.any(weights < 0) or not math.isclose(float(weights.sum()), 1., abs_tol=1e-10) or
            np.any(means <= 0) or np.any(variances < means)):
        raise ValueError("Invalid normalized count mixture")
    return weights, means, variances


def component_logpmf(actual, means, variances):
    if isinstance(actual, bool) or not math.isfinite(actual) or actual < 0 or int(actual) != actual:
        raise ValueError("Count must be a nonnegative integer")
    extra = variances - means
    use_poisson = extra <= 1e-10 * means
    result = actual * np.log(means) - means - gammaln(actual + 1)
    use = ~use_poisson
    shape = means[use] ** 2 / extra[use]
    p = means[use] / variances[use]
    result[use] = (gammaln(actual + shape) - gammaln(shape) - gammaln(actual + 1) +
                   shape * np.log(p) + actual * np.log1p(-p))
    return result


def count_log_probability(forecast, market, actual):
    weights, means, variances = distribution_arrays(forecast, market)
    log_weights = np.full(weights.shape, -np.inf)
    np.log(weights, out=log_weights, where=weights > 0)
    return float(logsumexp(log_weights + component_logpmf(actual, means, variances)))


def count_probability(forecast, market, actual):
    return math.exp(count_log_probability(forecast, market, actual))


def count_cdf(forecast, market, value):
    weights, means, variances = distribution_arrays(forecast, market)
    extra = variances - means
    values = poisson.cdf(value, means)
    use = extra > 1e-10 * means
    values[use] = nbinom.cdf(value, means[use] ** 2 / extra[use], means[use] / variances[use])
    return float(weights @ values)


def price_forecast(forecast, market, line, actual=None):
    if isinstance(line, bool) or not math.isfinite(line) or line < 0:
        raise ValueError("Line must be finite and nonnegative")
    weights, means, variances = distribution_arrays(forecast, market)
    under = count_cdf(forecast, market, math.ceil(line) - 1)
    push = count_probability(forecast, market, int(line)) if float(line).is_integer() else 0.
    over = max(0., 1 - under - push)
    mean = float(weights @ means)
    return {"p_under": under, "p_push": push, "p_over": over,
            "model_mean": mean, "model_variance": float(weights @ (variances + means ** 2) - mean ** 2),
            "count_p_actual": None if actual is None else count_probability(forecast, market, actual),
            "participation_conditioned": True}
