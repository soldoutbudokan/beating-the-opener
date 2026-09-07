"""Synthetic provider-to-frozen-forecast capture, with no network requests."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch
import urllib.parse

from research.wnba_v2 import capture, shadow
from research.wnba_v2.future import FutureOnlyModel
from tests.test_wnba_v2_model import recipe

NOW = datetime(2026, 9, 8, 16, tzinfo=timezone.utc)
FREEZE = NOW - timedelta(hours=1)
TIP = NOW + timedelta(hours=4)


def bp_event():
    return {"id": 100, "scheduled": "2026-09-08 20:00:00", "home": "NYL", "visitor": "LAS", "status": "scheduled"}


def espn_event():
    return {"id": "401", "date": shadow.stamp(TIP), "status": {"type": {"state": "pre"}},
        "competitions": [{"id": "401", "competitors": [
            {"homeAway": "home", "team": {"id": "9", "abbreviation": "NY"}},
            {"homeAway": "away", "team": {"id": "3", "abbreviation": "LA"}}]}]}


def offer():
    return {"id": 500, "event_id": 100, "market_id": 393, "player_id": 111, "active": True,
        "participants": [{"player": {"id": 111, "first_name": "Test", "last_name": "Player", "team": "NYL"}}],
        "selections": [{"selection": side, "active": True, "books": [
            {"id": book, "lines": [{"line": 5.5, "cost": -110, "active": True, "main": True,
                "is_off": False, "updated": "2026-09-08 15:59:00"}]} for book in (10, 14)]}
            for side in ("over", "under")]}


class Response(io.BytesIO):
    def __init__(self, value, status=200):
        super().__init__(shadow.encode(value))
        self.status = status
        self.headers = {"Content-Type": "application/json", "Date": "Tue, 08 Sep 2026 16:00:00 GMT"}


class Tick:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        result = self.now
        self.now += timedelta(milliseconds=1)
        return result


class FixtureOpener:
    def __init__(self, *, events=True, offered=None, offer_status=200, pages=1):
        self.events, self.offered, self.offer_status, self.pages = events, offered, offer_status, pages
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        parsed = urllib.parse.urlsplit(request.full_url)
        query = urllib.parse.parse_qs(parsed.query)
        if parsed.path.endswith("/events"):
            return Response({"events": [bp_event()] if self.events else []})
        if parsed.path.endswith("/scoreboard"):
            return Response({"events": [espn_event()] if query["dates"] == ["20260908"] else []})
        if parsed.path.endswith("/roster"):
            team = parsed.path.split("/")[-2]
            return Response({"team": {"id": team}, "athletes": [{"id": "123", "fullName": "Test Player"}] if team == "9" else []})
        if parsed.path.endswith("/offers"):
            if query.get("page") == ["2"]:
                return Response({"error": "temporarily unavailable"}, 503)
            return Response({"offers": [offer()] if self.offered is None else self.offered,
                "_pagination": {"total_pages": self.pages, "total_items": 1 if self.pages == 1 else 2}}, self.offer_status)
        raise AssertionError("Unexpected synthetic request")


def ledger_model(directory):
    model = FutureOnlyModel([], recipe(), frozen_at=FREEZE)
    ledger = shadow.Ledger(Path(directory) / "ledger")
    ledger.initialize({"schema": "wnba-shadow-study-v1", "study_id": "fixture-study", "candidate_id": "fixture-candidate",
        "market": "points", "mode": "shadow", "bundle_sha256": "a" * 64, "recipe_sha256": "b" * 64,
        "model_recipe_hash": model.recipe_hash, "implementation_sha256": "c" * 64,
        "registration_commit": "d" * 40, "frozen_at": shadow.stamp(FREEZE),
        "bundle_sealed_at": shadow.stamp(FREEZE), "starts_at": shadow.stamp(FREEZE),
        "bundle_library_file_id": "fixture", "bundle_library_version_id": "0"}, NOW)
    return ledger, model


class WnbaV2CaptureTests(unittest.TestCase):
    def test_full_current_capture_stages_both_books_and_actual_model_output(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(capture, "bp_key", return_value="fixture-secret"):
            ledger, model = ledger_model(directory)
            opener = FixtureOpener()
            result = capture.scan(model, ledger, "run-one", repo=Path(directory), clock=Tick(),
                                  opener=opener, sleeper=lambda _: None)
            self.assertEqual((result["capture_status"], result["forecasts"]), ("OK", 2))
            self.assertEqual(result["status"], "AWAITING_DURABLE_RECEIPT")
            manifest, rows = ledger.batch("run-one", NOW + timedelta(minutes=1))
            self.assertEqual([row["quote"]["book_id"] for row in rows], [10, 14])
            self.assertEqual(rows[0]["player_id"], "123")
            self.assertEqual(rows[0]["game_id"], "401")
            self.assertEqual(rows[0]["model_output"]["schema"], "wnba-v2-forecast-v1")
            self.assertTrue(rows[0]["mapping_evidence"]["rule"].startswith("unique_exact"))
            self.assertFalse((ledger.root / "batches/run-one/seal.json").exists())
            self.assertEqual(len([name for name in manifest["files"] if name.startswith("raw/")]), 6)
            artifacts = b"".join(path.read_bytes() for path in ledger.root.rglob("*") if path.is_file())
            self.assertNotIn(b"fixture-secret", artifacts)

    def test_no_events_is_a_real_preserved_empty_batch(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(capture, "bp_key", return_value="fixture"):
            ledger, model = ledger_model(directory)
            opener = FixtureOpener(events=False)
            result = capture.scan(model, ledger, "empty", repo=directory, clock=lambda: NOW,
                                  opener=opener, sleeper=lambda _: None)
            self.assertEqual((result["capture_status"], result["forecasts"]), ("NO_EVENTS", 0))
            self.assertEqual(len(opener.requests), 1)
            manifest, rows = ledger.batch("empty", NOW)
            self.assertEqual(rows, [])
            self.assertIn("raw/response-0001.json", manifest["files"])

    def test_missing_key_is_recorded_as_failure_not_no_events(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(capture, "bp_key", side_effect=ValueError("missing key")):
            ledger, model = ledger_model(directory)
            result = capture.scan(model, ledger, "key-failed", repo=directory, clock=lambda: NOW, sleeper=lambda _: None)
            self.assertEqual(result["capture_status"], "DEGRADED")
            self.assertEqual(result["forecasts"], 0)
            self.assertTrue((ledger.root / "batches/key-failed/attempts.jsonl").exists())

    def test_failed_pagination_preserves_all_responses_and_never_prices_partial_population(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(capture, "bp_key", return_value="fixture"):
            ledger, model = ledger_model(directory)
            result = capture.scan(model, ledger, "page-failed", repo=directory, clock=lambda: NOW,
                                  opener=FixtureOpener(pages=2), sleeper=lambda _: None)
            self.assertEqual((result["capture_status"], result["forecasts"]), ("DEGRADED", 0))
            attempts = [json.loads(line) for line in (ledger.root / "batches/page-failed/attempts.jsonl").read_text().splitlines()]
            self.assertEqual(sum(a.get("http_status") == 503 for a in attempts), 3)
            self.assertTrue(all("snapshot_sha256" in a for a in attempts if a.get("http_status") == 503))

    def test_mapping_requires_unique_current_roster_name_and_preserves_punctuation(self):
        event = dict(capture.map_event(bp_event(), [espn_event()]), source_snapshot_sha256="a" * 64, observed_at=shadow.stamp(NOW))
        raw = offer()
        roster = {"9": {"team": {"id": "9"}, "athletes": [{"id": "123", "fullName": "Test Player"}]}}
        self.assertEqual(capture.map_player(raw, event, roster)["player_id"], "123")
        roster["9"]["athletes"].append({"id": "124", "fullName": "Test Player"})
        with self.assertRaisesRegex(ValueError, "AMBIGUOUS"):
            capture.map_player(raw, event, roster)
        roster["9"]["athletes"] = [{"id": "123", "fullName": "Test-Player"}]
        with self.assertRaisesRegex(ValueError, "AMBIGUOUS"):
            capture.map_player(raw, event, roster)

    def test_explicit_crosswalk_must_resolve_in_current_team_and_never_falls_back(self):
        event = capture.map_event(bp_event(), [espn_event()])
        raw = offer()
        raw["participants"][0]["player"]["espn_id"] = "999"
        roster = {"9": {"team": {"id": "9"}, "athletes": [{"id": "123", "fullName": "Test Player"}]}}
        with self.assertRaisesRegex(ValueError, "AMBIGUOUS"):
            capture.map_player(raw, event, roster)
        raw["participants"][0]["player"]["espn_id"] = "123"
        self.assertTrue(capture.map_player(raw, event, roster)["mapping_evidence"]["rule"].startswith("explicit_bp"))

    def test_event_matching_requires_exact_tip_fixed_codes_and_unique_game(self):
        raw = bp_event()
        changed = deepcopy(espn_event())
        changed["date"] = shadow.stamp(TIP + timedelta(minutes=1))
        with self.assertRaisesRegex(ValueError, "AMBIGUOUS"):
            capture.map_event(raw, [changed])
        self.assertEqual(capture.map_event(raw, [espn_event(), espn_event()])["game_id"], "401")
        changed = deepcopy(espn_event())
        changed["id"] = changed["competitions"][0]["id"] = "402"
        with self.assertRaisesRegex(ValueError, "AMBIGUOUS"):
            capture.map_event(raw, [espn_event(), changed])
        raw["home"] = "unregistered-alias"
        with self.assertRaisesRegex(ValueError, "UNMAPPED"):
            capture.map_event(raw, [espn_event()])

    def test_bad_paired_quote_clocks_lines_and_duplicate_main_lines_are_retained(self):
        receipt = {"received_at": shadow.stamp(NOW), "snapshot_sha256": "a" * 64}
        for field, value in (("updated", "2026-09-08 16:00:01"), ("updated", "2026-09-08 13:00:00"), ("line", 6.5)):
            raw = offer()
            raw["selections"][1]["books"][0]["lines"][0][field] = value
            rows = capture.quote_pairs(raw, receipt)
            self.assertEqual(len(rows), 2)
            self.assertIn("reason", rows[0])
            self.assertIn("quote", rows[1])
        raw = offer()
        raw["selections"][0]["books"][0]["lines"] *= 2
        self.assertIn("reason", capture.quote_pairs(raw, receipt)[0])

    def test_missing_or_null_activity_flags_never_become_active_quotes(self):
        receipt = {"received_at": shadow.stamp(NOW), "snapshot_sha256": "a" * 64}
        for level, field in (("offer", "active"), ("selection", "active"),
                             ("line", "active"), ("line", "main"), ("line", "is_off")):
            for missing in (True, False):
                with self.subTest(level=level, field=field, missing=missing):
                    raw = offer()
                    obj = raw if level == "offer" else raw["selections"][0] if level == "selection" else raw["selections"][0]["books"][0]["lines"][0]
                    if missing:
                        obj.pop(field)
                    else:
                        obj[field] = None
                    self.assertIn("reason", capture.quote_pairs(raw, receipt)[0])

    def test_unknown_player_mapping_records_both_book_exclusions(self):
        raw = offer()
        raw["participants"][0]["player"]["last_name"] = "Other"
        with tempfile.TemporaryDirectory() as directory, patch.object(capture, "bp_key", return_value="fixture"):
            ledger, model = ledger_model(directory)
            result = capture.scan(model, ledger, "unmapped", repo=directory, clock=lambda: NOW,
                                  opener=FixtureOpener(offered=[raw]), sleeper=lambda _: None)
            self.assertEqual((result["capture_status"], result["forecasts"], result["excluded_quote_requests"]), ("DEGRADED", 0, 2))

    def test_invalid_quote_identity_is_preserved_without_aborting_batch(self):
        raw = offer()
        raw["id"] = "invalid-id"
        with tempfile.TemporaryDirectory() as directory, patch.object(capture, "bp_key", return_value="fixture"):
            ledger, model = ledger_model(directory)
            result = capture.scan(model, ledger, "bad-id", repo=directory, clock=lambda: NOW,
                                  opener=FixtureOpener(offered=[raw]), sleeper=lambda _: None)
            self.assertEqual((result["forecasts"], result["excluded_quote_requests"]), (0, 2))
            self.assertEqual(result["capture_status"], "DEGRADED")

    def test_duplicate_detection_is_per_active_player_game_book(self):
        first, second = offer(), offer()
        second["id"] = 501
        for selection in first["selections"]:
            selection["books"] = selection["books"][:1]
        for selection in second["selections"]:
            selection["books"] = selection["books"][1:]
        fixture = FixtureOpener(offered=[first, second])
        original = fixture.__call__
        def opener(request, timeout):
            response = original(request, timeout)
            if "/offers?" in request.full_url:
                value = json.loads(response.getvalue())
                value["_pagination"]["total_items"] = 2
                return Response(value)
            return response
        with tempfile.TemporaryDirectory() as directory, patch.object(capture, "bp_key", return_value="fixture"):
            ledger, model = ledger_model(directory)
            result = capture.scan(model, ledger, "split-book", repo=directory, clock=Tick(),
                                  opener=opener, sleeper=lambda _: None)
            self.assertEqual((result["capture_status"], result["forecasts"]), ("OK", 2))

    def test_identity_receipt_equal_to_cutoff_is_excluded(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(capture, "bp_key", return_value="fixture"):
            ledger, model = ledger_model(directory)
            result = capture.scan(model, ledger, "equal-clock", repo=directory, clock=lambda: NOW,
                                  opener=FixtureOpener(), sleeper=lambda _: None)
            self.assertEqual((result["capture_status"], result["forecasts"]), ("DEGRADED", 0))
            attempts = (ledger.root / "batches/equal-clock/attempts.jsonl").read_text()
            self.assertIn("IDENTITY_EVIDENCE_NOT_BEFORE_FORECAST_CUTOFF", attempts)

    def test_event_and_competition_cannot_have_conflicting_clocks_or_status(self):
        for key, value in (("date", shadow.stamp(TIP + timedelta(seconds=1))),
                           ("status", {"type": {"state": "post"}})):
            event = espn_event()
            event["competitions"][0][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "CONFLICT"):
                capture.map_event(bp_event(), [event])

    def test_scan_refuses_model_recipe_or_freeze_mismatch_before_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger, model = ledger_model(directory)
            model.recipe_hash = "f" * 64
            with self.assertRaisesRegex(ValueError, "recipe identity"):
                capture.scan(model, ledger, "wrong", repo=directory, clock=lambda: NOW)
            self.assertFalse((ledger.root / "captures/wrong").exists())

    def test_standalone_cli_refuses_seed_without_frozen_hash_before_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recipe_file, seed = root / "recipe.json", root / "seed.gz"
            recipe_file.write_bytes(shadow.encode(recipe()))
            seed.write_bytes(b"alternate seed bytes")
            argv = ["capture", "--repo", directory, "--ledger", directory, "--recipe", str(recipe_file),
                    "--seed", str(seed), "--run-id", "cli-test"]
            study = {"recipe_sha256": shadow.digest(recipe_file.read_bytes())}
            with patch.object(sys, "argv", argv), patch.object(shadow.Ledger, "study", return_value=study), \
                    patch("research.wnba_v2.future.load_observations", side_effect=AssertionError("must not load unbound seed")):
                with self.assertRaisesRegex(ValueError, "Seed file differs"):
                    capture.main()

    def test_recorder_refuses_external_urls_and_credentials_in_query(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = capture.Recorder(Path(directory) / "recorded", clock=lambda: NOW, sleeper=lambda _: None)
            for url, params in (("https://example.com/data", {}), (capture.BP + "/events", {"api_key": "secret"})):
                with self.assertRaisesRegex(capture.CollectionError, "UNAPPROVED"):
                    recorder.get(url, params, lambda _: None)
            self.assertEqual(recorder.attempts, [])

    def test_backward_local_receipt_clock_preserves_failure_and_exact_body(self):
        clocks = iter((NOW, NOW - timedelta(milliseconds=1), NOW))
        with tempfile.TemporaryDirectory() as directory:
            recorder = capture.Recorder(Path(directory) / "clock-failure", clock=lambda: next(clocks),
                opener=lambda request, timeout: Response({"events": []}), tries=1, sleeper=lambda _: None)
            with self.assertRaisesRegex(capture.CollectionError, "REQUEST_FAILED"):
                recorder.get(capture.BP + "/events", {}, capture.validate_bp_events)
            self.assertEqual(recorder.attempts[0]["reason"], "CAPTURE_CLOCK_MOVED_BACKWARD")
            self.assertEqual(recorder.snapshots["response-0001.json"], shadow.encode({"events": []}))

    def test_name_normalization_is_limited_to_registered_operations(self):
        self.assertEqual(capture.exact_name("  TEST\tPLAYER  "), "test player")
        self.assertNotEqual(capture.exact_name("Test-Player"), capture.exact_name("Test Player"))
        self.assertNotEqual(capture.exact_name("T. Player"), capture.exact_name("Test Player"))


if __name__ == "__main__":
    unittest.main()
