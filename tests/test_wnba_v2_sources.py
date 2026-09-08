"""Synthetic source semantics: participation is distinct from measured exposure."""
from copy import deepcopy
from datetime import datetime, timezone
import unittest

import numpy as np
import pandas as pd

from research.engine.sources import COUNTS, HistoricalSources
from research.wnba_v2.sources import inference_seed_observations, normalise_player_row, observations, repair_cohort


def player(**changes):
    row = {"season": 2023, "game_id": "g1", "athlete_id": "p1", "team_id": "t1",
           "opponent_team_id": "t2", "minutes": 20., "did_not_play": False,
           "starter": False, "athlete_position_abbreviation": "G",
           **{column: 2. for column in COUNTS.values()}}
    row.update(changes)
    return row


def data(rows=None):
    rows = [player()] if rows is None else rows
    teams = [{"season": 2023, "game_id": "g1", "team_id": team,
        "opponent_team_id": opponent, "field_goals_attempted": 70.,
        "free_throws_attempted": 15., "offensive_rebounds": 10.,
        "total_turnovers": 12., "team_score": 80.}
        for team, opponent in (("t1", "t2"), ("t2", "t1"))]
    schedule = [{"season": 2023, "game_id": "g1", "season_type": 2,
        "type_abbreviation": "REG", "home_id": "t1", "away_id": "t2",
        "status_period": 4, "date": "2023-06-01T20:00:00Z",
        "game_date_time": "2023-06-01T16:00:00-04:00"}]
    return HistoricalSources(pd.DataFrame(rows), pd.DataFrame(teams), pd.DataFrame(),
        pd.DataFrame(schedule), {"recorded_at_utc": "2026-09-07T00:00:00Z"})


def player_observations(source):
    return [row for row in observations(source) if row.payload["record_type"] == "player_box"]


def seed_data(year=2025):
    source = data()
    for frame in (source.player_box, source.team_box, source.schedule):
        frame["season"] = year
    source.schedule.loc[0, "date"] = f"{year}-06-01T20:00:00Z"
    source.schedule.loc[0, "game_date_time"] = f"{year}-06-01T16:00:00-04:00"
    return source


class WnbaV2SourceTests(unittest.TestCase):
    def test_zero_reported_minutes_preserves_played_flag_and_positive_counts(self):
        result = normalise_player_row(player(minutes=0.))
        payload = result["payload"]
        self.assertEqual(payload["participation"], "played")
        self.assertIs(payload["did_not_play"], False)
        self.assertIsNone(payload["minutes"])
        self.assertEqual(payload["reported_minutes"], 0.)
        self.assertEqual(payload["exposure_status"], "unknown")
        self.assertEqual(payload["counts"], {market: 2. for market in COUNTS})
        self.assertFalse(payload["minute_measurement_eligible"])
        self.assertFalse(payload["rate_measurement_eligible"])
        self.assertFalse(result["conflict"])

    def test_missing_minutes_with_played_flag_preserves_participation(self):
        for minutes in (None, np.nan, pd.NA):
            with self.subTest(minutes=minutes):
                payload = normalise_player_row(player(minutes=minutes))["payload"]
                self.assertEqual(payload["participation"], "played")
                self.assertEqual(payload["exposure_status"], "unknown")
                self.assertIsNone(payload["minutes"])
                self.assertEqual(payload["counts"]["points"], 2.)

    def test_zero_count_cannot_change_participation_or_invent_exact_exposure(self):
        zero = player(minutes=0., **{column: 0. for column in COUNTS.values()})
        a, b = normalise_player_row(zero)["payload"], normalise_player_row(player(minutes=0.))["payload"]
        for key in ("participation", "participation_basis", "exposure_status", "minutes"):
            self.assertEqual(a[key], b[key])
        self.assertEqual(a["exposure_status"], "unknown")

    def test_explicit_dnp_missing_counts_remain_missing_and_not_a_rate_measurement(self):
        row = player(minutes=None, did_not_play=True, **{column: None for column in COUNTS.values()})
        payload = normalise_player_row(row)["payload"]
        self.assertEqual(payload["participation"], "dnp")
        self.assertEqual(payload["minutes"], 0.)
        self.assertEqual(payload["exposure_status"], "not_applicable")
        self.assertTrue(all(value is None for value in payload["counts"].values()))
        self.assertTrue(all(value is None for value in payload["source_fields"]["counts"].values()))
        self.assertFalse(payload["rate_measurement_eligible"])
        self.assertEqual(len(player_observations(data([row]))), 1)

    def test_contradictory_explicit_dnp_is_retained_as_rejected_row(self):
        for row in (player(minutes=0., did_not_play=True),
                    player(minutes=10., did_not_play=True, **{column: 0. for column in COUNTS.values()})):
            with self.subTest(row=row):
                source = data([row])
                self.assertEqual(player_observations(source), [])
                audit = source.quality["wnba_v2_source_audit"]["player_rows"][0]
                self.assertIn("explicit_dnp_conflict", audit["omissions"])
                self.assertEqual(audit["source_fields"]["counts"]["points"], row["points"])

    def test_missing_counts_keep_valid_minutes_but_disable_rate_measurement(self):
        row = normalise_player_row(player(points=None))["payload"]
        self.assertTrue(row["minute_measurement_eligible"])
        self.assertFalse(row["rate_measurement_eligible"])
        self.assertEqual(row["minutes"], 20.)
        self.assertIsNone(row["counts"]["points"])

    def test_negative_noninteger_nonfinite_measurements_are_rejected_with_raw_values(self):
        for field, value in (("minutes", -1.), ("minutes", float("inf")),
                             ("points", -1.), ("points", .5), ("points", "broken")):
            with self.subTest(field=field, value=value):
                source = data([player(**{field: value})])
                self.assertEqual(player_observations(source), [])
                audit = source.quality["wnba_v2_source_audit"]["player_rows"][0]
                self.assertIn("invalid_player_measurements", audit["omissions"])

    def test_unknown_flag_and_unmeasured_minutes_do_not_infer_participation_from_counts(self):
        for flag in (None, pd.NA, "false", 0):
            with self.subTest(flag=flag):
                payload = normalise_player_row(player(minutes=0., did_not_play=flag))["payload"]
                self.assertEqual(payload["participation"], "unknown")
                self.assertIsNone(payload["did_not_play"])
                self.assertFalse(payload["minute_measurement_eligible"])
        positive = normalise_player_row(player(did_not_play=None))["payload"]
        self.assertEqual(positive["participation"], "played")
        self.assertEqual(positive["participation_basis"], "positive_reported_minutes")

    def test_numpy_boolean_flags_have_explicit_boolean_semantics(self):
        self.assertEqual(normalise_player_row(player(minutes=0., did_not_play=np.bool_(False)))["payload"]["participation"], "played")

    def test_cohort_is_frozen_from_all_explicit_non_dnp_zero_or_missing_minutes(self):
        rows = [player(athlete_id="zero", minutes=0.), player(athlete_id="missing", minutes=None),
                player(athlete_id="dnp", minutes=0., did_not_play=True),
                player(athlete_id="unknown", minutes=0., did_not_play=None),
                player(athlete_id="normal"), player(athlete_id="invalid", minutes=-1.)]
        first = repair_cohort(pd.DataFrame(rows))
        self.assertEqual([row["player_id"] for row in first], ["zero", "missing"])
        altered = [{**row, **{column: 999. for column in COUNTS.values()}} for row in rows]
        self.assertEqual(first, repair_cohort(pd.DataFrame(altered)))
        without_counts = pd.DataFrame(rows).drop(columns=list(COUNTS.values()))
        self.assertEqual(first, repair_cohort(without_counts))

    def test_duplicate_identities_remain_two_audit_rows_and_never_enter_state(self):
        source = data([player(minutes=0.), player(minutes=0.)])
        self.assertEqual(player_observations(source), [])
        audit = source.quality["wnba_v2_source_audit"]
        self.assertEqual(len(audit["repair_cohort"]), 2)
        self.assertEqual(len(audit["player_rows"]), 2)
        self.assertTrue(all("conflicting_player_identity" in row["omissions"] for row in audit["player_rows"]))

    def test_missing_schedule_and_team_identity_conflicts_stay_in_denominator(self):
        source = data([player(game_id="missing"), player(athlete_id="badside", opponent_team_id="t3")])
        self.assertEqual(player_observations(source), [])
        audit = source.quality["wnba_v2_source_audit"]
        self.assertEqual(audit["summary"]["raw_player_rows"], 2)
        self.assertIn("missing_or_noncompetitive_schedule", audit["player_rows"][0]["omissions"])
        self.assertIn("player_team_opponent_conflict", audit["player_rows"][1]["omissions"])

    def test_empty_padded_and_missing_player_identity_are_audited_not_emitted(self):
        for identifier in (None, "", " padded "):
            with self.subTest(identifier=identifier):
                source = data([player(athlete_id=identifier)])
                self.assertEqual(player_observations(source), [])
                audit = source.quality["wnba_v2_source_audit"]["player_rows"][0]
                self.assertIn("invalid_player_identity", audit["omissions"])

    def test_source_row_season_must_match_schedule_identity(self):
        source = data([player(season=2022)])
        self.assertEqual(player_observations(source), [])
        audit = source.quality["wnba_v2_source_audit"]["player_rows"][0]
        self.assertIn("player_schedule_season_conflict", audit["omissions"])

    def test_invalid_team_counts_cannot_supply_valid_player_exposure(self):
        for field, value in (("field_goals_attempted", 70.5), ("team_score", 80.5),
                             ("team_score", -1.)):
            with self.subTest(field=field, value=value):
                source = data()
                source.team_box.loc[0, field] = value
                self.assertEqual(player_observations(source), [])
                audit = source.quality["wnba_v2_source_audit"]
                self.assertIn("invalid_team_identity_or_values", audit["team_rows"][0]["issues"])
                self.assertIn("missing_or_invalid_team", audit["player_rows"][0]["omissions"])

    def test_clock_and_identity_contracts_preserve_timezone_and_raw_box(self):
        source = data([player(minutes=0.)])
        before = deepcopy(source.player_box)
        item = player_observations(source)[0]
        self.assertEqual((item.event_id, item.entity_id), ("g1", "p1"))
        self.assertEqual(item.available_at.isoformat(), "2023-06-02T04:00:00+00:00")
        self.assertEqual(item.time_basis.value, "assumed")
        pd.testing.assert_frame_equal(source.player_box, before)

    def test_protected_seasons_rejected_in_every_source_family(self):
        for family in ("player_box", "team_box", "schedule", "pbp"):
            for year in (2025, 2026):
                with self.subTest(family=family, year=year):
                    source = data()
                    if family == "pbp":
                        source.pbp = pd.DataFrame([{"season": year}])
                    else:
                        getattr(source, family).loc[0, "season"] = year
                    with self.assertRaisesRegex(ValueError, "2003..2024"):
                        observations(source)

    def test_a_future_event_clock_cannot_hide_inside_2024_season(self):
        source = data()
        source.schedule.loc[0, "date"] = "2025-06-01T20:00:00Z"
        source.schedule.loc[0, "game_date_time"] = "2025-06-01T16:00:00-04:00"
        with self.assertRaisesRegex(ValueError, "before 2025"):
            observations(source)

    def test_invalid_fallback_duration_and_fractional_period_keep_audits(self):
        for period, minutes in ((None, float("inf")), (3.5, 20.), (None, 20.)):
            with self.subTest(period=period, minutes=minutes):
                source = data([player(minutes=minutes)])
                source.schedule = source.schedule.astype({"status_period": object})
                source.schedule.loc[0, "status_period"] = period
                self.assertEqual(player_observations(source), [])
                audit = source.quality["wnba_v2_source_audit"]
                self.assertEqual(len(audit["player_rows"]), 1)
                self.assertIn("invalid_team_exposure", audit["team_rows"][0]["issues"])

    def test_schedule_season_must_match_its_event_year(self):
        source = data()
        source.schedule.loc[0, "season"] = 2024
        with self.assertRaisesRegex(ValueError, "season disagrees"):
            observations(source)

    def test_source_receipt_cannot_precede_historical_event(self):
        source = data()
        source.manifest["recorded_at_utc"] = "2022-06-01T20:00:00Z"
        with self.assertRaisesRegex(ValueError, "retrieval precedes"):
            observations(source)

    def test_explicit_frozen_candidate_seed_accepts_2025_without_opening_development(self):
        source = seed_data()
        frozen = datetime(2026, 9, 7, tzinfo=timezone.utc)
        result = inference_seed_observations(source, frozen_at=frozen)
        self.assertEqual(len(result), 3)
        self.assertTrue(all(row.season == 2025 for row in result))
        self.assertTrue(all(row.available_at < frozen for row in result))
        self.assertEqual(source.quality["wnba_v2_source_audit"]["purpose"], "frozen_candidate_inference_seed")
        with self.assertRaisesRegex(ValueError, "2003..2024"):
            observations(source)
        with self.assertRaisesRegex(ValueError, "2003..2024"):
            repair_cohort(source.player_box)

    def test_inference_seed_always_rejects_2026_source_rows(self):
        for family in ("player_box", "team_box", "schedule", "pbp"):
            with self.subTest(family=family):
                source = seed_data()
                if family == "pbp":
                    source.pbp = pd.DataFrame([{"season": 2026}])
                else:
                    getattr(source, family).loc[0, "season"] = 2026
                with self.assertRaisesRegex(ValueError, "2003..2025"):
                    inference_seed_observations(source, frozen_at="2026-09-07T00:00:00Z")

    def test_inference_seed_requires_aware_future_freeze_time(self):
        for frozen in (None, "2026-09-07T00:00:00", "2025-09-07T00:00:00Z"):
            with self.subTest(frozen=frozen), self.assertRaises(ValueError):
                inference_seed_observations(seed_data(), frozen_at=frozen)

    def test_seed_known_source_clock_cannot_reach_or_follow_freeze(self):
        for completed in ("2026-09-06T23:00:00Z", "2026-09-07T00:00:00Z"):
            with self.subTest(completed=completed):
                source = seed_data()
                source.schedule["completed_at"] = completed
                with self.assertRaisesRegex(ValueError, "strictly precede"):
                    inference_seed_observations(source, frozen_at="2026-09-07T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
