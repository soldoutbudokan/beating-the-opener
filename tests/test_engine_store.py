"""Synthetic boundary tests; these establish no empirical forecast result."""

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
import itertools
import unittest

from research.engine.store import (
    ConflictingObservationError, ForecastRequest, Observation, ObservationKind,
    PointInTimeStore, ProtectedDataError, Snapshot, TimeBasis, canonical_json,
    payload_digest,
)


UTC = timezone.utc
AS_OF = datetime(2025, 7, 1, 15, tzinfo=UTC)


def request(**changes):
    fields = dict(entity_id="player-a", event_id="target", team_id="team-a",
                  opponent_id="team-b", season=2025,
                  tip_at=AS_OF + timedelta(hours=4), as_of=AS_OF)
    fields.update(changes)
    return ForecastRequest(**fields)


def observation(**changes):
    fields = dict(source_id="box", record_id="row-1", entity_id="player-a",
                  event_id="previous", season=2025,
                  kind=ObservationKind.HISTORICAL_OUTCOME,
                  effective_at=AS_OF - timedelta(days=3),
                  observed_at=AS_OF - timedelta(days=2),
                  payload={"minutes": 24, "points": 12})
    fields.update(changes)
    return Observation(**fields)


def software_fixture_forecast(store):
    """A transparent arithmetic witness, not the research candidate model."""
    snapshot = store.snapshot(request())
    values = [item.payload["minutes"] for item in snapshot.observations
              if item.kind is ObservationKind.HISTORICAL_OUTCOME]
    return (sum(values) / len(values) if values else None), snapshot.manifest_hash


class ImmutableContracts(unittest.TestCase):
    def test_nested_payload_and_manifest_are_immutable_copies(self):
        payload = {"nested": {"players": ["a", "b"]}, "minutes": 24}
        item = observation(payload=payload)
        payload["nested"]["players"].append("c")
        self.assertEqual(item.payload["nested"]["players"], ("a", "b"))
        with self.assertRaises(TypeError):
            item.payload["minutes"] = 80
        with self.assertRaises(TypeError):
            item.payload["nested"]["players"][0] = "c"
        with self.assertRaises(FrozenInstanceError):
            item.entity_id = "someone-else"
        store = PointInTimeStore([item])
        with self.assertRaises(AttributeError):
            store._observations = ()
        snapshot = store.snapshot(request())
        with self.assertRaises(TypeError):
            snapshot.input_manifest["request"]["entity_id"] = "someone-else"
        self.assertEqual(snapshot.manifest_hash, payload_digest(snapshot.input_manifest))
        entry = item.manifest_entry()
        entry["entity_id"] = "someone-else"
        self.assertEqual(item.entity_id, "player-a")

    def test_hashes_are_canonical_and_validate_source_payload(self):
        self.assertEqual(payload_digest({"b": [2, 3], "a": 1}),
                         payload_digest({"a": 1, "b": (2, 3)}))
        for invalid in (float("nan"), float("inf"), object(), {1: "bad"}):
            with self.subTest(invalid=type(invalid).__name__):
                with self.assertRaises(ValueError):
                    observation(payload={"value": invalid})
        with self.assertRaises(ValueError):
            observation(payload_hash="0" * 64)
        item = observation()
        self.assertEqual(observation(payload_hash=item.payload_hash), item)
        self.assertNotEqual(item.payload_hash, observation(payload={"minutes": 25}).payload_hash)

    def test_forecast_request_has_no_market_outcome_or_feature_bag(self):
        for field in ("line", "price", "market", "outcome", "features", "final_roster", "receipt"):
            with self.subTest(field=field):
                with self.assertRaises(TypeError):
                    request(**{field: {"value": 1}})
        for changes in ({"as_of": AS_OF.replace(tzinfo=None)},
                        {"tip_at": AS_OF}, {"entity_id": " "},
                        {"team_id": "team-b"}, {"season": True}):
            with self.assertRaises(ValueError):
                request(**changes)
        with self.assertRaises(TypeError):
            PointInTimeStore().snapshot({"as_of": AS_OF})


class AvailabilityContracts(unittest.TestCase):
    def test_latest_required_clock_controls_strict_boundary(self):
        item = observation(observed_at=AS_OF - timedelta(hours=2),
                           published_at=AS_OF - timedelta(hours=1),
                           required_at=(AS_OF, AS_OF - timedelta(minutes=20)))
        self.assertEqual(item.available_at, AS_OF)
        self.assertEqual(PointInTimeStore([item]).snapshot(request()).observations, ())
        before = replace(item, required_at=(AS_OF - timedelta(microseconds=1),))
        selected = PointInTimeStore([before]).snapshot(request()).observations
        self.assertEqual(selected, (before,))
        for consumed in selected:
            for clock in (consumed.observed_at, consumed.published_at,
                          consumed.available_at, *consumed.required_at):
                self.assertLess(clock, AS_OF)
        for name in ("observed_at", "effective_at", "retrieved_at", "published_at"):
            with self.assertRaises(ValueError):
                observation(**{name: AS_OF.replace(tzinfo=None)})

    def test_future_effective_news_is_usable_but_future_outcomes_are_not(self):
        future = AS_OF + timedelta(hours=4)
        news = observation(record_id="news", kind="news", event_id="target",
                           effective_at=future, payload={"status": "out"})
        lineup = observation(record_id="lineup", kind="expected_roster", event_id="target",
                             effective_at=future, payload={"players": ["player-a"]})
        unfinished = observation(record_id="unfinished", effective_at=future)
        snapshot = PointInTimeStore([news, lineup, unfinished]).snapshot(request())
        self.assertEqual({row.record_id for row in snapshot.observations}, {"news", "lineup"})

    def test_assumed_historical_availability_is_explicit_and_cannot_override_known_clock(self):
        retrieved = datetime(2026, 9, 7, tzinfo=UTC)
        with self.assertRaises(ValueError):
            observation(observed_at=None, retrieved_at=retrieved)
        with self.assertRaises(ValueError):
            observation(observed_at=None, time_basis="assumed", assumed_available_at=AS_OF)
        with self.assertRaises(ValueError):
            observation(assumed_available_at=AS_OF - timedelta(hours=1))
        assumed = observation(observed_at=None, retrieved_at=retrieved, time_basis="assumed",
                              assumed_available_at=AS_OF - timedelta(days=2),
                              time_note="Fixture assumption: completed box available eight hours later")
        self.assertEqual(assumed.available_at, AS_OF - timedelta(days=2))
        snapshot = PointInTimeStore([assumed]).snapshot(request())
        self.assertTrue(snapshot.uses_assumed_timing)
        self.assertIn('"time_basis":"assumed"', canonical_json(snapshot.input_manifest))
        self.assertIsNone(snapshot.input_manifest["observations"][0]["observed_at"])
        late_known = replace(assumed, observed_at=AS_OF)
        self.assertEqual(PointInTimeStore([late_known]).snapshot(request()).observations, ())
        changed_assumption = replace(assumed, time_note="A different declared assumption")
        self.assertNotEqual(snapshot.manifest_hash,
                            PointInTimeStore([changed_assumption]).snapshot(request()).manifest_hash)
        redownloaded = replace(assumed, retrieved_at=retrieved + timedelta(days=1))
        self.assertEqual(snapshot.manifest_hash,
                         PointInTimeStore([redownloaded]).snapshot(request()).manifest_hash)


class VersionAndInvarianceContracts(unittest.TestCase):
    def test_latest_version_is_selected_only_after_it_became_available(self):
        original = observation()
        revision = replace(original, payload={"minutes": 20}, payload_hash=None,
                           observed_at=AS_OF - timedelta(hours=1))
        future_revision = replace(original, payload={"minutes": 40}, payload_hash=None,
                                  observed_at=AS_OF + timedelta(hours=1))
        store = PointInTimeStore([future_revision, original, revision])
        self.assertEqual(store.snapshot(request()).observations, (revision,))
        earlier = request(as_of=AS_OF - timedelta(days=1))
        self.assertEqual(store.snapshot(earlier).observations, (original,))
        changed_identity = replace(revision, entity_id="unrelated")
        self.assertEqual(PointInTimeStore([original, changed_identity]).snapshot(request()).observations, ())

    def test_duplicates_idempotent_but_conflicting_source_versions_fail(self):
        item = observation()
        self.assertEqual(PointInTimeStore([item, item]).snapshot(request()).observations, (item,))
        for changes in ({"payload": {"minutes": 90}, "payload_hash": None},
                        {"event_id": "different"}, {"effective_at": AS_OF}):
            conflict = replace(item, **changes)
            for rows in ([item, conflict], [conflict, item]):
                with self.assertRaises(ConflictingObservationError):
                    PointInTimeStore(rows)

    def test_future_target_outcome_final_roster_and_unrelated_rows_cannot_change_forecast(self):
        past = observation()
        another = observation(record_id="row-2", payload={"minutes": 30})
        baseline = PointInTimeStore([past, another])
        expected = software_fixture_forecast(baseline)
        future = observation(record_id="future", event_id="future", season=2027,
                             observed_at=datetime(2027, 1, 1, tzinfo=UTC),
                             effective_at=datetime(2027, 1, 1, tzinfo=UTC), payload={"minutes": 100})
        protected_future = replace(future, record_id="future-2026", season=2026,
                                   observed_at=datetime(2026, 8, 1, tzinfo=UTC))
        # Deliberately false pregame clocks test exclusion independently of timing.
        target_outcome = observation(record_id="target-outcome", event_id="target",
                                     payload={"minutes": 1000})
        target_roster = observation(record_id="target-roster", event_id="target",
                                    kind="final_roster", entity_id="team-a",
                                    payload={"players": ["some-other-player"]})
        unrelated = observation(record_id="unrelated", entity_id="player-z", payload={"minutes": 999})
        additions = [future, protected_future, target_outcome, target_roster, unrelated]
        self.assertEqual(software_fixture_forecast(baseline.with_observations(additions)), expected)
        target_changes = [replace(target_outcome, payload={"minutes": 0}, payload_hash=None),
                          replace(target_roster, payload={"players": []}, payload_hash=None)]
        self.assertEqual(software_fixture_forecast(baseline.with_observations(target_changes)), expected)
        for rows in itertools.permutations([past, another, unrelated]):
            self.assertEqual(software_fixture_forecast(PointInTimeStore(rows)), expected)
        self.assertEqual(software_fixture_forecast(PointInTimeStore([another, past, unrelated, unrelated])), expected)
        self.assertEqual(baseline.observations, (past, another))

    def test_source_filter_and_identity_are_manifest_bound(self):
        box = observation()
        news = observation(source_id="news", record_id="news-1", kind="news",
                           payload={"status": "questionable"})
        store = PointInTimeStore([news, box])
        selected = store.snapshot(request(), source_ids=["box"])
        self.assertEqual(selected.observations, (box,))
        self.assertNotEqual(selected.manifest_hash, store.snapshot(request()).manifest_hash)
        self.assertNotEqual(selected.manifest_hash,
                            store.snapshot(request(event_id="other-target"), source_ids=["box"]).manifest_hash)


class ProtectedDataContracts(unittest.TestCase):
    def test_target_season_cannot_be_unlocked_by_spoofing_comparison_dates(self):
        store = PointInTimeStore()
        for item in (request(season=2026),
                     request(season=2025, tip_at=datetime(2026, 7, 1, tzinfo=UTC)),
                     request(season=2026, tip_at=datetime(2027, 7, 1, tzinfo=UTC))):
            with self.assertRaises(ProtectedDataError):
                store.snapshot(item)
        with self.assertRaises(TypeError):
            store.snapshot(request(), release_receipt={"status": "complete"})
        with self.assertRaises(TypeError):
            store.snapshot(request(), allow_protected=True)

    def test_historical_2026_remains_protected_after_the_calendar_passes(self):
        later = request(season=2027, tip_at=datetime(2027, 7, 1, tzinfo=UTC),
                        as_of=datetime(2027, 6, 30, tzinfo=UTC))
        protected = observation(season=2026, effective_at=datetime(2026, 8, 1, tzinfo=UTC),
                                observed_at=datetime(2026, 8, 2, tzinfo=UTC))
        for item in (protected, replace(protected, season=2025),
                     replace(protected, effective_at=AS_OF)):
            with self.assertRaises(ProtectedDataError):
                PointInTimeStore([item]).snapshot(later)
        with self.assertRaises(ProtectedDataError):
            Snapshot(request=later, observations=(protected,))
        with self.assertRaises(ValueError):
            Snapshot(request=request(), observations=(observation(observed_at=AS_OF),))
        with self.assertRaises(ValueError):
            Snapshot(request=request(), observations=(observation(event_id="target"),))


if __name__ == "__main__":
    unittest.main()
