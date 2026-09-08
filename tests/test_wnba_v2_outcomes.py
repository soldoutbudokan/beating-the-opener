"""Synthetic ESPN response contracts; never touches the historical archive."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import shutil

from research.engine.store import ProtectedDataError, payload_digest
from research.wnba_v2 import outcomes
from research.wnba_v2.future import write_observations
from research.wnba_v2.shadow import digest, stamp, validate_settlement

UTC = timezone.utc
FROZEN = datetime(2026, 9, 8, 12, tzinfo=UTC)
TIP = FROZEN + timedelta(hours=5)
NOW = TIP + timedelta(hours=3)


def event():
    return {"game_id": "123", "tip_at": stamp(TIP), "season": 2026, "team_ids": ["1", "2"]}


def summary():
    status = {"period": 4, "type": {"completed": True, "state": "post", "name": "STATUS_FINAL"}}
    competitors = [{"team": {"id": tid}, "homeAway": side, "score": "80"}
                   for tid, side in (("1", "home"), ("2", "away"))]
    team_stats = [{"name": name, "displayValue": value} for name, value in (
        ("fieldGoalsMade-fieldGoalsAttempted", "30-60"), ("freeThrowsMade-freeThrowsAttempted", "15-20"),
        ("offensiveRebounds", "10"), ("totalTurnovers", "12"))]
    value = {"header": {"id": "123", "season": {"year": 2026}, "competitions": [
        {"id": "123", "date": stamp(TIP), "status": status, "competitors": competitors}]},
        "boxscore": {"teams": [{"team": {"id": tid}, "statistics": deepcopy(team_stats)} for tid in ("1", "2")],
        "players": [{"team": {"id": tid}, "statistics": [{"names": ["MIN", "PTS", "REB", "AST", "3PT"],
            "athletes": [{"athlete": {"id": pid, "position": {"abbreviation": "G"}}, "starter": True,
                          "didNotPlay": False, "stats": ["30", "18", "3", "5", "2-6"]}]}]}
                    for tid, pid in (("1", "11"), ("2", "22"))]}}
    for i, box in enumerate(value["boxscore"]["players"]):
        athletes = box["statistics"][0]["athletes"]
        for n, points in enumerate((14, 12, 12, 12, 12)):
            extra = deepcopy(athletes[0])
            extra["athlete"]["id"] = str(100 + i * 10 + n)
            extra["stats"] = ["34", str(points), "3", "2", "0-2"]
            athletes.append(extra)
    return value


def raw(value):
    return json.dumps(value).encode()


def player(body):
    return body["boxscore"]["players"][0]["statistics"][0]["athletes"][0]


def scoreboard(body=None):
    body = summary() if body is None else body
    comp = deepcopy(body["header"]["competitions"][0])
    return {"season": {"year": 2026}, "events": [{"id": "123", "date": stamp(TIP),
        "status": deepcopy(comp["status"]), "competitions": [comp]}]}


def parse(body):
    return outcomes.parse_summary(raw(body), event=event(), observed_at=NOW, frozen_at=FROZEN, now=NOW)


class FakeLedger:
    def __init__(self, forecasts=()):
        self.forecasts = list(forecasts)
        self.rows = {}
    def population(self, now):
        return self.forecasts, {}
    def settlements(self, by_id, now):
        return self.rows.copy()
    def settle(self, record, body, now):
        validate_settlement(record, {f["forecast_id"]: f for f in self.forecasts}, body, now)
        old = self.rows.get(record["forecast_id"])
        if record["supersedes"] != (old["settlement_id"] if old else None):
            raise ValueError("wrong revision parent")
        self.rows[record["forecast_id"]] = record


def forecast():
    return {"forecast_id": "forecast-1", "game_id": "123", "player_id": "11", "team_id": "1",
            "opponent_id": "2", "tip_at": stamp(TIP)}


class OutcomesTests(unittest.TestCase):
    def test_completed_observations_have_actual_receipt_availability(self):
        parsed = parse(summary())
        self.assertEqual(len(parsed["observations"]), 14)
        self.assertTrue(all(o.available_at == NOW and o.observed_at == NOW for o in parsed["observations"]))
        self.assertEqual(parsed["completion_clock_basis"], "observed_final_upper_bound")
        self.assertEqual(parsed["game_completed_at"], stamp(NOW))

    def test_in_progress_game_cannot_enter_state_or_settle(self):
        body = summary()
        body["header"]["competitions"][0]["status"]["type"] = {"completed": False, "state": "in", "name": "STATUS_IN_PROGRESS"}
        self.assertEqual(parse(body)["observations"], ())
        self.assertEqual(outcomes.scoreboard_events(raw(scoreboard(body)), observed_at=NOW, frozen_at=FROZEN), [])

    def test_pre_freeze_and_future_receipt_rejected(self):
        with self.assertRaises(ProtectedDataError):
            outcomes.parse_summary(raw(summary()), event=event(), observed_at=NOW, frozen_at=TIP + timedelta(seconds=1), now=NOW)
        with self.assertRaises(ValueError):
            outcomes.parse_summary(raw(summary()), event=event(), observed_at=NOW + timedelta(seconds=1), frozen_at=FROZEN, now=NOW)
        self.assertEqual(outcomes.scoreboard_events(raw(scoreboard()), observed_at=NOW, frozen_at=TIP + timedelta(seconds=1)), [])

    def test_played_zero_and_unknown_exposure_retain_points(self):
        for minutes in ("0", "--", "0:00"):
            body = summary()
            player(body)["stats"][0] = minutes
            parsed = parse(body)
            p = next(o.payload for o in parsed["observations"] if o.entity_id == "11")
            self.assertEqual(p["participation"], "played")
            self.assertEqual(p["counts"]["points"], 18)
            self.assertFalse(p["rate_measurement_eligible"])
            self.assertIsNone(p["minutes"])
            self.assertEqual(parsed["players"]["11"]["actual_points"], 18)

    def test_dnp_requires_explicit_flag_empty_stats_remain_unknown(self):
        for flag, expected in ((True, "dnp"), (None, "unknown"), (False, "played")):
            body = summary()
            player(body)["stats"] = []
            player(body)["didNotPlay"] = flag
            if flag is True:
                body["header"]["competitions"][0]["competitors"][0]["score"] = "62"
                for athlete in body["boxscore"]["players"][0]["statistics"][0]["athletes"][1:]:
                    athlete["stats"][0] = "40"
            parsed = parse(body)
            self.assertEqual(parsed["players"]["11"]["participation"], expected)
            self.assertIsNone(parsed["players"]["11"]["actual_points"])
            record = outcomes.settlement_records(parsed, [forecast()], {})[0]
            self.assertEqual(record["participation"], expected if expected != "played" else "unknown")
            validate_settlement(record, {"forecast-1": forecast()}, raw(body), NOW)

    def test_explicit_dnp_conflict_rejected(self):
        body = summary()
        player(body)["didNotPlay"] = True
        with self.assertRaisesRegex(ValueError, "conflicting"):
            parse(body)

    def test_mismatched_game_team_and_player_duplicates_rejected(self):
        body = summary()
        body["header"]["id"] = "999"
        with self.assertRaisesRegex(ValueError, "identity"):
            parse(body)
        body = summary()
        body["boxscore"]["teams"][0]["team"]["id"] = "3"
        with self.assertRaisesRegex(ValueError, "identity"):
            parse(body)
        body = summary()
        body["boxscore"]["players"][1]["statistics"][0]["athletes"][0]["athlete"]["id"] = "11"
        with self.assertRaisesRegex(ValueError, "duplicate player"):
            parse(body)
        with self.assertRaisesRegex(ValueError, "team identity"):
            outcomes.settlement_records(parse(summary()), [{**forecast(), "team_id": "2"}], {})

    def test_revision_retains_explicit_parent_and_duplicate_is_noop(self):
        initial = outcomes.settlement_records(parse(summary()), [forecast()], {})[0]
        self.assertIsNone(initial["supersedes"])
        previous = {"forecast-1": initial}
        self.assertEqual(outcomes.settlement_records(parse(summary()), [forecast()], previous), [])
        body = summary()
        player(body)["stats"][1] = "19"
        body["header"]["competitions"][0]["competitors"][0]["score"] = "81"
        revised = outcomes.settlement_records(parse(body), [forecast()], previous)[0]
        self.assertEqual(revised["supersedes"], initial["settlement_id"])
        self.assertNotEqual(revised["settlement_id"], initial["settlement_id"])

    def test_negative_invalid_and_partial_cells_fail_closed(self):
        for change in (lambda p: p["stats"].__setitem__(1, "-1"),
                       lambda p: p["stats"].__setitem__(0, "61"),
                       lambda p: p["stats"].pop(),
                       lambda p: p.__setitem__("didNotPlay", "false")):
            body = summary()
            change(player(body))
            with self.assertRaises(ValueError):
                parse(body)

    def test_refresh_archives_raw_and_deduplicates_state_and_settlements(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = FakeLedger([forecast()])
            body = summary()
            def fetch(url):
                data = raw(scoreboard(body) if "/scoreboard?" in url else body)
                return data, {"url": url, "requested_at": stamp(NOW), "received_at": stamp(NOW),
                              "status_code": 200, "body_complete": True, "sha256": digest(data)}
            first = outcomes.refresh_outcomes(tmp, ledger, FROZEN, run_id="run-1", clock=lambda: NOW, fetch=fetch)
            self.assertEqual(first["status"], "OK")
            self.assertEqual(len(first["observations"]), 14)
            self.assertEqual(ledger.rows["forecast-1"]["actual_points"], 18)
            second = outcomes.refresh_outcomes(tmp, ledger, FROZEN, run_id="run-2", clock=lambda: NOW, fetch=fetch)
            self.assertEqual(len(second["observations"]), 0)
            self.assertTrue((Path(tmp) / "run-1" / "summary-123.body").is_file())
            with self.assertRaises(FileExistsError):
                outcomes.refresh_outcomes(tmp, ledger, FROZEN, run_id="run-2", clock=lambda: NOW, fetch=fetch)
            body["boxscore"]["players"][0]["statistics"][0]["athletes"][0]["stats"][1] = "19"
            body["header"]["competitions"][0]["competitors"][0]["score"] = "81"
            later = NOW + timedelta(minutes=5)
            def revised_fetch(url):
                data, receipt = fetch(url)
                return data, {**receipt, "requested_at": stamp(later), "received_at": stamp(later)}
            revised = outcomes.refresh_outcomes(tmp, ledger, FROZEN, run_id="run-3", clock=lambda: later, fetch=revised_fetch)
            self.assertEqual(len(revised["observations"]), 7)
            self.assertEqual(ledger.rows["forecast-1"]["actual_points"], 19)
            self.assertIsNotNone(ledger.rows["forecast-1"]["supersedes"])

    def test_restore_rejects_changed_raw_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            def fetch(url):
                body = raw({"events": []})
                return body, {"url": url, "requested_at": stamp(NOW), "received_at": stamp(NOW),
                              "status_code": 200, "body_complete": True, "sha256": digest(body)}
            outcomes.refresh_outcomes(tmp, FakeLedger(), FROZEN, run_id="run-1", clock=lambda: NOW, fetch=fetch)
            path = next((Path(tmp) / "run-1").glob("*.body"))
            path.write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "raw outcome bytes"):
                outcomes.refresh_outcomes(tmp, FakeLedger(), FROZEN, run_id="run-2", clock=lambda: NOW, fetch=fetch)

    def test_http_error_body_is_archived_and_not_acknowledged(self):
        with tempfile.TemporaryDirectory() as tmp:
            def fetch(url):
                body = b"synthetic HTTP failure"
                return body, {"url": url, "requested_at": stamp(NOW), "received_at": stamp(NOW),
                              "status_code": 503, "body_complete": True, "sha256": digest(body)}
            result = outcomes.refresh_outcomes(tmp, FakeLedger(), FROZEN, run_id="run-1", clock=lambda: NOW, fetch=fetch)
            self.assertEqual(result["status"], "DEGRADED")
            self.assertEqual(result["receipt"]["covered_dates"], [])
            self.assertEqual(next((Path(tmp) / "run-1").glob("*.body")).read_bytes(), b"synthetic HTTP failure")

    def test_missing_completion_status_is_source_failure(self):
        body = scoreboard()
        body["events"][0].pop("status")
        with self.assertRaisesRegex(ValueError, "completion status"):
            outcomes.scoreboard_events(raw(body), observed_at=NOW, frozen_at=FROZEN)

    def test_box_completeness_and_duration_reconciliation(self):
        changes = (
            lambda b: b["boxscore"]["players"][0]["statistics"][0].__setitem__("athletes", [player(b)]),
            lambda b: player(b)["stats"].__setitem__(0, "50"),
            lambda b: player(b)["stats"].__setitem__(0, "1"),
            lambda b: player(b)["stats"].__setitem__(1, "19"),
        )
        for change in changes:
            body = summary()
            change(body)
            with self.assertRaises(ValueError):
                parse(body)

    def test_incomplete_points_cannot_hide_impossible_known_total(self):
        body = summary()
        player(body)["stats"][1] = "--"
        body["header"]["competitions"][0]["competitors"][0]["score"] = "61"
        # The other five known counts already sum to 62; the missing count
        # cannot reduce that total to the final score.
        with self.assertRaisesRegex(ValueError, "points exceed"):
            parse(body)

        body = summary()
        player(body)["stats"][0:2] = ["0", "19"]
        player(body)["didNotPlay"] = None
        # Unknown participation leaves settlement unresolved, but its reported
        # count still contributes to an impossible sum of 81 against 80.
        with self.assertRaisesRegex(ValueError, "points exceed"):
            parse(body)

    def test_incomplete_minutes_cannot_hide_impossible_known_total(self):
        body = summary()
        athletes = body["boxscore"]["players"][0]["statistics"][0]["athletes"]
        player(body)["stats"][0] = "--"
        for athlete in athletes[1:]:
            athlete["stats"][0] = "40"
        extra = deepcopy(athletes[1])
        extra["athlete"]["id"] = "999"
        extra["stats"] = ["40", "0", "0", "0", "0-0"]
        athletes.append(extra)
        with self.assertRaisesRegex(ValueError, "minutes exceed game duration allowance"):
            parse(body)

    def test_feasible_partial_box_keeps_unknown_measurements_and_known_settlement(self):
        body = summary()
        missing = body["boxscore"]["players"][0]["statistics"][0]["athletes"][1]
        missing["stats"][0:2] = ["--", "--"]
        parsed = parse(body)
        self.assertEqual(parsed["points_reconciliation"]["1"], "incomplete_reported_counts")
        self.assertEqual(parsed["minutes_reconciliation"]["1"], "incomplete_reported_exposure")
        self.assertIsNone(parsed["players"][missing["athlete"]["id"]]["actual_points"])
        settlement = outcomes.settlement_records(parsed, [forecast()], {})[0]
        self.assertEqual((settlement["participation"], settlement["actual_points"]), ("played", 18))

    def test_stale_clock_and_wrong_requested_date_are_not_acknowledged(self):
        for wrong_date in (False, True):
            with tempfile.TemporaryDirectory() as tmp:
                later = NOW + timedelta(days=1)
                def fetch(url):
                    body = raw(scoreboard())
                    received = later if wrong_date else NOW
                    return body, {"url": url, "requested_at": stamp(received), "received_at": stamp(received),
                                  "status_code": 200, "body_complete": True, "sha256": digest(body)}
                result = outcomes.refresh_outcomes(tmp, FakeLedger(), FROZEN, run_id="run-1", clock=lambda: later, fetch=fetch)
                self.assertEqual(result["status"], "DEGRADED")
                self.assertNotIn("2026-09-09", result["receipt"]["covered_dates"])
                self.assertTrue(list((Path(tmp) / "run-1").glob("*.body")))
                if not wrong_date:
                    # Invalid clock bytes remain evidence and can be restored
                    # as failures; they never bind a model observation.
                    again = outcomes.refresh_outcomes(tmp, FakeLedger(), FROZEN, run_id="run-2", clock=lambda: later, fetch=fetch)
                    self.assertEqual(again["restored_observations"], ())

    def test_current_day_remains_queued_after_more_than_fourteen_days_outage(self):
        with tempfile.TemporaryDirectory() as tmp:
            def empty(url):
                body = raw({"events": []})
                return body, {"url": url, "requested_at": stamp(NOW), "received_at": stamp(NOW),
                              "status_code": 200, "body_complete": True, "sha256": digest(body)}
            first = outcomes.refresh_outcomes(tmp, FakeLedger(), FROZEN, run_id="run-1", clock=lambda: NOW, fetch=empty)
            self.assertEqual(first["receipt"]["covered_dates"], [])
            later = NOW + timedelta(days=20)
            requested = []
            def caught_up(url):
                requested.append(url)
                body = raw(summary() if "/summary?" in url else scoreboard() if "dates=20260908" in url else {"events": []})
                return body, {"url": url, "requested_at": stamp(later), "received_at": stamp(later),
                              "status_code": 200, "body_complete": True, "sha256": digest(body)}
            result = outcomes.refresh_outcomes(tmp, FakeLedger(), FROZEN, run_id="run-2", clock=lambda: later, fetch=caught_up)
            self.assertEqual(len(result["observations"]), 14)
            self.assertIn("2026-09-08", result["receipt"]["covered_dates"])
            self.assertTrue(any("dates=20260908" in url for url in requested))

    def test_malformed_nested_json_becomes_source_failure(self):
        for value in ({"header": []}, {"header": {"competitions": [None]}}, []):
            with self.assertRaises(ValueError):
                parse(value)
        with self.assertRaises(ValueError):
            outcomes.scoreboard_events(raw({"events": [None]}), observed_at=NOW, frozen_at=FROZEN)

    def test_orphan_history_is_excluded_and_same_clock_conflicts_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            def fetch(url):
                body = raw(scoreboard() if "/scoreboard?" in url else summary())
                return body, {"url": url, "requested_at": stamp(NOW), "received_at": stamp(NOW),
                              "status_code": 200, "body_complete": True, "sha256": digest(body)}
            first = outcomes.refresh_outcomes(tmp, FakeLedger(), FROZEN, run_id="run-1", clock=lambda: NOW, fetch=fetch)
            orphan = Path(tmp) / "orphan"
            orphan.mkdir()
            shutil.copyfile(Path(tmp) / "run-1" / "observations.jsonl.gz", orphan / "observations.jsonl.gz")
            second = outcomes.refresh_outcomes(tmp, FakeLedger(), FROZEN, run_id="run-2", clock=lambda: NOW, fetch=fetch)
            self.assertEqual(len(second["restored_observations"]), 14)
            conflict = Path(tmp) / "conflict"
            shutil.copytree(Path(tmp) / "run-1", conflict)
            rows = list(first["observations"])
            prior = rows[-1]
            values = dict(prior.payload)
            values["starter"] = not values.get("starter", False)
            rows[-1] = replace(prior, payload=values, payload_hash=None)
            path = conflict / "observations.jsonl.gz"
            path.unlink()
            write_observations(path, rows)
            receipt = json.loads((conflict / "receipt.json").read_bytes())
            receipt["observations_sha256"] = digest(path.read_bytes())
            receipt["raw_sources"]["summary-123"]["observation_manifest_hashes"] = [payload_digest(r.manifest_entry()) for r in rows]
            (conflict / "receipt.json").write_text(json.dumps(receipt))
            with self.assertRaisesRegex(ValueError, "conflicting restored"):
                outcomes.refresh_outcomes(tmp, FakeLedger(), FROZEN, run_id="run-3", clock=lambda: NOW, fetch=fetch)

    def test_partial_http_body_survives_read_failure(self):
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read1(self, size):
                if getattr(self, "sent", False):
                    raise OSError("synthetic interrupted stream")
                self.sent = True
                return b"partial response"
        with patch.object(outcomes, "urlopen", return_value=Response()):
            body, receipt = outcomes.fetch_body(outcomes.API + "/scoreboard?dates=20260908", clock=lambda: NOW)
        self.assertEqual(body, b"partial response")
        self.assertFalse(receipt["body_complete"])
        self.assertIn("interrupted stream", receipt["error"])

    def test_calendar_cap_includes_full_fourteen_day_settlement_window(self):
        later = outcomes.CAP + timedelta(days=14)
        with tempfile.TemporaryDirectory() as tmp:
            def fetch(url):
                body = raw({"events": []})
                return body, {"url": url, "requested_at": stamp(later), "received_at": stamp(later),
                              "status_code": 200, "body_complete": True, "sha256": digest(body)}
            result = outcomes.refresh_outcomes(tmp, FakeLedger(), FROZEN, run_id="run-1", clock=lambda: later, fetch=fetch)
            self.assertEqual(result["status"], "DEGRADED")  # bounded catch-up, not a forbidden horizon
            self.assertEqual(result["attempts"][0]["cause"], "catchup_backlog")

    def test_refresh_unreadable_source_retains_attempt_and_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            def fetch(url):
                raise OSError("synthetic unavailable")
            result = outcomes.refresh_outcomes(tmp, FakeLedger(), FROZEN, run_id="run-1", clock=lambda: NOW, fetch=fetch)
            self.assertEqual(result["status"], "DEGRADED")
            self.assertEqual(result["observations"], ())
            self.assertEqual(result["receipt"]["covered_dates"], [])


if __name__ == "__main__":
    unittest.main()
