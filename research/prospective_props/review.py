"""Pre-score review schema for the owner-authorized pinned-recipe reconstruction."""
from __future__ import annotations

import hashlib
import json
from datetime import date
import math
import re


def integer(value, name):
    if type(value) is not int or value < 0:
        raise ValueError(f"Invalid nonnegative count: {name}")
    return value


def finite(value, name, optional=False):
    if value is None and optional:
        return None
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"Invalid finite metric: {name}")
    return value


def economics(value):
    bets = integer(value.get("bets"), "bets")
    stake, profit = finite(value.get("stake"), "stake"), finite(value.get("profit"), "profit")
    roi = finite(value.get("roi"), "ROI", optional=True)
    if stake != bets or (bets == 0 and (profit != 0 or roi is not None)) or (bets and (roi is None or not math.isclose(roi, profit / bets, rel_tol=1e-10, abs_tol=1e-10))):
        raise ValueError("Flat-$1 economics do not reconcile")
    selected = value.get("selections")
    if not isinstance(selected, list) or len(selected) != bets:
        raise ValueError("Selected bet count differs from economics")
    for row in selected:
        if row.get("stake") != 1 or row.get("refund") != 0:
            raise ValueError("Native selection stake/refund differs")
        finite(row.get("profit"), "selected profit")
    if not math.isclose(math.fsum(row["profit"] for row in selected), profit, rel_tol=1e-10, abs_tol=1e-10):
        raise ValueError("Selected profits differ from economics")
    finite(value.get("registered_t"), "ROI t", optional=True)


def validate_population(population):
    """The complete first crossing-day rule, independent of model scores."""
    if population.get("minimum") != 3000 or population.get("endpoint_rule") != "entire_first_ET_date_reaching_threshold":
        raise ValueError("Unexpected prospective endpoint rule")
    cumulative, previous, endpoint, early = 0, "", None, 0
    for row in population.get("daily_counts", []):
        day = row.get("date", "")
        parsed = date.fromisoformat(day)
        if parsed.year != 2026 or parsed.isoformat() != day or day < "2026-08-01" or day <= previous or type(row.get("n")) is not int or row["n"] <= 0:
            raise ValueError("Invalid ordered daily endpoint counts")
        cumulative += row["n"]
        if integer(row.get("cumulative"), "daily cumulative") != cumulative:
            raise ValueError("Daily cumulative population arithmetic differs")
        if cumulative >= 3000 and endpoint is None:
            endpoint = (day, cumulative)
        if day <= "2026-08-03" and (endpoint is None or day <= endpoint[0]):
            early += row["n"]
        previous = day
    if endpoint is None or endpoint != (population.get("endpoint_date"), integer(population.get("endpoint_n"), "endpoint")):
        raise ValueError("Endpoint is not the entire first threshold-crossing date")
    if cumulative != integer(population.get("available_eligible_n"), "available population"):
        raise ValueError("Available population arithmetic differs")
    return {"early_n": early, "later_n": population["endpoint_n"] - early}


def review(results, receipt, *, results_bytes=None):
    if results.get("schema") != "prospective-props-reconstruction-v1":
        raise ValueError("Unsupported prospective reconstruction schema")
    if results.get("arm") not in {"fp-prospective-1", "fp-prospective-2"}:
        raise ValueError("Unrecognized prospective arm")
    if receipt.get("status") != "complete" or receipt.get("arm") != results.get("arm") or receipt.get("reconstructed") is not True:
        raise ValueError("Missing completed matching arm receipt")
    if not isinstance(results_bytes, bytes) or hashlib.sha256(results_bytes).hexdigest() != receipt.get("results_sha256"):
        raise ValueError("Prospective result hash mismatch")
    if json.loads(results_bytes) != results:
        raise ValueError("Result object differs from the hashed saved bytes")
    for key in ("recipe_sha256", "forecasts_sha256", "implementation_sha256", "generation_implementation_sha256", "registration_sha256", "results_sha256", "attestation_sha256"):
        if not isinstance(receipt.get(key), str) or not re.fullmatch(r"[0-9a-f]{64}", receipt[key]):
            raise ValueError(f"Missing frozen provenance: {key}")
    for key in ("registration_commit", "freeze_commit"):
        if not isinstance(receipt.get(key), str) or not re.fullmatch(r"[0-9a-f]{40}", receipt[key]):
            raise ValueError(f"Missing committed provenance: {key}")
    if results.get("original_forecast_bytes_recovered") is not False:
        raise ValueError("Reconstruction must not claim original forecast recovery")
    expected_segments = validate_population(results["population"])
    if set(results["segments"]) != {"overall", "august_1_3", "august_4_onward"}:
        raise ValueError("Unexpected ASG segment labels")
    overall = results["segments"]["overall"]
    if integer(overall["n"], "overall") != results["population"]["endpoint_n"] or overall["n"] < 3000:
        raise ValueError("Endpoint population differs from scored rows")
    if sum(results["segments"][key]["n"] for key in ("august_1_3", "august_4_onward")) != overall["n"]:
        raise ValueError("ASG segments do not partition the endpoint cohort")
    if results["segments"]["august_1_3"]["n"] != expected_segments["early_n"] or results["segments"]["august_4_onward"]["n"] != expected_segments["later_n"]:
        raise ValueError("ASG segment counts differ from endpoint date counts")
    for name, segment in results["segments"].items():
        n = integer(segment.get("n"), name)
        model = finite(segment.get("log_loss_model"), "model LL", optional=n == 0)
        opener = finite(segment.get("log_loss_opener"), "opener LL", optional=n == 0)
        estimate = finite(segment["model_minus_opener"].get("estimate"), "paired gap", optional=n == 0)
        if n and (model < 0 or opener < 0 or not math.isclose(model - opener, estimate, rel_tol=1e-9, abs_tol=1e-10)):
            raise ValueError("Stored log losses disagree with paired gap")
        close_n = integer(segment["model_minus_close"].get("n"), "close population")
        if close_n > n:
            raise ValueError("Close population exceeds evaluated population")
        for key, empty in (("model_minus_opener", n == 0), ("model_minus_close", close_n == 0)):
            metric = segment[key]
            estimate = finite(metric.get("estimate"), key, optional=empty)
            t = finite(metric.get("registered_t"), key + " t", optional=True)
            if t is not None and (estimate is None or t * estimate < 0):
                raise ValueError("Paired estimate/t signs disagree")
            ci = metric.get("ci95")
            if ci is not None and (not isinstance(ci, list) or len(ci) != 2 or finite(ci[0], "interval lower") > finite(ci[1], "interval upper")):
                raise ValueError("Invalid paired confidence interval")
        for control in ("candidate", "placebo"):
            economics(segment["economics"][control])
    gap, t = overall["model_minus_opener"]["estimate"], overall["model_minus_opener"]["registered_t"]
    primary = gap <= .003 if results["arm"] == "fp-prospective-1" else gap <= 0 and t is not None and t <= -2
    close = overall["model_minus_close"]
    tripwire = close["estimate"] is not None and close["estimate"] < -.001 and close["registered_t"] is not None and close["registered_t"] < -3
    decision = "investigate leakage" if tripwire else "registered primary passed" if primary else "registered primary failed"
    return {"arm": results["arm"], "decision": decision, "primary_pass": bool(primary),
            "tripwire": bool(tripwire), "n": overall["n"], "gap": gap, "registered_t": t,
            "endpoint_date": results["population"]["endpoint_date"],
            "roi": overall["economics"]["candidate"]["roi"],
            "placebo_bets": overall["economics"]["placebo"]["bets"]}


def render(results, receipt, *, results_bytes=None):
    decision = review(results, receipt, results_bytes=results_bytes)
    interval = results["segments"]["overall"]["model_minus_opener"]["ci95"]
    lines = [f"# {decision['arm']}: {decision['decision']}", "",
             f"The reconstructed pinned model was scored on {decision['n']:,} eligible props through {decision['endpoint_date']}.", "",
             f"Model minus opener log loss: {decision['gap']:+.6f}; registered date-clustered t: {decision['registered_t']}; supporting 95% date-bootstrap interval: {interval}.", "",
             f"Flat-$1 EV>5% ROI: {decision['roi']}; opener-probability placebo: {decision['placebo_bets']} bets.", "",
             "These are reconstructed-recipe results. Original frozen parameter bundles and August 1–3 forecast bytes were unavailable. Fresh source data may contain revisions. The ASG panel amendment starts August 4; both segments are reported separately.", "",
             "The pinned normalization, matching, push accounting, and multiple-props-per-player betting rules are preserved, including their known defects. No live rule changes follow from this score.", "",
             "2025 has already been used repeatedly for development and calibration. The prior-use inventory is research/audits/2026-09-process-audit.md.", ""]
    return "\n".join(lines)
