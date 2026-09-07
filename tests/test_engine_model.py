"""Synthetic mathematical and timing checks; these are not empirical gates."""
import copy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import math
import unittest

try:
    import numpy as np
    from scipy.stats import nbinom, poisson
except ImportError:
    np = None
if np is not None:
    from research.engine.model import (StructuralModel, MARKETS, count_probability,
                                       fit, price_forecast, training_examples)
from research.engine.store import (ForecastRequest, Observation, ObservationKind,
                                   PointInTimeStore, ProtectedDataError, TimeBasis,
                                   canonical_json)

UTC = timezone.utc


def player(event, tip, minutes=24., player_id="p1", team="t1", season=None, available=None,
           counts=None, kind=ObservationKind.HISTORICAL_OUTCOME):
    payload = {"record_type": "player_box", "team_id": team,
               "opponent_id": "t2" if team == "t1" else "t1", "minutes": minutes,
               "did_not_play": minutes == 0, "starter": minutes >= 24,
               "position": "G", "possessions_estimate": 80., "duration_minutes": 40.,
               "counts": counts or {"points": int(minutes / 2), "rebounds": int(minutes / 6),
                                    "assists": int(minutes / 8), "threes": int(minutes / 16)}}
    return Observation("fixture", "player:" + event + ":" + player_id, player_id, event,
                       season or tip.year, kind, tip, payload, time_basis=TimeBasis.ASSUMED,
                       assumed_available_at=available or tip + timedelta(hours=8),
                       time_note="synthetic fixed delay")


def team(event, tip, team_id="t1", pace=80.):
    return Observation("fixture", "team:" + event + ":" + team_id, team_id, event,
                       tip.year, ObservationKind.HISTORICAL_OUTCOME, tip,
                       {"record_type": "team_box", "team_id": team_id,
                        "opponent_id": "t2" if team_id == "t1" else "t1",
                        "possessions_estimate": pace, "duration_minutes": 40.,
                        "roster_count": 12}, time_basis=TimeBasis.ASSUMED,
                       assumed_available_at=tip + timedelta(hours=8), time_note="synthetic fixed delay")


def history():
    output = []
    for season, n in ((2014, 15), (2015, 30)):
        for game in range(n):
            tip = datetime(season, 6, 1, 20, tzinfo=UTC) + timedelta(days=game * 2)
            event = f"{season}-{game}"
            output.extend((team(event, tip), team(event, tip, "t2", 82.)))
            for i, pid in enumerate(("p1", "p2", "p3")):
                minutes = 0. if (game + i) % 9 == 0 else 15. + ((game + i) % 5) * 4.
                output.append(player(event, tip, minutes, pid))
    return output


@unittest.skipIf(np is None, "NumPy and SciPy are optional in lightweight process CI")
class EngineModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.history = history()
        cls.recipe = fit(cls.history, through_season=2015)
        cls.request = ForecastRequest("p1", "target", "t1", "t2", 2016,
                                      datetime(2016, 6, 2, 20, tzinfo=UTC),
                                      datetime(2016, 6, 1, 20, tzinfo=UTC))

    def forecast(self, records=None, request=None):
        return StructuralModel(self.history if records is None else records,
                               self.recipe).predict(request or self.request)

    def test_normalized_minutes_and_separate_dnp(self):
        forecast = self.forecast()
        self.assertEqual(forecast["minutes_values"], list(range(1, 61)))
        self.assertAlmostEqual(sum(forecast["minutes_probs"]), 1., places=12)
        self.assertTrue(all(p >= 0 for p in forecast["minutes_probs"]))
        self.assertGreater(forecast["p_dnp"], 0.)
        self.assertLess(forecast["p_dnp"], 1.)
        for market in MARKETS:
            price = price_forecast(forecast, market, 4.)
            self.assertAlmostEqual(price["p_over"] + price["p_push"] + price["p_under"], 1., places=12)
            self.assertGreater(price["p_push"], 0.)
            self.assertEqual(price_forecast(forecast, market, 4.5)["p_push"], 0.)

    def test_mixture_matches_independent_poisson_arithmetic(self):
        forecast = {"minutes_probs": [1.] + [0.] * 59,
                    "count_components": {"points": {"means": [3.] * 60, "fano": 1.}}}
        price = price_forecast(forecast, "points", 3., actual=2)
        self.assertAlmostEqual(price["p_under"], poisson.cdf(2, 3), places=12)
        self.assertAlmostEqual(price["p_push"], poisson.pmf(3, 3), places=12)
        self.assertAlmostEqual(price["count_p_actual"], math.exp(-3) * 9 / 2, places=12)
        self.assertAlmostEqual(price["model_mean"], 3.)
        self.assertEqual(price["model_median"], 3.)
        forecast["p_dnp"] = .99
        self.assertEqual(price, price_forecast(forecast, "points", 3., actual=2))
        forecast["count_components"]["points"]["fano"] = 1.5
        self.assertAlmostEqual(count_probability(forecast, "points", 2),
                               nbinom.pmf(2, 6., 2/3), places=12)

    def test_future_target_roster_and_unrelated_invariance(self):
        expected = self.forecast()
        future = player("future", datetime(2026, 6, 1, 20, tzinfo=UTC), 59.)
        target = player("target", self.request.tip_at, 59., available=self.request.as_of - timedelta(hours=1))
        roster = replace(target, record_id="roster:target", kind=ObservationKind.FINAL_ROSTER)
        unrelated = player("unrelated", datetime(2015, 7, 1, 20, tzinfo=UTC), player_id="unrelated")
        amended = list(reversed(self.history)) + [future, target, roster, unrelated, self.history[0]]
        self.assertEqual(expected, self.forecast(amended))
        self.assertEqual(expected, self.forecast(self.history + [unrelated]))

    def test_index_matches_store_and_strict_available_boundary(self):
        equal = player("equal", self.request.as_of - timedelta(days=1), 59., available=self.request.as_of)
        before = player(
            "before", self.request.as_of - timedelta(days=2), 32., available=self.request.as_of - timedelta(microseconds=1))
        records = self.history + [equal, before]
        engine = StructuralModel(records, self.recipe)
        actual = engine.index.select(self.request)
        expected = PointInTimeStore(records).snapshot(self.request).observations
        self.assertEqual([x.manifest_entry() for x in actual], [x.manifest_entry() for x in expected])
        self.assertNotIn(equal.record_id, [o.record_id for o in actual])
        self.assertIn(before.record_id, [o.record_id for o in actual])
        forecast = engine.predict(self.request)
        self.assertTrue(all(o["available_at"] < forecast["request"]["as_of"]
                            for o in forecast["input_manifest"]["observations"]))

    def test_postgame_update_and_request_order(self):
        record = player("new", self.request.as_of - timedelta(days=2), 40.,
                        counts={"points": 70, "rebounds": 20, "assists": 15, "threes": 10})
        before = replace(self.request, as_of=record.available_at)
        after = replace(self.request, as_of=record.available_at + timedelta(seconds=1))
        engine = StructuralModel(self.history + [record], self.recipe)
        first = engine.predict(before)
        later = engine.predict(after)
        self.assertEqual(first["history_rows"] + 1, later["history_rows"])
        self.assertGreater(later["count_components"]["points"]["means"][20],
                           first["count_components"]["points"]["means"][20])
        self.assertEqual(first, engine.predict(before))
        self.assertEqual(later, engine.predict(after))

    def test_revisions_replace_history_instead_of_double_counting(self):
        original = player("new", self.request.as_of - timedelta(days=2), 20.)
        revised = player("new", original.effective_at, 40.,
                         available=original.available_at + timedelta(hours=1))
        engine = StructuralModel(self.history + [original, revised], self.recipe)
        early = replace(self.request, as_of=original.available_at + timedelta(seconds=1))
        before = engine.predict(early)
        after = engine.predict(self.request)
        self.assertEqual(before["history_rows"], after["history_rows"])
        self.assertEqual(after, self.forecast(self.history + [revised]))

    def test_fit_freeze_and_recipe_hash(self):
        future = player("2025", datetime(2025, 6, 1, 20, tzinfo=UTC), 59.)
        self.assertEqual(self.recipe, fit(self.history + [future], through_season=2015))
        altered = copy.deepcopy(self.recipe)
        altered["count_fano"][0] += .01
        with self.assertRaisesRegex(ValueError, "recipe hash"):
            StructuralModel(self.history, altered)
        with self.assertRaises(ProtectedDataError):
            fit(self.history, through_season=2025)
        with self.assertRaises(ValueError):
            fit(self.history, through_season=2015, shrinkage=3)
        with self.assertRaises(ProtectedDataError):
            self.forecast(request=replace(self.request, season=2026))
        with self.assertRaises(ValueError):
            self.forecast(request=replace(self.request, season=2015))

    def test_minute_and_count_outcomes_never_enter_request(self):
        with self.assertRaises(TypeError):
            ForecastRequest("p1", "g", "t1", "t2", 2016,
                            self.request.tip_at, self.request.as_of, line=4.5)
        examples = list(training_examples(self.history, 2015))
        self.assertEqual(len(examples), 90)
        self.assertGreater(sum(row.payload["did_not_play"] for _, row in examples), 0)
        self.assertTrue(all((r.tip_at - r.as_of).total_seconds() == 86400 for r, _ in examples))
        record = self.forecast()
        probability = count_probability(record, "points", 8)
        self.assertGreater(probability, 0)
        self.assertEqual(canonical_json(record), canonical_json(self.forecast()))

    def test_training_request_ignores_target_final_team(self):
        before = {(r.event_id, r.entity_id): r for r, _ in training_examples(self.history, 2015)}
        amended = []
        target = ("2015-12", "p1")
        for observation in self.history:
            if (observation.event_id, observation.entity_id) == target:
                payload = dict(observation.payload)
                payload["team_id"], payload["opponent_id"] = "alien-final-team", "another-final-team"
                observation = replace(observation, payload=payload, payload_hash=None)
            amended.append(observation)
        after = {(r.event_id, r.entity_id): r for r, _ in training_examples(amended, 2015)}
        self.assertEqual(before[target], after[target])

    def test_unknown_player_has_explicit_prior_fallback(self):
        unknown = replace(self.request, entity_id="unknown")
        forecast = self.forecast(request=unknown)
        self.assertEqual(forecast["fallback"], "league_prior")
        self.assertEqual(forecast["history_rows"], 0)
        self.assertTrue(all(np.isfinite(forecast["count_components"][m]["means"]).all()
                            for m in MARKETS))

    def test_compact_validation_keeps_identical_forecast_values(self):
        model = StructuralModel(self.history, self.recipe)
        full = model.predict(self.request)
        compact = model.predict(self.request, include_manifest=False)
        for key in ("p_dnp", "minutes_values", "minutes_probs", "count_components",
                    "input_max_available_at", "history_rows", "recipe_hash"):
            self.assertEqual(full[key], compact[key])
        self.assertIsNone(compact["input_manifest_hash"])
        self.assertNotIn("input_manifest", compact)


if __name__ == "__main__":
    unittest.main()
