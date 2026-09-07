"""Immutable, versioned inputs with a strict historical information boundary.

This module is infrastructure, not a historical availability claim. Adapters
must resolve identities and seasons from source records and distinguish a
publisher/observer clock from today's retrieval clock. An explicitly assumed
availability clock is supported and remains visible in every input manifest.
Only identities and clocks enter ForecastRequest; quotes and outcomes belong
to a separate scoring boundary.

There is intentionally no receipt, current-date, or release flag that enables
2026 development. A future implementation may add verified registered-arm
releases after the original experiments have actually been scored.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import math
from types import MappingProxyType
from typing import Any, Iterable, Mapping


class ObservationKind(str, Enum):
    FEATURE = "feature"
    NEWS = "news"
    EXPECTED_ROSTER = "expected_roster"
    HISTORICAL_OUTCOME = "historical_outcome"
    FINAL_ROSTER = "final_roster"


class TimeBasis(str, Enum):
    OBSERVED = "observed"
    ASSUMED = "assumed"


class ProtectedDataError(ValueError):
    """Development attempted to consume an unreleased protected season."""


class ConflictingObservationError(ValueError):
    """One source record/version was assigned incompatible contents."""


def _instant(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name}: an aware datetime is required")
    return value.astimezone(timezone.utc)


def _identifier(value: str, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name}: a nonempty, unpadded source identity is required")
    return value


def _season(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1900 <= value <= 2200:
        raise ValueError("season must be an explicit integer source season")
    return value


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("payload keys must be strings")
        return MappingProxyType({key: _freeze(value[key]) for key in sorted(value)})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ValueError("payload must contain only finite JSON values")


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def canonical_json(value: Any) -> str:
    """Stable JSON used for hashes; rejects unsupported and nonfinite values."""
    return json.dumps(_plain(_freeze(value)), sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False)


def payload_digest(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _stamp(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class Observation:
    """One version of one resolved source record.

    ``record_id`` stays stable across revisions. Each revision's availability
    is the latest of all its known required clocks. For observed evidence an
    observation clock is mandatory. For historical data with unknown arrival,
    ``time_basis='assumed'``, ``assumed_available_at`` and ``time_note`` must be
    explicit; the known clocks still cannot be overridden by the assumption.

    ``effective_at`` describes the event/information, not its publication.
    News about a future absence may be consumed before its effective time.
    Outcomes and final rosters require a completed historical effective time.
    ``retrieved_at`` records downloading separately and does not enter model
    input hashes; it cannot silently backdate an observation.
    """

    source_id: str
    record_id: str
    entity_id: str
    event_id: str
    season: int
    kind: ObservationKind
    effective_at: datetime
    payload: Mapping[str, Any]
    observed_at: datetime | None = None
    published_at: datetime | None = None
    retrieved_at: datetime | None = None
    required_at: tuple[datetime, ...] = ()
    time_basis: TimeBasis = TimeBasis.OBSERVED
    assumed_available_at: datetime | None = None
    time_note: str = ""
    payload_hash: str | None = None
    available_at: datetime = field(init=False)

    def __post_init__(self) -> None:
        for name in ("source_id", "record_id", "entity_id", "event_id"):
            _identifier(getattr(self, name), name)
        _season(self.season)
        object.__setattr__(self, "kind", ObservationKind(self.kind))
        object.__setattr__(self, "time_basis", TimeBasis(self.time_basis))
        object.__setattr__(self, "effective_at", _instant(self.effective_at, "effective_at"))
        for name in ("observed_at", "published_at", "retrieved_at", "assumed_available_at"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _instant(value, name))
        required = tuple(sorted(_instant(value, "required_at") for value in self.required_at))
        object.__setattr__(self, "required_at", required)
        if not isinstance(self.time_note, str):
            raise ValueError("time_note must be text")
        if self.time_basis is TimeBasis.OBSERVED:
            if self.observed_at is None:
                raise ValueError("observed evidence requires observed_at; retrieval is not observation")
            if self.assumed_available_at is not None:
                raise ValueError("assumed_available_at requires time_basis='assumed'")
        elif self.assumed_available_at is None or not self.time_note.strip():
            raise ValueError("assumed timing requires assumed_available_at and an explicit time_note")
        clocks = [value for value in (self.observed_at, self.published_at,
                  self.assumed_available_at, *required) if value is not None]
        object.__setattr__(self, "available_at", max(clocks))
        if not isinstance(self.payload, Mapping):
            raise ValueError("payload must be a JSON object")
        frozen = _freeze(self.payload)
        digest = payload_digest(frozen)
        if self.payload_hash is not None and self.payload_hash != digest:
            raise ValueError("payload_hash disagrees with canonical source payload")
        object.__setattr__(self, "payload", frozen)
        object.__setattr__(self, "payload_hash", digest)

    def manifest_entry(self) -> dict[str, Any]:
        """Return a fresh JSON object for the exact consumed record version."""
        return {
            "source_id": self.source_id, "record_id": self.record_id,
            "entity_id": self.entity_id, "event_id": self.event_id,
            "season": self.season, "kind": self.kind.value,
            "observed_at": _stamp(self.observed_at),
            "published_at": _stamp(self.published_at),
            "effective_at": _stamp(self.effective_at),
            "available_at": _stamp(self.available_at),
            "required_at": [_stamp(value) for value in self.required_at],
            "time_basis": self.time_basis.value,
            "assumed_available_at": _stamp(self.assumed_available_at),
            "time_note": self.time_note, "payload_hash": self.payload_hash,
        }


@dataclass(frozen=True, slots=True)
class ForecastRequest:
    """A pregame identity and cutoff, with no markets or feature/outcome bags.

    Source adapters supply these identities before observing the final roster.
    Only the requested player, team and opponent are queried by default.
    ``season`` is a source identity attribute, not inferred from a caller's
    comparison dates. Both season and event time enforce protected data rules.
    """

    entity_id: str
    event_id: str
    team_id: str
    opponent_id: str
    season: int
    tip_at: datetime
    as_of: datetime

    def __post_init__(self) -> None:
        for name in ("entity_id", "event_id", "team_id", "opponent_id"):
            _identifier(getattr(self, name), name)
        _season(self.season)
        if self.team_id == self.opponent_id:
            raise ValueError("team_id and opponent_id must differ")
        object.__setattr__(self, "tip_at", _instant(self.tip_at, "tip_at"))
        object.__setattr__(self, "as_of", _instant(self.as_of, "as_of"))
        if self.as_of >= self.tip_at:
            raise ValueError("as_of must strictly precede the scheduled tip")

    def manifest_entry(self) -> dict[str, Any]:
        return {"entity_id": self.entity_id, "event_id": self.event_id,
                "team_id": self.team_id, "opponent_id": self.opponent_id,
                "season": self.season, "tip_at": _stamp(self.tip_at),
                "as_of": _stamp(self.as_of)}


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Immutable consumed evidence and its canonical identity-bound manifest."""

    request: ForecastRequest
    observations: tuple[Observation, ...]
    input_manifest: Mapping[str, Any] = field(init=False)
    manifest_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.request, ForecastRequest):
            raise TypeError("snapshot requires ForecastRequest")
        if self.request.season == 2026 or self.request.tip_at.year == 2026:
            raise ProtectedDataError("2026 development remains protected; no verified releases exist")
        observations = tuple(self.observations)
        for item in observations:
            if not isinstance(item, Observation):
                raise TypeError("snapshot accepts only Observation records")
            if item.available_at >= self.request.as_of:
                raise ValueError("every consumed source clock must strictly precede as_of")
            if item.kind in (ObservationKind.HISTORICAL_OUTCOME, ObservationKind.FINAL_ROSTER):
                if item.event_id == self.request.event_id or item.effective_at >= self.request.as_of:
                    raise ValueError("target or unfinished outcomes/rosters cannot enter a forecast")
                if item.effective_at.year == 2026:
                    raise ProtectedDataError("2026 outcomes remain protected regardless of season label")
            if item.season == 2026:
                raise ProtectedDataError("2026 evidence remains protected regardless of cutoff")
        object.__setattr__(self, "observations", observations)
        entries = [observation.manifest_entry() for observation in self.observations]
        manifest = {"schema": "point-in-time-inputs-v1",
                    "request": self.request.manifest_entry(), "observations": entries}
        object.__setattr__(self, "input_manifest", _freeze(manifest))
        object.__setattr__(self, "manifest_hash", payload_digest(manifest))

    @property
    def uses_assumed_timing(self) -> bool:
        return any(item.time_basis is TimeBasis.ASSUMED for item in self.observations)


class PointInTimeStore:
    """An immutable collection; ``with_observations`` creates a new version.

    Equivalent duplicate records are idempotent. Different payloads/identities
    under the same source, record ID and availability timestamp are ambiguous
    and fail closed. Revised versions need a later availability timestamp.
    Query order is deterministic and does not depend on input row ordering.
    """

    __slots__ = ("_observations",)

    def __init__(self, observations: Iterable[Observation] = ()) -> None:
        versions: dict[tuple[str, str, datetime], Observation] = {}
        for item in observations:
            if not isinstance(item, Observation):
                raise TypeError("PointInTimeStore accepts only Observation records")
            key = (item.source_id, item.record_id, item.available_at)
            previous = versions.get(key)
            if previous is not None:
                if previous.manifest_entry() != item.manifest_entry():
                    raise ConflictingObservationError(f"conflicting source version: {key}")
                # Download metadata remains separate from the content identity.
                # Keep the earliest known retrieval deterministically.
                if previous.retrieved_at is not None and (
                        item.retrieved_at is None or previous.retrieved_at <= item.retrieved_at):
                    continue
            versions[key] = item
        object.__setattr__(self, "_observations", tuple(versions[key] for key in sorted(versions)))

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("PointInTimeStore is immutable; use with_observations")

    @property
    def observations(self) -> tuple[Observation, ...]:
        return self._observations

    def with_observations(self, observations: Iterable[Observation]) -> "PointInTimeStore":
        return PointInTimeStore((*self._observations, *observations))

    def snapshot(self, request: ForecastRequest, *,
                 source_ids: Iterable[str] | None = None) -> Snapshot:
        """Select the latest strictly available version per source record.

        Outcomes/final rosters for the target are excluded even when a source
        falsely claims a pregame timestamp. Other completed historical records
        need ``effective_at < as_of`` in addition to every availability clock.
        Expected rosters/news may legitimately have a future effective time.
        There is no candidate filter based on whether a player ultimately
        appeared in the target game's box score or final roster.
        """
        if not isinstance(request, ForecastRequest):
            raise TypeError("snapshot requires ForecastRequest")
        if request.season == 2026 or request.tip_at.year == 2026:
            raise ProtectedDataError("2026 development remains protected; no verified releases exist")
        sources = None if source_ids is None else frozenset(
            _identifier(value, "source_ids") for value in source_ids)
        entities = frozenset((request.entity_id, request.team_id, request.opponent_id))
        latest: dict[tuple[str, str], Observation] = {}
        completed = (ObservationKind.HISTORICAL_OUTCOME, ObservationKind.FINAL_ROSTER)
        for item in self._observations:
            if item.available_at < request.as_of:
                latest[(item.source_id, item.record_id)] = item
        selected = []
        for key in sorted(latest):
            item = latest[key]
            if item.entity_id not in entities or (sources is not None and item.source_id not in sources):
                continue
            if item.kind in completed:
                if item.event_id == request.event_id or item.effective_at >= request.as_of:
                    continue
            if item.season == 2026 or (item.kind in completed and item.effective_at.year == 2026):
                raise ProtectedDataError("consumed 2026 evidence remains protected regardless of cutoff")
            selected.append(item)
        return Snapshot(request=request, observations=tuple(selected))
