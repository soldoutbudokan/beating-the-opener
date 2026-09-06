"""Offline boundary tests for research decisions, source pilots and evidence."""
import copy
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'tools' / 'research.py'
spec = importlib.util.spec_from_file_location('research_helper', SCRIPT)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


def policy():
    return {'bets': 3, 'wins': 1, 'losses': 1, 'pushes': 0, 'voids': 1,
            'unresolved': 0, 'stake': 2., 'profit': .5, 'matched_claimed_profit': 1.2,
            'over_bets': 1, 'roi': {'estimate': .25, 'ci95': [-.5, .7]}}


def result_fixture():
    # Deliberately much higher candidate profit with no clear forecast gain.
    simple = {'log_loss': .69, 'economics': {'0.05': policy(), '0.1': policy()}}
    rich = copy.deepcopy(simple)
    rich['log_loss'] = .691
    rich['economics']['0.05'].update(profit=1., roi={'estimate': .5, 'ci95': [-.2, .9]})
    scenario = {'quotes': 11, 'settled_quotes': 10, 'void_quotes': 1, 'dates': 5,
                'market': {'log_loss': .688}, 'models': {'simple': simple, 'rich': rich},
                'comparisons': {'rich_minus_simple': {
                    'estimate': .001, 'ci95': [-.002, .003], 'n': 10, 'dates': 5}}}
    return {'8h': scenario, '24h': copy.deepcopy(scenario)}


class TemporaryFiles(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()


class SourcePilotTests(TemporaryFiles):
    def sample(self, **changes):
        row = {'query_id': 'q1', 'family': 'role', 'entity_id': 'p1', 'event_id': 'g2',
               'source_id': 'news1', 'prediction_at': '2025-05-20T12:00:00Z',
               'available_at': '2025-05-20T06:00:00-04:00', 'matched': 'true', 'value': 'limited'}
        row.update(changes)
        return row

    def run_sample(self, rows, expected=None, coverage=.9, basis='observed', note=''):
        path = self.root / 'sample.csv'
        with path.open('w', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=self.sample().keys())
            writer.writeheader()
            writer.writerows(rows)
        return helper.check_source(path, len(rows) if expected is None else expected, coverage, basis, note)

    def test_equivalent_timezones_and_strict_boundary(self):
        result = self.run_sample([self.sample()])
        self.assertEqual(result['status'], 'PASS_SAMPLE')
        self.assertEqual(result['matched_prior_source_age_hours']['median'], 2.)
        late = self.run_sample([self.sample(available_at='2025-05-20T08:00:00-04:00')])
        self.assertEqual(late['status'], 'STOP')
        self.assertIn('not_available_before_prediction', late['blocking_reasons'])

    def test_future_record_cannot_hide_inside_high_coverage(self):
        rows = [self.sample(query_id=str(i)) for i in range(100)]
        rows[0]['available_at'] = '2025-05-21T12:00:00Z'
        self.assertEqual(self.run_sample(rows)['status'], 'STOP')

    def test_missing_requests_stay_in_denominator(self):
        rows = [self.sample(), self.sample(query_id='missing', matched='false', source_id='', value='', available_at='')]
        result = self.run_sample(rows)
        self.assertEqual(result['families']['role']['usable_fraction'], .5)
        self.assertEqual(result['status'], 'STOP')
        dropped = self.run_sample([rows[0]], expected=2)
        self.assertIn('requested_row_count_mismatch', dropped['blocking_reasons'])

    def test_weak_family_cannot_hide_inside_strong_family(self):
        rows = [self.sample(query_id=str(i)) for i in range(100)]
        rows.append(self.sample(query_id='q2', family='injuries', value=''))
        result = self.run_sample(rows)
        self.assertIn('insufficient_coverage:injuries', result['blocking_reasons'])

    def test_duplicate_requests_are_normalized_and_rejected(self):
        result = self.run_sample([self.sample(), self.sample(query_id=' q1 ')])
        self.assertIn('duplicate_query_family', result['blocking_reasons'])
        self.assertEqual(result['families']['role']['usable'], 0)

    def test_unknown_times_nonfinite_values_and_false_matches_are_not_zeroes(self):
        for changes in ({'available_at': ''}, {'value': 'NaN'}, {'value': 'inf'},
                        {'matched': 'false'}, {'available_at': '2025-05-19'}):
            with self.subTest(changes=changes):
                result = self.run_sample([self.sample(**changes)])
                self.assertEqual(result['status'], 'STOP')
        self.assertEqual(self.run_sample([self.sample(value='0')])['status'], 'PASS_SAMPLE')

    def test_assumed_time_needs_explanation_and_stays_labeled(self):
        with self.assertRaises(ValueError):
            self.run_sample([self.sample()], basis='assumed')
        result = self.run_sample([self.sample()], basis='assumed', note='Past game plus 24 hours')
        self.assertEqual(result['status'], 'PASS_UNDER_ASSUMPTION')

    def test_cli_returns_stop_and_writes_reviewable_reason(self):
        self.run_sample([self.sample(available_at='2025-05-21T12:00:00Z')])
        output = self.root / 'check.json'
        process = subprocess.run([sys.executable, str(SCRIPT), 'source-check', str(self.root/'sample.csv'),
                                  '--expected-rows', '1', '--min-coverage', '.9', '--time-basis', 'observed',
                                  '--output', str(output)], capture_output=True, text=True)
        self.assertEqual(process.returncode, 2, process.stderr)
        self.assertEqual(json.loads(output.read_text())['status'], 'STOP')


class DecisionTests(TemporaryFiles):
    def save(self, result=None):
        self.result = self.root / 'results.json'
        self.receipt = self.root / 'receipt.json'
        self.result.write_text(json.dumps(result or result_fixture()))
        self.receipt.write_text(json.dumps({'status': 'complete', 'research_year': 2025,
                                           'results_sha256': helper.digest(self.result)}))

    def review(self, minimum=None):
        return helper.review_results(self.result, self.receipt, 'rich_minus_simple', 'reused-development', minimum)

    def test_larger_profit_does_not_turn_uncertain_forecasts_into_a_win(self):
        self.save()
        review = self.review()
        self.assertTrue(all('No clear improvement' in row['conclusion'] for row in review['scenarios'].values()))
        text = helper.render_review(review)
        self.assertIn('cannot establish an independent betting edge', text)
        self.assertIn('No minimum useful improvement', text)
        self.assertEqual(text.count('/ 5%'), 4)
        self.assertEqual(text.count('/ 10%'), 4)

    def test_confidence_boundaries_and_practical_size(self):
        assess = helper.conclusion
        self.assertIn('No clear improvement', assess(-.001, -.003, 0))
        self.assertIn('Worse forecasts', assess(.002, .001, .003))
        self.assertIn('improvement is visible', assess(-.002, -.003, -.001))
        self.assertIn('useful size remains uncertain', assess(-.002, -.003, -.001, .002))
        self.assertIn('does not reach', assess(-.001, -.002, -.0005, .003))

    def test_changed_or_incomplete_evidence_is_rejected(self):
        self.save()
        self.result.write_text(self.result.read_text() + '\n')
        with self.assertRaisesRegex(ValueError, 'differ'):
            self.review()
        self.save()
        receipt = json.loads(self.receipt.read_text())
        receipt['status'] = 'running'
        self.receipt.write_text(json.dumps(receipt))
        with self.assertRaisesRegex(ValueError, 'not complete'):
            self.review()

    def test_unpaired_populations_and_inconsistent_effects_are_rejected(self):
        for change in ({'n': 9}, {'estimate': -.01}, {'ci95': [.002, -.001]}, {'ci95': [float('nan'), .1]}):
            data = result_fixture()
            data['8h']['comparisons']['rich_minus_simple'].update(change)
            self.save(data)
            with self.assertRaises(ValueError):
                self.review()

    def test_refunds_and_wrong_roi_cannot_silently_change_returns(self):
        for change in ({'voids': 0}, {'stake': 3}, {'profit': .8}, {'over_bets': 10}):
            data = result_fixture()
            data['8h']['models']['simple']['economics']['0.05'].update(change)
            self.save(data)
            with self.assertRaises(ValueError):
                self.review()

    def test_abstention_and_all_pending_policies_are_not_measured_zero_roi(self):
        for value in (
            {'bets': 0, 'stake': 0, 'profit': 0, 'roi': {'estimate': None, 'ci95': [None, None]}},
            {'bets': 2, 'wins': 0, 'losses': 0, 'pushes': 0, 'voids': 1, 'unresolved': 1,
             'stake': 0, 'profit': 0, 'matched_claimed_profit': 0, 'over_bets': 0,
             'roi': {'estimate': None, 'ci95': [None, None]}}):
            data = result_fixture()
            data['8h']['models']['simple']['economics']['0.05'] = value
            self.save(data)
            self.assertIn('Not measurable', helper.render_review(self.review()))

    def test_review_does_not_write_inputs_and_refuses_existing_output(self):
        self.save()
        before = (self.result.read_bytes(), self.receipt.read_bytes())
        self.review()
        self.assertEqual(before, (self.result.read_bytes(), self.receipt.read_bytes()))
        with self.assertRaises(FileExistsError):
            helper.emit('replace original', self.result)
        self.assertEqual(before[0], self.result.read_bytes())


class EvidenceTests(TemporaryFiles):
    def test_bundle_is_complete_checksumming_and_deterministic(self):
        (self.root/'a.csv').write_text('player,probability\nx,.5\n' * 100)
        (self.root/'b.json').write_text('{"verdict": "inconclusive"}\n')
        first, second = self.root/'first.tar.gz', self.root/'second.tar.gz'
        one = helper.pack_evidence(self.root, ['a.csv', 'b.json'], first)
        two = helper.pack_evidence(self.root, ['b.json', 'a.csv'], second)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(one['bundle_sha256'], two['bundle_sha256'])
        with tarfile.open(first) as archive:
            manifest = json.load(archive.extractfile('MANIFEST.json'))
            self.assertEqual(set(archive.getnames()), {'a.csv', 'b.json', 'MANIFEST.json'})
            for row in manifest['files']:
                data = archive.extractfile(row['path']).read()
                self.assertEqual(len(data), row['bytes'])
                self.assertEqual(hashlib.sha256(data).hexdigest(), row['sha256'])
                self.assertEqual(data, (self.root/row['path']).read_bytes())

    def test_existing_evidence_is_not_overwritten(self):
        (self.root/'a.csv').write_text('original')
        output = self.root/'a.tar.gz'
        output.write_bytes(b'previous published evidence')
        with self.assertRaises(FileExistsError):
            helper.pack_evidence(self.root, ['a.csv'], output)
        self.assertEqual((self.root/'a.csv').read_text(), 'original')
        self.assertEqual(output.read_bytes(), b'previous published evidence')

    def test_path_escape_symlinks_directories_and_duplicate_inputs_are_rejected(self):
        (self.root/'a.csv').write_text('x')
        (self.root/'link.csv').symlink_to(self.root/'a.csv')
        (self.root/'MANIFEST.json').write_text('{}')
        for files in (['../escape'], ['link.csv'], ['a.csv', 'a.csv'], ['.'], ['MANIFEST.json']):
            with self.subTest(files=files), self.assertRaises(ValueError):
                helper.pack_evidence(self.root, files, self.root/'bundle.tar.gz')
        self.assertFalse((self.root/'bundle.tar.gz').exists())


if __name__ == '__main__':
    unittest.main()
