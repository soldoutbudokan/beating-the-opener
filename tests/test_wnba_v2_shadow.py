"""Synthetic contracts for real-clock private prospective evidence."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from research.wnba_v2 import shadow
from research.wnba_v2.model import price_forecast


UTC = timezone.utc
NOW = datetime(2026, 9, 8, 14, tzinfo=UTC)
RAW = b'{"source":"synthetic paired price response"}'
RESULT = b'{"source":"synthetic final box response"}'


def study():
    return {"schema": "wnba-shadow-study-v1", "study_id": "points-v2-trial",
            "candidate_id": "points-v2-fixed", "market": "points", "mode": "shadow",
            "bundle_sha256": "a" * 64, "recipe_sha256": "b" * 64,
            "model_recipe_hash": "9" * 64,
            "implementation_sha256": "c" * 64, "registration_commit": "d" * 40,
            "frozen_at": "2026-09-07T12:00:00Z", "bundle_sealed_at": "2026-09-07T12:01:00Z",
            "starts_at": "2026-09-07T12:02:00Z", "bundle_library_file_id": "libfile-fixture",
            "bundle_library_version_id": "0"}


def forecast(forecast_id="forecast-1", player_id="player-1", game_id="game-1", now=NOW):
    frozen = study()
    row = {"schema": "wnba-shadow-forecast-v1", "forecast_id": forecast_id,
            "candidate_id": frozen["candidate_id"], "market": "points",
            "game_id": game_id, "player_id": player_id, "team_id": "team-a", "opponent_id": "team-b",
            **{k: frozen[k] for k in ("bundle_sha256", "recipe_sha256", "implementation_sha256")},
            "generated_at": shadow.stamp(now), "input_cutoff_at": shadow.stamp(now - timedelta(seconds=30)),
            "input_max_available_at": "2025-09-01T12:00:00Z",
            "tip_at": shadow.stamp(now + timedelta(hours=2)),
            "p_over": .75, "p_push": 0., "p_under": .25, "p_dnp": .1,
            "quote": {"quote_id": forecast_id + "-quote", "book_id": 10,
                      "game_id": game_id, "player_id": player_id, "market": "points",
                      "line": 10.5, "over_decimal": 1.91, "under_decimal": 1.91,
                      "active": True, "main": True, "is_off": False,
                      "received_at": shadow.stamp(now - timedelta(seconds=20)),
                      "over_updated_at": shadow.stamp(now - timedelta(minutes=1)),
                      "under_updated_at": shadow.stamp(now - timedelta(minutes=1)),
                      "snapshot_sha256": shadow.digest(RAW)}}
    return refresh(row)


def refresh(row, mean=15.):
    request = {"entity_id": row["player_id"], "event_id": row["game_id"],
               "team_id": row["team_id"], "opponent_id": row["opponent_id"], "season": shadow.instant(row["tip_at"]).year,
               "as_of": row["input_cutoff_at"], "tip_at": row["tip_at"]}
    manifest = {"schema": "wnba-v2-inputs-v1", "request": request,
                "observations": [{"available_at": row["input_max_available_at"],
                                  "source_id": "synthetic-history", "record_id": "historical-record",
                                  "entity_id": row["player_id"], "event_id": "historical-game", "season": 2025,
                                  "kind": "historical_outcome", "effective_at": "2025-09-01T00:00:00Z",
                                  "observed_at": None, "published_at": None, "required_at": [],
                                  "time_basis": "assumed", "assumed_available_at": row["input_max_available_at"],
                                  "time_note": "Synthetic historical availability assumption.", "payload_hash": "7" * 64}]}
    manifest_hash = shadow.digest(shadow.encode(manifest)[:-1])
    output = {"schema": "wnba-v2-forecast-v1", "recipe_hash": study()["model_recipe_hash"],
              "request": request, "p_dnp": row["p_dnp"], "input_manifest": manifest,
              "input_manifest_hash": manifest_hash,
              "input_max_available_at": row["input_max_available_at"],
              "future_only_boundary": {"frozen_at": study()["frozen_at"], "seed_last_season": 2025,
                                       "pre_freeze_2026_history_allowed": False, "parameter_refits": 0,
                                       "independent_performance_claim": False},
              "minutes_values": list(range(1, 61)), "minutes_probs": [1.] + [0.] * 59,
              "count_components": {"points": {"means": [mean] * 60, "variances": [mean * 2] * 60}}}
    row.update(model_output=output, model_output_sha256=shadow.digest(shadow.encode(output)),
               input_manifest_sha256=manifest_hash)
    row.update({k: v for k, v in price_forecast(output, "points", row["quote"]["line"]).items()
                if k in {"p_over", "p_push", "p_under"}})
    return row


def receipt(staged, now=NOW):
    return {"schema": "wnba-shadow-seal-v1", "batch_id": staged["batch_id"],
            "manifest_sha256": staged["manifest_sha256"], "provider": "chatgpt-library",
            "library_file_id": "libfile-synthetic-batch", "library_version_id": "0",
            "stored_artifact_sha256": "f" * 64, "durable_at": shadow.stamp(now + timedelta(minutes=1))}


def settlement(row, participation="played", actual=12, settlement_id="settlement-1", supersedes=None):
    completed = shadow.instant(row["tip_at"]) + timedelta(hours=3)
    return {"schema": "wnba-shadow-settlement-v1", "settlement_id": settlement_id,
            "forecast_id": row["forecast_id"], "game_id": row["game_id"], "player_id": row["player_id"],
            "observed_at": shadow.stamp(completed + timedelta(minutes=10)),
            "game_completed_at": shadow.stamp(completed), "participation": participation,
            "actual_points": actual if participation == "played" else None,
            "display_minutes": 0 if participation in {"played", "dnp"} else None,
            "participation_evidence": "Explicit synthetic box appearance/DNP flag; missing remains unknown.",
            "source_sha256": shadow.digest(RESULT), "supersedes": supersedes}


class ShadowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = shadow.Ledger(Path(self.tmp.name) / "private-ledger")
        self.ledger.initialize(study(), NOW)

    def add(self, rows=None, batch_id="batch-1", now=NOW, seal=True):
        rows = [forecast()] if rows is None else rows
        staged = self.ledger.stage(batch_id, rows, {"offers.body": RAW},
                                   attempts=[{"source": "synthetic", "status": "OK"}], now=now)
        if seal:
            self.ledger.seal(batch_id, receipt(staged, now), now + timedelta(minutes=2))
        return staged

    def test_local_files_are_ineligible_until_verified_durable_receipt(self):
        staged = self.add(seal=False)
        rows, excluded = self.ledger.population(NOW + timedelta(minutes=2))
        self.assertEqual(rows, [])
        self.assertEqual(excluded["unsealed_forecasts"], 1)
        self.ledger.seal("batch-1", receipt(staged), NOW + timedelta(minutes=2))
        self.assertEqual(len(self.ledger.population(NOW + timedelta(minutes=2))[0]), 1)

    def test_post_tip_durable_upload_cannot_backdate_a_forecast(self):
        staged = self.add(seal=False)
        late = receipt(staged)
        late["durable_at"] = forecast()["tip_at"]
        saved = self.ledger.seal("batch-1", late, NOW + timedelta(hours=3))
        self.assertEqual(saved["late_seals"], 1)
        self.assertEqual(self.ledger.population(NOW + timedelta(hours=3))[0], [])

    def test_fifteen_minute_seal_boundary_is_required(self):
        staged = self.add(seal=False)
        seal = receipt(staged)
        seal["durable_at"] = shadow.stamp(shadow.instant(forecast()["tip_at"]) - timedelta(minutes=15))
        self.ledger.seal("batch-1", seal, NOW + timedelta(hours=2))
        self.assertEqual(len(self.ledger.population(NOW + timedelta(hours=2))[0]), 1)
        other = forecast("other", "player-2")
        staged = self.add([other], batch_id="batch-2", seal=False)
        seal = receipt(staged)
        seal["durable_at"] = shadow.stamp(shadow.instant(other["tip_at"]) - timedelta(minutes=14, seconds=59))
        self.ledger.seal("batch-2", seal, NOW + timedelta(hours=2))
        self.assertEqual(len(self.ledger.population(NOW + timedelta(hours=2))[0]), 1)

    def test_git_receipt_requires_exact_remote_commit_artifact_and_hash(self):
        staged = self.add(seal=False)
        seal = {**receipt(staged), "provider": "github", "repository_full_name": "owner/private-evidence",
                "commit_sha": "1" * 40, "artifact_path": "ledger/batches/batch-1/manifest.json"}
        self.ledger.seal("batch-1", seal, NOW + timedelta(minutes=2))
        self.assertEqual(len(self.ledger.population(NOW + timedelta(minutes=2))[0]), 1)
        for change in ({"commit_sha": "short"}, {"artifact_path": "../escape"}, {"repository_full_name": "unknown"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                shadow.validate_storage_identity({**seal, **change})
        other = shadow.Ledger(Path(self.tmp.name) / "generic-bundle")
        frozen = study()
        frozen["bundle_receipt"] = {**seal, "stored_artifact_sha256": frozen["bundle_sha256"],
                                    "durable_at": frozen["bundle_sealed_at"]}
        other.initialize(frozen, NOW)

    def test_receipt_must_bind_exact_bytes_and_a_real_private_storage_identity(self):
        staged = self.add(seal=False)
        for change in ({"manifest_sha256": "0" * 64}, {"provider": "local-file"},
                       {"library_file_id": ""}, {"durable_at": "2026-09-08T13:00:00Z"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.ledger.seal("batch-1", {**receipt(staged), **change}, NOW + timedelta(minutes=2))

    def test_immutable_batch_and_seal_cannot_be_overwritten(self):
        staged = self.add()
        with self.assertRaises(FileExistsError):
            self.add()
        with self.assertRaises(FileExistsError):
            self.ledger.seal("batch-1", receipt(staged), NOW + timedelta(minutes=2))
        raw = self.ledger.root / "batches/batch-1/raw/offers.body"
        raw.write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "bytes changed"):
            self.ledger.population(NOW + timedelta(minutes=2))

    def test_model_bytes_and_price_identity_are_bound(self):
        mutations = (
            lambda r: r["model_output"].update(mean=99),
            lambda r: r.update(bundle_sha256="0" * 64),
            lambda r: r["quote"].update(player_id="other-player"),
            lambda r: r["quote"].update(snapshot_sha256="0" * 64),
            lambda r: r.update(actual=0),
        )
        for mutate in mutations:
            row = forecast()
            mutate(row)
            with self.subTest(mutation=mutate), self.assertRaises(ValueError):
                shadow.validate_forecast(row, study(), {shadow.digest(RAW)}, NOW)

    def test_plausible_but_wrong_probabilities_and_request_hashes_are_rejected(self):
        row = forecast()
        row.update(p_over=.6, p_under=.4)
        with self.assertRaisesRegex(ValueError, "do not reproduce"):
            shadow.validate_forecast(row, study(), {shadow.digest(RAW)}, NOW)
        row = forecast()
        row["model_output"]["request"]["entity_id"] = "different-player"
        manifest = row["model_output"]["input_manifest"]
        row["input_manifest_sha256"] = shadow.digest(shadow.encode(manifest)[:-1])
        row["model_output"]["input_manifest_hash"] = row["input_manifest_sha256"]
        row["model_output_sha256"] = shadow.digest(shadow.encode(row["model_output"]))
        with self.assertRaisesRegex(ValueError, "request identity"):
            shadow.validate_forecast(row, study(), {shadow.digest(RAW)}, NOW)

    def test_full_input_manifest_independently_rejects_protected_and_target_history(self):
        changes = ({"event_id": "game-1"}, {"season": 2026}, {"entity_id": "unrelated"},
                   {"payload_hash": "missing"}, {"available_at": "2025-09-01T11:00:00Z"},
                   {"effective_at": "2026-08-01T00:00:00Z", "season": 2026,
                    "time_basis": "observed", "assumed_available_at": None,
                    "observed_at": "2026-09-07T13:00:00Z", "available_at": "2026-09-07T13:00:00Z"})
        for change in changes:
            row = forecast()
            output = row["model_output"]
            output["input_manifest"]["observations"][0].update(change)
            row["input_manifest_sha256"] = shadow.digest(shadow.encode(output["input_manifest"])[:-1])
            output["input_manifest_hash"] = row["input_manifest_sha256"]
            row["model_output_sha256"] = shadow.digest(shadow.encode(output))
            with self.subTest(change=change), self.assertRaises(ValueError):
                shadow.validate_forecast(row, study(), {shadow.digest(RAW)}, NOW)
        row = forecast()
        output = row["model_output"]
        output["request"]["season"] = 2025
        row["input_manifest_sha256"] = shadow.digest(shadow.encode(output["input_manifest"])[:-1])
        output["input_manifest_hash"] = row["input_manifest_sha256"]
        row["model_output_sha256"] = shadow.digest(shadow.encode(output))
        with self.assertRaisesRegex(ValueError, "request season"):
            shadow.validate_forecast(row, study(), {shadow.digest(RAW)}, NOW)

    def test_clocks_and_live_paired_quote_are_strict(self):
        changes = (
            ("generated_at", "2026-09-08T14:00:00"),
            ("generated_at", "2026-09-08T14:01:00Z"),
            ("input_max_available_at", "2026-09-08T14:00:00Z"),
            ("tip_at", "2026-09-08T14:10:00Z"),
            ("tip_at", "2026-09-10T14:00:00Z"),
        )
        for key, value in changes:
            row = forecast()
            row[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                shadow.validate_forecast(row, study(), {shadow.digest(RAW)}, NOW)
        for change in ({"over_updated_at": "2026-09-08T15:00:00Z"},
                       {"under_updated_at": "2026-09-08T10:00:00Z"},
                       {"active": False}, {"main": False}, {"is_off": True},
                       {"book_id": 99}, {"over_decimal": 3.}, {"line": 10.25}):
            row = forecast()
            row["quote"].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                shadow.validate_forecast(row, study(), {shadow.digest(RAW)}, NOW)

    def test_probabilities_respect_integer_count_pushes(self):
        row = forecast()
        row.update(p_over=.6, p_push=.1, p_under=.3)
        with self.assertRaises(ValueError):
            shadow.validate_forecast(row, study(), {shadow.digest(RAW)}, NOW)
        row["quote"]["line"] = 10
        refresh(row)
        shadow.validate_forecast(row, study(), {shadow.digest(RAW)}, NOW)
        row["p_under"] = .5
        with self.assertRaises(ValueError):
            shadow.validate_forecast(row, study(), {shadow.digest(RAW)}, NOW)

    def test_first_observed_book_selection_ignores_edge_and_outcomes(self):
        later = forecast("later")
        earlier = forecast("earlier")
        earlier["quote"].update(book_id=14, received_at="2026-09-08T13:59:30Z")
        refresh(earlier, mean=10.)
        tie = forecast("book-10-tie")
        tie["quote"]["received_at"] = earlier["quote"]["received_at"]
        self.add([later, earlier, tie])
        rows, excluded = self.ledger.population(NOW + timedelta(minutes=2))
        self.assertEqual([r["forecast_id"] for r in rows], ["book-10-tie"])
        self.assertEqual(excluded["later_player_game_forecasts"], 2)

    def test_same_forecast_identity_with_conflicting_bytes_fails(self):
        self.add()
        row = forecast()
        refresh(row, mean=12.)
        self.add([row], batch_id="batch-2")
        with self.assertRaisesRegex(ValueError, "conflicting reused"):
            self.ledger.population(NOW + timedelta(minutes=2))

    def test_empty_capture_and_failed_requests_do_not_invent_forecasts(self):
        staged = self.ledger.stage("empty", [], {"events.body": RAW},
                                   attempts=[{"source": "events", "status": "NO_EVENTS"}], now=NOW)
        self.ledger.seal("empty", receipt(staged), NOW + timedelta(minutes=2))
        status = self.ledger.status(NOW + timedelta(minutes=2))
        self.assertEqual(status["forecasts"], 0)
        self.assertEqual(status["status"], "COLLECTING")
        self.assertEqual(status["latest_capture"]["attempt_statuses"], {"NO_EVENTS": 1})
        self.assertTrue(status["latest_capture"]["durable_receipt_attached"])

    def test_participated_zero_display_minutes_keeps_counts(self):
        row = forecast()
        self.add([row])
        saved = settlement(row, actual=12)
        self.ledger.settle(saved, RESULT, NOW + timedelta(hours=6))
        actual = self.ledger.settlements({row["forecast_id"]: row}, NOW + timedelta(hours=6))
        self.assertEqual(actual[row["forecast_id"]]["actual_points"], 12)
        self.assertEqual(shadow.grade(row, saved, "over")["result"], "win")

    def test_dnp_refunds_unknown_waits_and_push_retains_stake(self):
        row = forecast()
        dnp = shadow.grade(row, settlement(row, "dnp"), "under")
        self.assertEqual(dnp, {"result": "void", "stake": 0., "profit": 0., "stressed_profit": 0.})
        unknown = shadow.grade(row, settlement(row, "unknown"), "under")
        self.assertIsNone(unknown["profit"])
        row["quote"]["line"] = 12
        push = shadow.grade(row, settlement(row, actual=12), "over")
        self.assertEqual(push, {"result": "push", "stake": 1., "profit": 0., "stressed_profit": -.01})

    def test_settlements_require_actual_evidence_and_cannot_infer_absence(self):
        row = forecast()
        self.add([row])
        mutations = ({"source_sha256": "0" * 64}, {"actual_points": -1},
                     {"actual_points": True}, {"participation": "dnp", "actual_points": 0},
                     {"participation": "unknown", "actual_points": 0},
                     {"game_completed_at": "2026-09-08T13:00:00Z"},
                     {"player_id": "other"}, {"participation_evidence": ""})
        for change in mutations:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.ledger.settle({**settlement(row), **change}, RESULT, NOW + timedelta(hours=6))

    def test_settlement_corrections_are_append_only_and_clock_ties_are_valid(self):
        row = forecast()
        self.add([row])
        first = settlement(row, "unknown", settlement_id="z-first")
        second = settlement(row, actual=12, settlement_id="a-second", supersedes="z-first")
        self.ledger.settle(first, RESULT, NOW + timedelta(hours=6))
        self.ledger.settle(second, RESULT, NOW + timedelta(hours=6))
        latest = self.ledger.settlements({row["forecast_id"]: row}, NOW + timedelta(hours=6))
        self.assertEqual(latest[row["forecast_id"]]["settlement_id"], "a-second")
        self.assertTrue((self.ledger.root / "settlements/z-first/record.json").exists())
        with self.assertRaises(ValueError):
            self.ledger.settle(settlement(row, actual=8, settlement_id="fork", supersedes="z-first"),
                               RESULT, NOW + timedelta(hours=6))

    def test_endpoint_keeps_whole_crossing_et_date_without_looking_at_grades(self):
        for i in range(4):
            event_time = NOW + timedelta(days=0 if i < 3 else 1)
            self.add([forecast(f"forecast-{i}", f"player-{i}", f"game-{i}", event_time)],
                     batch_id=f"batch-{i}", now=event_time)
        with patch.object(shadow, "TARGET", 2):
            rows, excluded = self.ledger.population(NOW + timedelta(days=2))
            self.assertEqual(len(rows), 3)
            self.assertEqual(excluded["post_endpoint_forecasts"], 1)
            self.assertEqual(self.ledger.status(NOW + timedelta(days=2))["status"], "WAITING_FOR_SETTLEMENT")
            self.assertEqual(self.ledger.status(NOW + timedelta(days=20))["status"], "EVALUATION_DUE")

    def test_primary_cannot_run_before_endpoint_and_runs_once(self):
        self.add()
        with self.assertRaisesRegex(ValueError, "remains sealed"):
            self.ledger.evaluate(NOW + timedelta(days=1))
        result = self.ledger.evaluate(shadow.CAP + timedelta(days=15))
        self.assertEqual(result["decision"], "INSUFFICIENT_EVIDENCE")
        status = self.ledger.status(shadow.CAP + timedelta(days=15))
        self.assertEqual(status["status"], "EVALUATED")
        self.assertEqual(status["evaluation"]["decision"], "INSUFFICIENT_EVIDENCE")
        with self.assertRaises(FileExistsError):
            self.ledger.evaluate(shadow.CAP + timedelta(days=15))

    def test_saved_evaluation_must_bind_the_frozen_study_and_endpoint_clock(self):
        self.add()
        self.ledger.evaluate(shadow.CAP + timedelta(days=15))
        path = self.ledger.root / "evaluation.json"
        saved = json.loads(path.read_bytes())
        for change in ({"study_id": "other"}, {"study_sha256": "0" * 64},
                       {"evaluated_at": "2026-09-08T14:00:00Z"}):
            path.write_bytes(shadow.encode({**saved, **change}))
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.ledger.status(shadow.CAP + timedelta(days=15))

    def test_evaluation_freezes_exact_settlement_versions_and_unresolved_rows(self):
        row, unknown = forecast(), forecast("forecast-2", "player-2")
        self.add([row, unknown])
        self.ledger.settle(settlement(row, actual=12, settlement_id="original"), RESULT, NOW + timedelta(hours=6))
        result = self.ledger.evaluate(shadow.CAP + timedelta(days=15))
        correction = settlement(row, actual=8, settlement_id="later", supersedes="original")
        correction["observed_at"] = shadow.stamp(shadow.CAP + timedelta(days=16))
        self.ledger.settle(correction, RESULT, shadow.CAP + timedelta(days=16))
        late_unknown = settlement(unknown, actual=20, settlement_id="resolved-later")
        late_unknown["observed_at"] = shadow.stamp(shadow.CAP + timedelta(days=16))
        self.ledger.settle(late_unknown, RESULT, shadow.CAP + timedelta(days=16))
        rows, versions = self.ledger.evaluation_inputs(now=shadow.CAP + timedelta(days=16))
        self.assertEqual(len(rows), 2)
        self.assertEqual(versions[row["forecast_id"]]["settlement_id"], "original")
        self.assertNotIn(unknown["forecast_id"], versions)
        replay = shadow.evaluate_saved_rows(rows, versions)
        self.assertEqual(replay["primary"], result["primary"])
        self.assertEqual(self.ledger.status(shadow.CAP + timedelta(days=16))["status"], "EVALUATED")
        frozen = self.ledger.root / "evaluation-inputs.json"
        frozen.write_bytes(frozen.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "input manifest changed"):
            self.ledger.status(shadow.CAP + timedelta(days=16))

    def test_interrupted_evaluation_cannot_silently_repeat_on_new_inputs(self):
        self.add()
        with patch.object(shadow, "evaluate_saved_rows", side_effect=RuntimeError("synthetic interruption")):
            with self.assertRaises(RuntimeError):
                self.ledger.evaluate(shadow.CAP + timedelta(days=15))
        self.assertEqual(self.ledger.status(shadow.CAP + timedelta(days=15))["status"], "EVALUATION_INTERRUPTED")
        with self.assertRaises(ValueError):
            self.ledger.evaluate(shadow.CAP + timedelta(days=15))

    def test_fixed_fourteen_day_wait_applies_even_when_every_result_is_known(self):
        row = forecast()
        self.add([row])
        self.ledger.settle(settlement(row), RESULT, NOW + timedelta(hours=6))
        with patch.object(shadow, "TARGET", 1):
            self.assertEqual(self.ledger.status(NOW + timedelta(days=1))["status"], "WAITING_FOR_SETTLEMENT")
            self.assertEqual(self.ledger.status(NOW + timedelta(days=16))["status"], "EVALUATION_DUE")

    def test_calendar_cap_precedes_the_last_eligible_et_date_midnight(self):
        now = shadow.CAP - timedelta(hours=4)
        row = forecast(now=now)
        self.add([row], now=now)
        with patch.object(shadow, "TARGET", 1):
            status = self.ledger.status(shadow.CAP + timedelta(days=14))
            self.assertEqual(status["endpoint_at"], shadow.stamp(shadow.CAP))
            self.assertEqual(status["status"], "EVALUATION_DUE")

    def test_selection_uses_quoted_ev_before_separate_cost_stress(self):
        row = forecast()
        # Set quoted EV to 5.5%, with 4.6% after stress. This pure arithmetic
        # fixture tests selection; production admissions independently reprice.
        row["p_over"] = (.055 / .9 + 1) / 1.91
        row["p_under"] = 1 - row["p_over"]
        self.assertGreater(shadow.expected_profit(row, "over"), .05)
        self.assertLess(shadow.expected_profit(row, "over", shadow.COST_STRESS), .05)
        result = shadow.evaluate_saved_rows([row], {row["forecast_id"]: settlement(row)})
        self.assertEqual(result["shadow_economics"]["selected"], 1)

    def test_saved_row_economics_include_vig_cost_stress_and_full_population_control(self):
        row = forecast()
        row.update(p_over=.75, p_under=.25)
        self.assertAlmostEqual(shadow.expected_profit(row, "over"), .9 * (.75 * .91 - .25))
        self.assertAlmostEqual(shadow.expected_profit(row, "over", shadow.COST_STRESS), .9 * (.75 * .91 - .25 - .01))
        rows, grades = [], {}
        for i in range(2):
            item = forecast(f"f-{i}", f"p-{i}", f"g-{i}", NOW + timedelta(days=i))
            rows.append(item)
            grades[item["forecast_id"]] = settlement(item, actual=12)
        result = shadow.evaluate_saved_rows(rows, grades)
        self.assertEqual(result["shadow_economics"]["selected"], 2)
        self.assertAlmostEqual(result["shadow_economics"]["roi"], .91)
        self.assertAlmostEqual(result["shadow_economics"]["stressed_roi"], .90)
        self.assertAlmostEqual(result["always_under_all_admitted_forecasts"]["stressed_roi"], -1.01)
        self.assertEqual(result["decision"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(result["gates"]["independent_reproduction"], "REQUIRED_SEPARATELY")
        low_ev = forecast("unselected", "player-low", "game-low", NOW + timedelta(days=3))
        low_ev.update(p_over=.5, p_under=.5)
        rows.append(low_ev)
        grades[low_ev["forecast_id"]] = settlement(low_ev, actual=8)
        result = shadow.evaluate_saved_rows(rows, grades)
        self.assertEqual(result["shadow_economics"]["selected"], 2)
        self.assertEqual(result["always_under_all_admitted_forecasts"]["selected"], 3)
        self.assertEqual(result["always_under_all_admitted_forecasts"]["results"], {"loss": 2, "win": 1})

    def test_date_cluster_interval_preserves_within_game_date_dependence(self):
        rows = [{"date": "2026-09-08", "value": -.2, "n": 1},
                {"date": "2026-09-08", "value": -.2, "n": 1},
                {"date": "2026-09-09", "value": .2, "n": 1}]
        self.assertEqual(shadow.cluster_interval(rows, "value", "n"), [-.2, .2])
        self.assertIsNone(shadow.cluster_interval(rows[:2], "value", "n"))

    def test_symlink_and_path_escape_cannot_enter_private_ledger(self):
        for batch_id in ("../escape", "/absolute", "a/b", ".."):
            with self.subTest(batch_id=batch_id), self.assertRaises(ValueError):
                self.add(batch_id=batch_id)
        outside = Path(self.tmp.name) / "elsewhere"
        outside.mkdir()
        (self.ledger.root / "batches").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.add()


if __name__ == "__main__":
    unittest.main()
