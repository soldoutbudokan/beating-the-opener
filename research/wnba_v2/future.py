"""Forecast-only post-freeze history boundary; no historical study release.

Seed history ends in 2025 and may only be prepared after candidate selection.
Later updates must concern games after the freeze, with actual observation
clocks. The original development index and protected-season guards are intact.
"""
from bisect import bisect_left
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path

from research.engine import model as original
from research.engine.store import Observation, ObservationKind, ProtectedDataError, TimeBasis, canonical_json
from .model import ParticipationModel


def instant(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Candidate freeze must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


class FutureOnlyIndex(original._HistoryIndex):
    """A separate permitted population, not a flag releasing protected data."""
    def __init__(self, observations, *, frozen_at):
        self.frozen_at = instant(frozen_at)
        if self.frozen_at.year < 2026:
            raise ValueError("Future-only WNBA study starts no earlier than 2026")
        observations = tuple(observations)
        for row in observations:
            if row.kind is not ObservationKind.HISTORICAL_OUTCOME or row.payload.get("record_type") not in ("player_box", "team_box"):
                raise ValueError("Future state accepts audited historical boxes only")
            if row.season != row.effective_at.year:
                raise ValueError("Source season and event year disagree")
            if row.season <= 2025:
                if row.available_at >= self.frozen_at:
                    raise ValueError("Seed history was not available before candidate freeze")
            else:
                if row.effective_at < self.frozen_at:
                    raise ProtectedDataError("Pre-freeze 2026+ history remains excluded")
                if row.time_basis is not TimeBasis.OBSERVED or row.observed_at is None or row.observed_at < self.frozen_at:
                    raise ValueError("Post-freeze updates require genuine observation clocks")
                if row.available_at <= row.effective_at:
                    raise ValueError("A completed game cannot be observed before its event")
        super().__init__(observations)

    def select_entity(self, entity, request):
        if request.as_of < self.frozen_at or request.tip_at <= self.frozen_at:
            raise ProtectedDataError("Future index cannot replay a pre-freeze forecast")
        if request.season != request.tip_at.year:
            raise ValueError("Forecast season and event year disagree")
        cache_key = (request.as_of, request.event_id, request.season)
        cached = self._entity_cache.get(entity)
        if cached is not None and cached[0] == cache_key:
            return cached[1]
        latest = {}
        stop = bisect_left(self.clocks.get(entity, []), request.as_of)
        for row in self.by_entity.get(entity, [])[:stop]:
            if row.event_id == request.event_id or row.effective_at >= request.as_of:
                continue
            latest[(row.source_id, row.record_id)] = row
        result = tuple(latest[key] for key in sorted(latest))
        self._entity_cache[entity] = (cache_key, result)
        return result


class FutureOnlyModel(ParticipationModel):
    def __init__(self, observations, recipe, *, frozen_at):
        # Validate the candidate normally; substitute only this new recipe's
        # explicit future population. No original index is changed globally.
        super().__init__((), recipe)
        self.index = FutureOnlyIndex(observations, frozen_at=frozen_at)
        self.frozen_at = self.index.frozen_at

    def predict(self, request, *, include_manifest=True):
        result = super().predict(request, include_manifest=include_manifest)
        result["future_only_boundary"] = {
            "frozen_at": self.frozen_at.isoformat(), "seed_last_season": 2025,
            "pre_freeze_2026_history_allowed": False,
            "parameter_refits": 0, "independent_performance_claim": False}
        return result


def observation_record(observation):
    """Exact versioned payload and source clocks for private durable restoration."""
    record = observation.manifest_entry()
    record["season"] = observation.season
    record["payload"] = json.loads(canonical_json(observation.payload))
    record["retrieved_at"] = observation.retrieved_at.isoformat() if observation.retrieved_at else None
    return record


def observation_from_record(record):
    allowed = {"source_id", "record_id", "entity_id", "event_id", "season", "kind", "effective_at", "payload",
               "observed_at", "published_at", "retrieved_at", "required_at", "time_basis",
               "assumed_available_at", "time_note", "payload_hash", "available_at"}
    if set(record) != allowed:
        raise ValueError("Frozen observation fields differ from the exact schema")
    args = {k: v for k, v in record.items() if k != "available_at"}
    for key in ("effective_at", "observed_at", "published_at", "retrieved_at", "assumed_available_at"):
        args[key] = datetime.fromisoformat(args[key].replace("Z", "+00:00")) if args[key] is not None else None
    args["required_at"] = tuple(datetime.fromisoformat(v.replace("Z", "+00:00")) for v in args["required_at"])
    observation = Observation(**args)
    claimed = datetime.fromisoformat(record["available_at"].replace("Z", "+00:00"))
    if observation.available_at != claimed:
        raise ValueError("Frozen observation availability does not reproduce")
    return observation


def write_observations(path, observations):
    with Path(path).open("xb") as target:
        with gzip.GzipFile(fileobj=target, mode="wb", filename="", mtime=0) as stream:
            for observation in sorted(observations, key=lambda r: (r.available_at, r.source_id, r.record_id)):
                stream.write((canonical_json(observation_record(observation)) + "\n").encode())


def load_observations(path):
    with gzip.open(path, "rt") as stream:
        return tuple(observation_from_record(json.loads(line)) for line in stream if line.strip())
