import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from tools.research import review_process_audit

ROOT = Path(__file__).resolve().parents[1]


class AuditReview(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.result = {'schema_version': 'process-audit-v1', 'decision': 'collect missing input',
                       'reason': 'Missing frozen evidence', 'prior_2025_uses': ['T1 development'],
                       'gates': [{'id': g, 'status': 'BLOCKED', 'reason': 'test'}
                                 for g in ['AUDIT', 'CLOCKS', 'WINDOWS', 'NEWS', 'RECEIPT']],
                       'protected_arms': [dict(arm=a, qualified_n=0, archive_upper_bound=0,
                             status='WAIT', endpoint='registered endpoint', release_rule='receipt required',
                             released_at=None) for a in ['fp-prospective-1', 'fp-prospective-2',
                             'fp-games-prospective-1', 'pm-prospective-1', 'pm-prospective-2']],
                       'news_baseline': {'entries': 554, 'eligible_entries': 0, 'matched_entries': 0,
                                         'metrics': None, 'reason': 'protected'}}
        (self.root / 'evidence.txt').write_text('count-only evidence')
        self.receipt = {'status': 'complete', 'evidence': [{'path': 'evidence.txt',
                        'sha256': hashlib.sha256((self.root / 'evidence.txt').read_bytes()).hexdigest()}],
                        'implementation': [{'path': 'tools/research.py',
                        'sha256': hashlib.sha256((ROOT / 'tools/research.py').read_bytes()).hexdigest()}]}

    def write(self):
        path = self.root / 'results.json'
        path.write_text(json.dumps(self.result))
        self.receipt['results_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        (self.root / 'receipt.json').write_text(json.dumps(self.receipt))

    def read(self): return review_process_audit(self.root / 'results.json', self.root / 'receipt.json')

    def test_valid_blocked_audit(self):
        self.write()
        self.assertEqual(self.read()['decision'], 'collect missing input')

    def test_tampered_evidence_and_code(self):
        self.write()
        (self.root / 'evidence.txt').write_text('changed')
        with self.assertRaises(ValueError): self.read()
        self.receipt['evidence'][0]['sha256'] = hashlib.sha256(b'changed').hexdigest()
        self.receipt['implementation'][0]['sha256'] = '0' * 64
        self.write()
        with self.assertRaises(ValueError): self.read()

    def test_unscored_release_and_protected_metrics_rejected(self):
        self.result['protected_arms'][0]['released_at'] = '2026-09-07T00:00:00Z'
        self.write()
        with self.assertRaises(ValueError): self.read()
        self.result['protected_arms'][0]['released_at'] = None
        self.result['news_baseline']['metrics'] = {'mae': 1.0}
        self.write()
        with self.assertRaises(ValueError): self.read()

    def test_duplicate_arms_and_false_scoring_rejected(self):
        arm = self.result['protected_arms'][0]
        self.result['protected_arms'][-1] = copy.deepcopy(arm)
        self.write()
        with self.assertRaises(ValueError): self.read()
        self.result['protected_arms'][-1]['arm'] = 'pm-prospective-2'
        arm.update(status='SCORED', evaluation_receipt='invented.json', released_at='2026-09-07T00:00:00Z')
        self.write()
        with self.assertRaises(ValueError): self.read()

    def test_counts_and_path_boundaries(self):
        self.result['news_baseline']['matched_entries'] = 1
        self.write()
        with self.assertRaises(ValueError): self.read()
        self.result['news_baseline']['matched_entries'] = 0
        self.receipt['evidence'][0]['path'] = '../outside.txt'
        self.write()
        with self.assertRaises(ValueError): self.read()


if __name__ == '__main__': unittest.main()
