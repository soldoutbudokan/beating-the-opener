"""Structural experiment decision adapter, registered before empirical results."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

GATES = {'TIMING', 'POPULATION', 'CALIBRATION', 'COUNT', 'OPENER', 'CLOSE', 'RECEIPT'}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def checked_files(root, records):
    require(isinstance(records, list) and bool(records), 'Empty file manifest')
    seen = set()
    for record in records:
        relative = Path(record['path'])
        require(not relative.is_absolute() and '..' not in relative.parts, 'Unsafe manifest path')
        require(relative.as_posix() not in seen, 'Duplicate manifest path')
        seen.add(relative.as_posix())
        path = root / relative
        require(path.resolve().is_relative_to(root.resolve()), 'Manifest escapes root')
        require(sha256(path) == record['sha256'], 'Checksum differs: ' + str(relative))
    return seen


def review_structural(results_path, receipt_path):
    results_path, receipt_path = Path(results_path), Path(receipt_path)
    result, receipt = json.loads(results_path.read_text()), json.loads(receipt_path.read_text())
    require(result['schema_version'] == 'structural-v1', 'Unsupported structural schema')
    require(receipt['schema_version'] == 'structural-receipt-v1', 'Unsupported receipt schema')
    require(receipt['status'] == 'complete', 'Incomplete evaluation receipt')
    require(receipt['results_sha256'] == sha256(results_path), 'Result checksum differs')
    require(receipt['evidence_kind'] == result['evidence_kind'] == 'reused-development', '2025 is reused development')
    require(result['research_year'] == 2025, 'Protected periods cannot enter this experiment')
    require(result['protected_arms_scored'] == 0, 'This experiment cannot score protected arms')
    require(result['prior_2025_uses'], 'Prior 2025 uses must be disclosed')
    require(len(receipt['registration_commit']) == 40, 'Missing pushed registration commit')
    artifacts = checked_files(receipt_path.resolve().parent, receipt['artifacts'])
    checked_files(Path(__file__).resolve().parents[2], receipt['implementation'])
    gates = result['gates']
    require(len(gates) == len(GATES) and {g['id'] for g in gates} == GATES, 'Missing or duplicate gates')
    require(all(g['status'] in {'PASS', 'FAIL', 'BLOCKED', 'NOT_RUN'} and g['reason'] for g in gates), 'Invalid gate')
    require(result['status'] in {'PASS', 'FAIL', 'BLOCKED'}, 'Invalid experiment status')
    require(result['decision'] in {'stop', 'collect missing input', 'prepare independent test'}, 'Invalid decision')
    require(result['attempt'] in (0, 1, 2), 'Attempt budget exceeded')
    if result['status'] == 'PASS':
        require(all(g['status'] == 'PASS' for g in gates), 'Cannot pass with failed or unrun gates')
    if result['status'] == 'BLOCKED':
        require(any(g['status'] == 'BLOCKED' for g in gates), 'Blocked result needs a named blocked gate')
        require(result['decision'] == 'collect missing input', 'Blocked experiment must name missing inputs')
    if result['decision'] == 'prepare independent test':
        require(result['status'] == 'PASS', 'Independent-test recommendation requires all gates')
    if result.get('models'):
        require('forecasts.jsonl.gz' in artifacts and 'recipe.json' in artifacts, 'Missing saved forecasts or recipe')
        require(result['attempt'] > 0, 'Model evaluation consumes an attempt')
    return dict(result, results_sha256=sha256(results_path), receipt_sha256=sha256(receipt_path))


def render_structural(result):
    lines = ['# Structural WNBA decision', '', '**Decision: ' + result['decision'] + '.** ' + result['reason'], '',
             '2025 has repeatedly informed model selection and calibration. These results are reused development, not an independent test.', '',
             '| Gate | Status | Finding |', '| --- | --- | --- |']
    clean = lambda value: str(value).replace('|', '/').replace('\n', ' ')
    lines += [f"| {g['id']} | {g['status']} | {clean(g['reason'])} |" for g in result['gates']]
    if result.get('summary'):
        lines += ['', result['summary']]
    lines += ['', '## Prior uses of 2025', '']
    lines += ['- ' + clean(x) for x in result['prior_2025_uses']]
    lines += ['', 'The saved-row verifier recomputes scores and betting arithmetic separately. Passing software tests is not evidence of forecast accuracy. No live model change or protected-window release follows from this report.', '',
              f"Results SHA-256: `{result['results_sha256']}`", f"Receipt SHA-256: `{result['receipt_sha256']}`", '']
    return '\n'.join(lines)
