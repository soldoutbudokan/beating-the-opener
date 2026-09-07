"""Runner integration guards, using synthetic records and no empirical scores."""
import copy
from datetime import datetime, timedelta, timezone
import gzip
import json
import math
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from research.engine import run


IMPLEMENTATION = [{'path': 'model.py', 'sha256': 'a'*64}]


def timing():
    return {'status': 'PASS', 'returncode': 0, 'implementation': IMPLEMENTATION,
            'tests': [{'path': 'tests/test_model.py', 'sha256': 'b'*64}]}


def reports():
    scores = {'quotes': 7604, 'settled_nonpush': 7473, 'log_loss': .68,
              'calibration': {'gap': .001, 'by_model_mean': [{'gap': .002, 'n': 20}]},
              'count_by_market': {m: {'nll': 1., 'baseline_nll': 1.1}
                                  for m in ('points', 'rebounds', 'assists', 'threes')},
              'comparisons': {'close': {'estimate': .001, 'dates': 10, 'se': .01, 't': .1}}}
    return {'8h': {'scores': scores}, '24h': {'scores': copy.deepcopy(scores)}}


class RunnerTests(unittest.TestCase):
    def test_native_schedule_and_neutral_fallback(self):
        tip = datetime(2025, 6, 1, 20, tzinfo=timezone.utc)
        at = tip - timedelta(hours=24)
        event = {'home_id': 8, 'away_id': 9, 'home_score': 999}
        prior = SimpleNamespace(event_id='past', effective_at=at-timedelta(days=2),
            available_at=at-timedelta(days=1), record_id='player', payload={'team_id': '9'})
        request, reason = run.forecast_request('p', 'g', at, tip, [prior], event)
        self.assertEqual((request.team_id, request.opponent_id, reason), ('9', '8', None))
        request, reason = run.forecast_request('p', 'g', at, tip, [], event)
        self.assertEqual(request.team_id, 'unknown-team')
        self.assertEqual(reason, 'no_prior_player_identity')
        prior.payload['team_id'] = '7'
        request, reason = run.forecast_request('p', 'g', at, tip, [prior], event)
        self.assertEqual((request.team_id, request.opponent_id), ('unknown-team', 'unknown-opponent'))
        self.assertEqual(reason, 'prior_team_outside_scheduled_matchup')
        with self.assertRaisesRegex(ValueError, 'missing'):
            run.forecast_request('p', 'g', at, tip, [], {'home_id': None, 'away_id': 9})

    def test_target_or_unavailable_history_cannot_supply_team(self):
        tip = datetime(2025, 6, 1, 20, tzinfo=timezone.utc)
        at = tip-timedelta(hours=24)
        record = SimpleNamespace(event_id='g', effective_at=tip, available_at=at-timedelta(hours=1),
                                 record_id='target', payload={'team_id': '8'})
        _, reason = run.forecast_request('p', 'g', at, tip, [record], {'home_id': 8, 'away_id': 9})
        self.assertEqual(reason, 'no_prior_player_identity')

    def test_frozen_fit_rejects_changed_inputs_code_or_recipe(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(run, 'implementation', return_value=IMPLEMENTATION):
            root = Path(temp); raw = root/'raw'; raw.mkdir()
            (raw/'manifest.json').write_text('{}')
            recipe = {'shrinkage': 1, 'through_season': 2024, 'recipe_hash': 'c'*64}
            run.dump(root/'recipe_1.json', recipe)
            metadata = {'status': 'frozen_before_2025', 'registration_commit': run.REGISTRATION,
                'recipes': {'1': run.digest(root/'recipe_1.json')}, 'implementation': IMPLEMENTATION,
                'source_manifest_sha256': run.digest(raw/'manifest.json')}
            run.dump(root/'fit.json', metadata)
            self.assertEqual(run.frozen_fit(root/'recipe_1.json', raw)[0], recipe)
            (raw/'manifest.json').write_text('{"changed":true}')
            with self.assertRaisesRegex(ValueError, 'Inputs changed'):
                run.frozen_fit(root/'recipe_1.json', raw)
            (raw/'manifest.json').write_text('{}')
            with patch.object(run, 'implementation', return_value=[]):
                with self.assertRaisesRegex(ValueError, 'Implementation changed'):
                    run.frozen_fit(root/'recipe_1.json', raw)
            (root/'recipe_1.json').write_text('{}')
            with self.assertRaises(ValueError):
                run.frozen_fit(root/'recipe_1.json', raw)

    def test_interrupted_attempt_cannot_be_rerun_elsewhere(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = {'shrinkage': 1, 'recipe_hash': 'a'*64}
            second = {'shrinkage': 2, 'recipe_hash': 'b'*64}
            run.dump(root/'recipe_1.json', first); run.dump(root/'recipe_2.json', second)
            run.reserve_evaluation(root/'recipe_1.json', first, root/'output1', 1)
            with self.assertRaises(ValueError):
                run.reserve_evaluation(root/'recipe_2.json', second, root/'output2', 1)
            with self.assertRaises(FileExistsError):
                run.reserve_evaluation(root/'recipe_1.json', first, root/'output3', 2)

    def test_timing_gate_requires_actual_successful_test_process(self):
        with patch.object(run, 'implementation', return_value=IMPLEMENTATION), patch.object(run.subprocess, 'run') as process:
            process.return_value = subprocess.CompletedProcess([], 1, '', 'assertion failed')
            with self.assertRaisesRegex(ValueError, 'tests failed'):
                run.timing_checks()
            process.return_value = subprocess.CompletedProcess([], 0, '', 'OK')
            result = run.timing_checks()
            self.assertEqual(result['returncode'], 0)
            self.assertIn('discover', result['command'])
            self.assertTrue(result['tests'])

    def test_derived_gates_fail_population_and_stale_timing(self):
        with patch.object(run, 'implementation', return_value=IMPLEMENTATION):
            model = reports()
            result = run.derived_results(model, 1, timing(), ['prior use'])
            self.assertEqual(result['status'], 'PASS')
            model['24h']['scores']['quotes'] = 7601
            result = run.derived_results(model, 1, timing(), ['prior use'])
            self.assertEqual(result['status'], 'FAIL')
            self.assertEqual(next(g['status'] for g in result['gates'] if g['id']=='POPULATION'), 'FAIL')
            changed = timing(); changed['implementation'] = []
            with self.assertRaisesRegex(ValueError, 'timing test receipt'):
                run.derived_results(model, 1, changed, ['prior use'])

    def test_verifier_rederives_result_even_when_hash_review_passes(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(run, 'implementation', return_value=IMPLEMENTATION):
            root = Path(temp); prior = root/'prior.json'; run.dump(prior, {'prior_2025_uses': ['prior use']})
            report = reports(); recipe = {'recipe_hash': 'a'*64, 'shrinkage': 1}
            run.dump(root/'recipe.json', recipe); sha = run.digest(root/'recipe.json')
            run.dump(root/'fit.json', {'recipes': {'1': sha}, 'implementation': IMPLEMENTATION})
            run.dump(root/'evaluation_lock.json', {'recipe_hash': recipe['recipe_hash'], 'recipe_sha256': sha, 'attempt': 1})
            run.dump(root/'scores.json', report); run.dump(root/'timing.json', timing())
            result = run.derived_results(report, 1, timing(), ['prior use'])
            run.dump(root/'results.json', result)
            payload = ''.join(json.dumps({'scenario': s, 'row': {'quote_id': 'q'}})+'\n' for s in ('8h', '24h'))
            (root/'forecasts.jsonl.gz').write_bytes(gzip.compress(payload.encode()))
            for scenario in ('8h', '24h'):
                (root/f'input_records_{scenario}.json.gz').write_bytes(gzip.compress(b'[]'))
            with patch.object(run, 'PRIOR_USES', prior), patch('research.engine.review.review_structural'), \
                 patch('research.engine.scoring.verify_saved_report', return_value={'status': 'PASS'}), \
                 patch.object(run, 'saved_distributions', return_value={'status': 'PASS'}), \
                 patch.object(run, 'frozen_population', return_value={'status': 'PASS'}):
                self.assertEqual(set(run.verify_stage(root)), {'8h', '24h'})
                # Even a rehashed summary cannot claim a different decision.
                result['models']['8h']['log_loss'] = .50
                (root/'results.json').write_text(json.dumps(result))
                with self.assertRaisesRegex(ValueError, 'summary differs'):
                    run.verify_stage(root)

    def test_shared_manifest_reconstruction_and_future_source_rejection(self):
        from research.engine.model import price_forecast
        from research.engine.store import payload_digest
        recipe = {'recipe_hash': 'r'*64}
        request = {'entity_id': 'p', 'event_id': 'g', 'team_id': 'A', 'opponent_id': 'B',
                   'season': 2025, 'as_of': '2025-06-01T00:00:00Z', 'tip_at': '2025-06-02T00:00:00Z'}
        source = {'kind': 'historical_outcome', 'event_id': 'old', 'season': 2024,
                  'available_at': '2024-06-01T08:00:00Z', 'effective_at': '2024-06-01T00:00:00Z'}
        full = {'schema': 'structural-inputs-v1', 'request': request, 'observations': [source]}
        code = payload_digest({'implementation': IMPLEMENTATION})
        forecast = {'request': request, 'recipe_hash': recipe['recipe_hash'], 'implementation_hash': code,
                    'minutes_values': list(range(1, 61)), 'minutes_probs': [1.]+[0.]*59, 'p_dnp': .1,
                    'count_components': {'points': {'means': [n/10 for n in range(1, 61)], 'fano': 1.2}},
                    'input_manifest_hash': payload_digest(full),
                    'input_manifest': {'schema': full['schema'], 'request': request, 'observation_ids': [0]}}
        row = {'player_id': 'p', 'game_id': 'g', 'as_of': request['as_of'], 'tip_at': request['tip_at'],
               'market': 'points', 'line': .5, 'actual': 0,
               'model_hash': payload_digest({'recipe_hash': recipe['recipe_hash'], 'implementation_hash': code}),
               'implementation_hash': code, 'distribution': forecast,
               'p_dnp': .1, 'minutes_values': forecast['minutes_values'], 'minutes_probs': forecast['minutes_probs'],
               'baseline_distribution': {'mean': .1, 'alpha': 0}, 'baseline_count_p_actual': math.exp(-.1)}
        row.update(price_forecast(forecast, 'points', .5, 0))
        with patch.object(run, 'implementation', return_value=IMPLEMENTATION):
            self.assertEqual(run.saved_distributions([row], recipe, [source])['status'], 'PASS')
            source['available_at'] = request['as_of']
            forecast['input_manifest_hash'] = payload_digest(full)
            with self.assertRaisesRegex(ValueError, 'future/protected'):
                run.saved_distributions([row], recipe, [source])


if __name__ == '__main__':
    unittest.main()
