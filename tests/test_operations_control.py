import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from research.operations.control import evaluate, qualification_errors, write_result, clock


NOW = '2026-09-07T12:00:00Z'


def policy():
    return {'schema': 'operations-policy-v1', 'policy_id': 'test-shadow-v1',
            'required_sources': {'bettingpros': {'max_age_seconds': 10800},
                                 'polymarket': {'max_age_seconds': 10800}},
            'max_clock_skew_seconds': 0, 'models': []}


def health():
    source = {'status': 'OK', 'checked_at': '2026-09-07T11:50:00Z',
              'received_at': '2026-09-07T11:49:59Z'}
    return {'schema': 'collection-health-v1', 'run_id': 'run-1', 'status': 'OK',
            'started_at': '2026-09-07T11:45:00Z', 'completed_at': '2026-09-07T11:55:00Z',
            'sources': {'bettingpros': {**source, 'markets': {
                'points': {'offers': 2, 'any_book_coherent_pairs': 1,
                           'books': {'14': {'name': 'Fanatics', 'coherent_pairs': 1,
                                            'fresh_pairs': 1, 'stale_pairs': 0,
                                            'missing_quote_clock_pairs': 0}}}}},
                        'polymarket': dict(source)}}


def validated_model():
    """Synthetic attestation, not a claim about any repository model."""
    return {'model_id': 'synthetic-model', 'recipe_sha256': 'a' * 64,
            'state': 'validated', 'required_sources': ['bettingpros'],
            'qualification': {
                'schema': 'operations-qualification-v1', 'model_id': 'synthetic-model',
                'recipe_sha256': 'a' * 64, 'receipt_sha256': 'b' * 64,
                'registration_sha256': 'c' * 64,
                'record_url': 'https://github.com/example/synthetic/blob/' + 'd' * 40 + '/receipt.json',
                'evidence_kind': 'independent-prospective',
                'registered_at': '2026-01-01T00:00:00Z',
                'recipe_frozen_at': '2026-01-02T00:00:00Z',
                'evaluation_started_at': '2026-02-01T00:00:00Z',
                'evaluation_completed_at': '2026-08-01T00:00:00Z',
                'recorded_at': '2026-08-02T00:00:00Z',
                'independent_reproduction': 'PASS', 'decision': 'VALIDATE',
                'checks': {key: True for key in (
                    'all_registered_gates_passed', 'timing_verified', 'costs_accounted',
                    'evaluation_was_unused', 'forecast_and_execution_rules_frozen')},
                'net_return_interval': [.01, .05],
                'uncertainty_method': 'synthetic prespecified paired day-block interval'}}


class OperationsControlTests(unittest.TestCase):
    def run_status(self, data=None, settings=None, previous=None, now=NOW):
        return evaluate(health() if data is None else data,
                        policy() if settings is None else settings, previous, now)

    def test_healthy_data_without_validated_model_is_normal_and_quiet(self):
        first = self.run_status()
        self.assertEqual(first['data_status'], 'DATA_HEALTHY')
        self.assertEqual(first['model_status'], 'MODEL_NOT_READY')
        self.assertEqual(first['active_incidents'], [])
        self.assertEqual(first['new_events'], [])
        self.assertFalse(first['actionable_bet_notifications'])
        self.assertEqual(self.run_status(previous=first)['new_events'], [])

    def test_missing_failed_and_stale_source_fail_closed(self):
        for kind in ('missing', 'failed', 'stale'):
            data = health()
            source = data['sources']['bettingpros']
            if kind == 'missing':
                del data['sources']['bettingpros']
            elif kind == 'failed':
                source['status'] = 'FAILED'
            else:
                data['started_at'] = '2026-09-07T08:00:00Z'
                source['received_at'] = '2026-09-07T08:59:59Z'
            with self.subTest(kind=kind):
                status = self.run_status(data)
                self.assertEqual(status['data_status'], 'DATA_DEGRADED')
                self.assertEqual(len(status['new_events']), 1)
                self.assertFalse(status['actionable_bet_notifications'])

    def test_source_clocks_must_be_explicit_ordered_and_not_future(self):
        for checked, received in [
                ('2026-09-07T12:00:01Z', '2026-09-07T11:59:59Z'),
                ('2026-09-07T11:50:00Z', '2026-09-07T11:50:01Z'),
                ('2026-09-07T11:50:00', '2026-09-07T11:49:59Z'),
                ('2026-09-07T11:50:00Z', None)]:
            data = health()
            data['sources']['polymarket'].update(checked_at=checked, received_at=received)
            with self.subTest(checked=checked, received=received):
                self.assertIn('SOURCE_CLOCK_INVALID', [row['code'] for row in self.run_status(data)['active_incidents']])

    def test_run_clock_and_schema_fail_closed_even_when_sources_look_healthy(self):
        for change in [{'schema': 'collection-health-v2'}, {'completed_at': '2026-09-07T12:00:01Z'},
                       {'completed_at': '2026-09-07T11:00:00Z'}, {'status': 'MAYBE'}, {'status': []}]:
            settings = policy()
            settings['models'] = [validated_model()]
            status = self.run_status({**health(), **change}, settings)
            self.assertEqual(status['data_status'], 'DATA_DEGRADED')
            self.assertEqual(status['ready_models'], [])

    def test_invalid_policy_never_defaults_to_a_ready_model(self):
        for change in [{'schema': 'operations-policy-v2'}, {'required_sources': {}},
                       {'models': [{'model_id': 'oops'}]}, {'max_clock_skew_seconds': float('nan')}]:
            status = self.run_status(settings={**policy(), **change})
            self.assertEqual(status['ready_models'], [])
            self.assertEqual(status['active_incidents'][0]['code'], 'POLICY_INVALID')

    def test_no_events_is_healthy_collection_not_an_offer(self):
        data = health()
        source = data['sources']['bettingpros']
        source.update(status='NO_EVENTS', markets={})
        status = self.run_status(data)
        self.assertEqual(status['data_status'], 'DATA_HEALTHY')
        self.assertEqual(status['sources']['bettingpros']['coverage'], {})
        self.assertEqual(status['ready_models'], [])

    def test_required_market_records_cannot_silently_disappear(self):
        settings = policy()
        settings['required_sources']['bettingpros']['required_markets'] = ['points', 'assists']
        status = self.run_status(settings=settings)
        self.assertEqual(status['data_status'], 'DATA_DEGRADED')
        self.assertIn('COVERAGE_INVALID', [row['code'] for row in status['active_incidents']])

    def test_malformed_source_status_or_false_empty_slate_cannot_look_healthy(self):
        for state in ([], {}, 'NO_EVENTS'):
            data = health()
            data['sources']['bettingpros']['status'] = state
            with self.subTest(state=state):
                status = self.run_status(data)
                self.assertEqual(status['data_status'], 'DATA_DEGRADED')

    def test_incomplete_and_unclocked_quotes_are_unavailable(self):
        for kind in ('unpaired', 'missing-clock', 'stale-clock', 'inconsistent'):
            data = health()
            market = data['sources']['bettingpros']['markets']['points']
            book = market['books']['14']
            if kind == 'unpaired':
                market.update(any_book_coherent_pairs=0, books={})
            elif kind == 'inconsistent':
                book['fresh_pairs'] = 2
            else:
                book['fresh_pairs'] = 0
                book['missing_quote_clock_pairs' if kind == 'missing-clock' else 'stale_pairs'] = 1
            with self.subTest(kind=kind):
                status = self.run_status(data)
                self.assertEqual(status['data_status'], 'DATA_DEGRADED')
                self.assertEqual(len(status['active_incidents']), 1)

    def test_partial_book_coverage_retains_usable_book_without_claiming_all_quotes(self):
        data = health()
        market = data['sources']['bettingpros']['markets']['points']
        market['books']['10'] = {'coherent_pairs': 1, 'fresh_pairs': 0, 'stale_pairs': 1,
                                 'missing_quote_clock_pairs': 0}
        status = self.run_status(data)
        self.assertEqual(status['data_status'], 'DATA_HEALTHY')
        self.assertEqual(status['sources']['bettingpros']['coverage']['points']['books_with_fresh_pairs'], ['14'])

    def test_same_incident_does_not_alert_again_when_age_or_run_changes(self):
        data = health()
        data['started_at'] = '2026-09-07T07:59:59Z'
        data['sources']['bettingpros']['received_at'] = '2026-09-07T08:00:00Z'
        first = self.run_status(data)
        data['run_id'] = 'run-2'
        second = self.run_status(data, previous=first, now='2026-09-07T12:10:00Z')
        self.assertEqual(second['new_events'], [])
        self.assertEqual(first['active_incidents'][0]['incident_id'], second['active_incidents'][0]['incident_id'])
        self.assertEqual(first['active_incidents'][0]['first_seen_at'], second['active_incidents'][0]['first_seen_at'])
        recovered = self.run_status(previous=second, now='2026-09-07T12:20:00Z')
        self.assertEqual(recovered['new_events'][0]['kind'], 'INCIDENT_RECOVERED')
        self.assertEqual(recovered['active_incidents'], [])
        self.assertEqual(len(recovered['recent_events']), 2)

    def test_alert_history_retains_48_hours_even_with_more_than_100_events(self):
        previous = self.run_status()
        base = {'kind': 'INCIDENT_RECOVERED', 'at': '2026-09-06T00:00:00Z', 'summary': 'Synthetic old recovery.'}
        previous['recent_events'] = [{**base, 'event_id': f'{i:024x}'} for i in range(150)]
        previous['recent_events'].append({**base, 'at': '2026-09-05T11:59:59Z', 'event_id': 'f' * 24})
        status = self.run_status(previous=previous)
        self.assertEqual(len(status['recent_events']), 150)

    def test_invalid_previous_document_is_visible_and_blocks_readiness(self):
        for change in [{'schema': 'operations-status-v2'}, {'generated_at': '2026-09-08T00:00:00Z'},
                       {'recent_events': [{}]}, {'ready_models': [{}]}, {'active_incidents': [None]}]:
            previous = {**self.run_status(), **change}
            settings = policy()
            settings['models'] = [validated_model()]
            status = self.run_status(settings=settings, previous=previous)
            self.assertEqual(status['ready_models'], [])
            self.assertIn('PREVIOUS_STATUS_INVALID', [row['code'] for row in status['active_incidents']])

    def test_lifecycle_states_do_not_promote_from_software_success(self):
        for state in ('registered', 'collecting', 'evaluation_due', 'stopped'):
            model = validated_model()
            model['state'] = state
            settings = policy()
            settings['models'] = [model]
            status = self.run_status(settings=settings)
            self.assertEqual(status['data_status'], 'DATA_HEALTHY')
            self.assertEqual(status['ready_models'], [])
            self.assertFalse(status['models'][0]['qualification_valid'])

    def test_evaluation_due_emits_one_reminder_bound_to_run(self):
        model = validated_model()
        model['state'] = 'evaluation_due'
        settings = policy()
        settings['models'] = [model]
        first = self.run_status(settings=settings)
        self.assertEqual(first['new_events'][0]['kind'], 'MODEL_EVALUATION_DUE')
        self.assertEqual(first['new_events'][0]['run_id'], 'run-1')
        self.assertEqual(self.run_status(settings=settings, previous=first)['new_events'], [])

    def test_synthetic_qualification_is_bound_to_recipe_and_independent_future_test(self):
        changes = [{'recipe_sha256': 'b' * 64}, {'evidence_kind': 'reused-development'},
                   {'independent_reproduction': 'FAIL'}, {'recorded_at': '2026-09-08T00:00:00Z'},
                   {'evaluation_started_at': '2026-01-02T00:00:00Z'},
                   {'net_return_interval': [-.01, .1]}, {'net_return_interval': [True, .1]},
                   {'record_url': 'https://github.com/example/synthetic/blob/main/receipt.json'},
                   {'record_url': 'https://example.com/mutable/receipt.json'}]
        for change in changes:
            model = validated_model()
            model['qualification'].update(change)
            with self.subTest(change=change):
                self.assertTrue(qualification_errors(model, clock(NOW)))
        for flag in validated_model()['qualification']['checks']:
            model = validated_model()
            model['qualification']['checks'][flag] = False
            self.assertTrue(qualification_errors(model, clock(NOW)))

    def test_missing_qualification_is_model_problem_not_source_failure(self):
        model = validated_model()
        del model['qualification']
        settings = policy()
        settings['models'] = [model]
        status = self.run_status(settings=settings)
        self.assertEqual(status['data_status'], 'DATA_HEALTHY')
        self.assertEqual(status['model_status'], 'MODEL_NOT_READY')
        self.assertFalse(status['actionable_bet_notifications'])

    def test_malformed_qualification_blocks_without_crashing_the_controller(self):
        for field, bad_values in {'checks': [[], None, 123, 'PASS'],
                                  'record_url': [123, [], {}, None]}.items():
            for value in bad_values:
                settings = policy()
                model = validated_model()
                model['qualification'][field] = value
                settings['models'] = [model]
                with self.subTest(field=field, value=value):
                    status = self.run_status(settings=settings)
                    self.assertEqual(status['data_status'], 'DATA_HEALTHY')
                    self.assertEqual(status['ready_models'], [])
                    self.assertFalse(status['models'][0]['qualification_valid'])
                    self.assertIn('Qualification unavailable or invalid', status['models'][0]['blockers'][0])

    def test_source_clocks_must_belong_to_the_run_envelope(self):
        settings = policy()
        settings['models'] = [validated_model()]
        for started, completed in [
                ('2026-09-06T00:00:00Z', '2026-09-06T00:01:00Z'),
                ('2026-09-07T11:49:59.500000Z', '2026-09-07T11:55:00Z'),
                ('2026-09-07T11:45:00Z', '2026-09-07T11:49:59.500000Z')]:
            data = health()
            data.update(started_at=started, completed_at=completed)
            with self.subTest(started=started, completed=completed):
                status = self.run_status(data, settings)
                self.assertEqual(status['data_status'], 'DATA_DEGRADED')
                self.assertEqual(status['ready_models'], [])
                self.assertIn('SOURCE_CLOCK_INVALID', [row['code'] for row in status['active_incidents']])

    def test_stale_run_is_blocked_even_when_its_internal_clock_order_is_valid(self):
        data = health()
        for key in ('started_at', 'completed_at'):
            data[key] = data[key].replace('2026-09-07', '2026-09-06')
        for source in data['sources'].values():
            for key in ('received_at', 'checked_at'):
                source[key] = source[key].replace('2026-09-07', '2026-09-06')
        status = self.run_status(data)
        self.assertIn('RUN_STALE', [row['code'] for row in status['active_incidents']])
        self.assertNotIn('SOURCE_CLOCK_INVALID', [row['code'] for row in status['active_incidents']])

    def test_synthetic_qualified_model_only_becomes_shadow_ready_and_is_withdrawn_on_failure(self):
        settings = policy()
        settings['models'] = [validated_model()]
        original = copy.deepcopy(settings)
        first = self.run_status(settings=settings)
        self.assertEqual(settings, original)
        self.assertEqual(first['ready_models'], ['synthetic-model'])
        self.assertEqual(first['new_events'][0]['kind'], 'MODEL_READY')
        self.assertFalse(first['actionable_bet_notifications'])
        self.assertEqual(self.run_status(settings=settings, previous=first)['new_events'], [])
        bad = health()
        bad['sources']['bettingpros']['status'] = 'FAILED'
        blocked = self.run_status(bad, settings, previous=first)
        self.assertEqual(blocked['ready_models'], [])
        self.assertIn('MODEL_NOT_READY', [event['kind'] for event in blocked['new_events']])

    def test_new_recipe_with_same_model_name_has_its_own_readiness_event(self):
        settings = policy()
        settings['models'] = [validated_model()]
        first = self.run_status(settings=settings)
        settings['models'][0]['recipe_sha256'] = 'e' * 64
        settings['models'][0]['qualification']['recipe_sha256'] = 'e' * 64
        second = self.run_status(settings=settings, previous=first)
        self.assertEqual(second['new_events'][0]['kind'], 'MODEL_READY')
        self.assertNotEqual(first['new_events'][0]['event_id'], second['new_events'][0]['event_id'])

    def test_outputs_are_append_only_and_cli_records_a_missing_input_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = root / 'policy.json'
            settings.write_text(json.dumps(policy()))
            status_path, decisions = root / 'status.json', root / 'decisions.jsonl'
            process = subprocess.run([sys.executable, '-m', 'research.operations.control',
                '--health', str(root / 'missing.json'), '--policy', str(settings),
                '--now', NOW, '--output', str(status_path), '--decisions', str(decisions)], capture_output=True, text=True)
            self.assertEqual(process.returncode, 2, process.stderr)
            saved = json.loads(status_path.read_text())
            self.assertEqual(saved['data_status'], 'DATA_DEGRADED')
            self.assertEqual(json.loads(decisions.read_text())['kind'], 'INCIDENT_OPENED')
            before = status_path.read_bytes()
            with self.assertRaises(FileExistsError):
                write_result(self.run_status(), status_path, root / 'other.jsonl')
            self.assertEqual(status_path.read_bytes(), before)
            self.assertFalse((root / 'other.jsonl').exists())


if __name__ == '__main__':
    unittest.main()
