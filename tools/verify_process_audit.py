"""Verify saved audit counts and optionally reproduce them in a new directory."""
from __future__ import annotations
from collections import Counter
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.research import review_process_audit


def verify(root, reproduce=None):
    result = review_process_audit(root / 'results.json', root / 'receipt.json')
    saved = json.loads(gzip.decompress((root / 'counts.json.gz').read_bytes()))
    rows = saved['override_timing_rows']
    assert len(rows) == result['news_baseline']['entries'] == saved['overrides']['entries']
    assert result['news_baseline']['eligible_entries'] == saved['overrides']['eligible_entries'] == 0
    assert result['news_baseline']['matched_entries'] == 0
    assert result['news_baseline']['metrics'] is None
    timing = Counter(r['timing_check'] for r in rows)
    for key, value in timing.items(): assert saved['overrides']['counts']['time:' + key] == value
    for r in rows:
        if r['matched_tip_at'] is not None:
            # UTC ISO strings have a common canonical format here.
            assert (r['added'] < r['matched_tip_at']) == (r['timing_check'] == 'before_tip')
    avail = saved['availability_rows']
    assert len(avail) == saved['availability']['snapshots']
    assert sum(r['injuries'] for r in avail) == sum(saved['availability']['injury_status_rows'].values())
    assert sum(r['lineups'] for r in avail) == saved['availability']['lineup_rows']
    proxy = saved['wnba']['espn_count_proxy']
    assert sum(proxy['counts'].values()) == saved['wnba']['counts']['coherent_unique_openers_upper_bound']
    assert sum(proxy['by_date'].values()) == proxy['counts']['nonpush_played_proxy']
    assert sum(saved['wnba']['opener_source_books'].values()) == saved['wnba']['counts']['coherent_unique_openers_upper_bound']
    for arm in result['protected_arms']:
        assert arm['status'] != 'SCORED', 'This published audit scores no protected arm'
        if arm['arm'].startswith('fp-prospective'):
            assert arm['archive_upper_bound'] == saved['wnba']['counts']['coherent_unique_openers_upper_bound']
            assert arm['qualified_n'] is None
        elif arm['arm'].startswith('pm-'):
            assert arm['archive_upper_bound'] == arm['qualified_n'] == 0
        else:
            assert arm['archive_upper_bound'] == saved['wnba']['event_statuses']['closed']
    for path, value in saved['input_files'].items():
        raw = (ROOT / path).read_bytes()
        assert len(raw) == value['bytes'] and hashlib.sha256(raw).hexdigest() == value['sha256'], path
    if reproduce is not None:
        if reproduce.exists(): raise ValueError('Reproduction directory already exists; refusing overwrite')
        reproduce.mkdir(parents=True)
        output = reproduce / 'counts.json'
        subprocess.run([sys.executable, str(ROOT / 'tools/process_audit.py'), '--output', str(output)],
                       cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
        actual = json.loads(output.read_text())
        assert actual == saved, 'Independent source recount differs from saved evidence'
    return {'status': 'PASS', 'protected_arms_scored': 0, 'source_files_verified': len(saved['input_files']),
            'count_reproduction': reproduce is not None,
            'scope': 'Counts, timestamp classifications, hashes and protected bookkeeping; no accuracy/ROI metrics exist.'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--audit-dir', type=Path, default=ROOT / 'research/audits/2026-09-process-audit')
    p.add_argument('--reproduce', type=Path)
    a = p.parse_args()
    print(json.dumps(verify(a.audit_dir, a.reproduce), indent=2))


if __name__ == '__main__': main()
