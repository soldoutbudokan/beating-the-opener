"""Small, offline helpers for source pilots, research decisions and evidence bundles.

Uses only Python's standard library. Never fits models, fetches data or places bets.
See research/WORKFLOW.md for the process and input contracts.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import contextmanager
import csv
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
import statistics
import sys
import tarfile


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def finite(value, name):
    require(not isinstance(value, bool) and isinstance(value, (float, int)), f'{name} must be a number')
    require(math.isfinite(value), f'{name} must be finite')
    return float(value)


def count(value, name):
    require(type(value) is int and value >= 0, f'{name} must be a nonnegative integer')
    return value


@contextmanager
def new_output(path):
    """Exclusive creation: existing research evidence is never overwritten."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open('xb')
    try:
        with handle:
            yield handle
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def emit(value, path=None):
    text = value if isinstance(value, str) else json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n'
    if path:
        with new_output(path) as handle:
            handle.write(text.encode('utf-8'))
    else:
        print(text, end='')


def timestamp(value):
    moment = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    require(moment.tzinfo is not None and moment.utcoffset() is not None,
            'Timestamp must include a timezone')
    return moment.astimezone(timezone.utc)


def has_value(value):
    value = value.strip()
    if value.lower() in ('', 'null', 'none', 'nan', 'na', 'n/a'):
        return False
    try:
        return math.isfinite(float(value))
    except ValueError:
        return True  # Categorical information, such as an availability status.


def check_source(path, expected_rows, min_coverage, time_basis, time_note=''):
    """One row per requested query/family, including misses; no outcomes needed."""
    require(type(expected_rows) is int and expected_rows > 0, 'expected_rows must be positive')
    require(0 < finite(min_coverage, 'min_coverage') <= 1, 'min_coverage must be in (0, 1]')
    require(time_basis in ('observed', 'assumed'), 'Unknown time basis')
    require(time_basis != 'assumed' or time_note.strip(), 'Explain the assumed publication delay with --time-note')
    required = {'query_id', 'family', 'entity_id', 'event_id', 'source_id',
                'prediction_at', 'available_at', 'matched', 'value'}
    with Path(path).open(newline='', encoding='utf-8-sig') as handle:
        reader = csv.DictReader(handle)
        require(required <= set(reader.fieldnames or []), 'Missing source-pilot columns: ' + ', '.join(sorted(required - set(reader.fieldnames or []))))
        require(len(reader.fieldnames) == len(set(reader.fieldnames)), 'Duplicate CSV column names')
        rows = []
        for row in reader:
            require(None not in row, 'A source-pilot row has extra fields')
            rows.append({key: (value or '').strip() for key, value in row.items()})
    families = defaultdict(Counter)
    faults = Counter()
    ages = []
    identities = Counter((row.get('query_id'), row.get('family')) for row in rows)
    for row in rows:
        family = (row.get('family') or '').strip() or '(missing family)'
        cell = families[family]
        cell['requested'] += 1
        reasons = set()
        if any(not (row.get(key) or '').strip() for key in ('query_id', 'family', 'entity_id', 'event_id')):
            reasons.add('missing_identity')
        if identities[(row.get('query_id'), row.get('family'))] > 1:
            reasons.add('duplicate_query_family')
        matched = (row.get('matched') or '').strip().lower()
        if matched not in ('true', 'false', '1', '0'):
            reasons.add('invalid_match_flag')
        elif matched in ('false', '0'):
            reasons.add('unmatched')
        if matched in ('true', '1') and not (row.get('source_id') or '').strip():
            reasons.add('missing_source_id')
        if not has_value(row.get('value') or ''):
            reasons.add('missing_value')
        try:
            cutoff = timestamp(row.get('prediction_at') or '')
        except (ValueError, TypeError):
            cutoff = None
            reasons.add('invalid_prediction_time')
        available_text = (row.get('available_at') or '').strip()
        if not available_text:
            reasons.add('missing_available_time')
        else:
            try:
                available = timestamp(available_text)
                if cutoff is not None:
                    if available >= cutoff:
                        reasons.add('not_available_before_prediction')
                    elif matched in ('true', '1'):
                        ages.append((cutoff - available).total_seconds() / 3600)
            except (ValueError, TypeError):
                reasons.add('invalid_available_time')
        if not reasons:
            cell['usable'] += 1
        for reason in reasons:
            cell[reason] += 1
            faults[reason] += 1
    structural = ('missing_identity', 'duplicate_query_family', 'invalid_match_flag',
                  'missing_source_id', 'invalid_prediction_time', 'invalid_available_time',
                  'not_available_before_prediction')
    blocking = [name for name in structural if faults[name]]
    if len(rows) != expected_rows:
        blocking.append('requested_row_count_mismatch')
    for name, cell in families.items():
        cell['usable_fraction'] = cell['usable'] / cell['requested']
        if cell['usable_fraction'] < min_coverage:
            blocking.append('insufficient_coverage:' + name)
    if not families:
        blocking.append('empty_sample')
    return {
        'schema_version': 1, 'check': 'source pilot; not model evidence',
        'input_sha256': digest(path), 'expected_rows': expected_rows,
        'rows': len(rows), 'minimum_coverage_per_family': min_coverage,
        'time_basis': time_basis, 'time_note': time_note,
        'status': 'STOP' if blocking else ('PASS_UNDER_ASSUMPTION' if time_basis == 'assumed' else 'PASS_SAMPLE'),
        'blocking_reasons': blocking, 'families': dict(families),
        'fault_counts': dict(faults),
        'matched_prior_source_age_hours': {'median': statistics.median(ages), 'maximum': max(ages)} if ages else None,
        'limit': 'Checks the supplied sample and declared timestamps only. It does not verify the source clock, representative sampling, values or future predictive usefulness.',
    }


def interval(value, name):
    estimate = finite(value['estimate'], name + ' estimate')
    bounds = value['ci95']
    require(isinstance(bounds, list) and len(bounds) == 2, name + ' needs two interval endpoints')
    low, high = (finite(x, name + ' interval') for x in bounds)
    require(low <= high, name + ' interval endpoints are reversed')
    return estimate, low, high


def conclusion(estimate, low, high, minimum_gain=None):
    """All differences are candidate minus baseline; smaller is better."""
    if low > 0:
        return 'Worse forecasts on this comparison'
    if minimum_gain is not None and low > -minimum_gain:
        return 'The interval does not reach the stated useful gain'
    if high < 0:
        if minimum_gain is not None and high > -minimum_gain:
            return 'An improvement is visible, but its useful size remains uncertain'
        return 'An improvement is visible on this comparison'
    return 'No clear improvement; the interval includes no difference'


def review_results(path, receipt_path, comparison, evidence_kind, minimum_gain=None):
    """Adapter for the published rich-context result format; uses summaries only."""
    require(evidence_kind in ('development', 'reused-development', 'untouched-test'), 'Unknown evidence kind')
    if minimum_gain is not None:
        require(finite(minimum_gain, 'minimum_gain') > 0, 'minimum_gain must be positive')
    result = json.loads(Path(path).read_text())
    receipt = json.loads(Path(receipt_path).read_text())
    require(receipt.get('status') == 'complete', 'The evaluation receipt is not complete')
    fingerprint = digest(path)
    require(receipt.get('results_sha256') == fingerprint, 'Results differ from the completed evaluation receipt')
    require(comparison.count('_minus_') == 1, 'Comparison must be candidate_minus_baseline')
    candidate, baseline = comparison.split('_minus_')
    scenarios = {}
    for label, scenario in result.items():
        if not isinstance(scenario, dict) or 'models' not in scenario:
            continue
        quoted = count(scenario['quotes'], 'quotes')
        settled = count(scenario['settled_quotes'], 'settled_quotes')
        voids = count(scenario['void_quotes'], 'void_quotes')
        dates = count(scenario['dates'], 'dates')
        require(quoted >= settled + voids and settled > 0 and dates > 1, 'Invalid comparison population')
        models = scenario['models']
        require(candidate in models and baseline in models, 'Selected comparison models are missing')
        require(comparison in scenario['comparisons'], 'A paired uncertainty interval is required; separate scores are insufficient')
        paired = scenario['comparisons'][comparison]
        require(paired['n'] == settled and paired['dates'] == dates, 'Paired comparison uses a different population')
        point, low, high = interval(paired, comparison)
        expected = finite(models[candidate]['log_loss'], 'candidate log loss') - finite(models[baseline]['log_loss'], 'baseline log loss')
        require(math.isclose(point, expected, abs_tol=1e-9, rel_tol=0), 'Paired difference does not match the reported model scores')
        rows = []
        for name, model in models.items():
            loss = finite(model['log_loss'], name + ' log loss')
            require(loss >= 0, 'Log loss cannot be negative')
            rules = {}
            for rule, value in model['economics'].items():
                bets = count(value['bets'], 'bets')
                require(0 <= float(rule) <= 1 and math.isfinite(float(rule)), 'Invalid betting threshold')
                if bets == 0:
                    require(value['stake'] == 0 and value['profit'] == 0, 'A zero-bet policy cannot have stakes or profit')
                    require(value['roi']['estimate'] is None, 'A zero-bet policy has no measured return')
                    rules[rule] = {'bets': 0, 'voids': 0, 'unresolved': 0, 'stake': 0,
                                   'profit': 0, 'claimed_profit': 0, 'roi': None,
                                   'ci95': [None, None], 'under_fraction': None}
                    continue
                n_void = count(value['voids'], 'voids')
                unresolved = count(value['unresolved'], 'unresolved')
                wins, losses, pushes = (count(value[k], k) for k in ('wins', 'losses', 'pushes'))
                require(bets == wins + losses + pushes + n_void + unresolved, 'Bet counts do not reconcile')
                stake = finite(value['stake'], 'stake')
                profit = finite(value['profit'], 'profit')
                claimed = finite(value['matched_claimed_profit'], 'matched claimed profit')
                require(stake >= 0, 'Stake cannot be negative')
                if stake == 0:
                    require(profit == 0 and claimed == 0 and bets == n_void + unresolved,
                            'No settled stake but settled outcomes or profit are present')
                    require(value['roi']['estimate'] is None, 'Return is undefined without settled stake')
                    roi = roi_low = roi_high = None
                else:
                    roi, roi_low, roi_high = interval(value['roi'], name + ' ROI')
                    require(math.isclose(roi, profit / stake, abs_tol=1e-9, rel_tol=0), 'Return does not match profit divided by settled stake')
                overs = count(value['over_bets'], 'over_bets')
                require(overs <= bets, 'More over bets than bets')
                rules[rule] = {'bets': bets, 'voids': n_void, 'unresolved': unresolved,
                               'stake': stake, 'profit': profit, 'claimed_profit': claimed,
                               'roi': roi, 'ci95': [roi_low, roi_high],
                               'under_fraction': (bets - overs) / bets if bets else None}
            rows.append({'model': name, 'log_loss': loss, 'rules': rules})
        scenarios[label] = {
            'quotes': quoted, 'settled_quotes': settled, 'voids': voids,
            'unresolved_quotes': quoted - settled - voids, 'dates': dates,
            'opener_log_loss': finite(scenario['market']['log_loss'], 'opener log loss'),
            'difference': point, 'ci95': [low, high],
            'conclusion': conclusion(point, low, high, minimum_gain), 'models': rows,
            'native_incumbent_log_loss': scenario.get('native_incumbent_secondary_log_loss'),
        }
    require(scenarios, 'No supported result scenarios found')
    return {'schema_version': 1, 'candidate': candidate, 'baseline': baseline,
            'evidence_kind': evidence_kind, 'research_year': receipt.get('research_year'),
            'minimum_gain': minimum_gain, 'results_sha256': fingerprint,
            'receipt_sha256': digest(receipt_path), 'scenarios': scenarios,
            'verification_scope': 'Receipt checksum and summary arithmetic only; individual predictions and uncertainty intervals are not recomputed.',
            'decision_limit': 'This report cannot authorize model adoption or live betting. Test freshness and any useful-gain threshold must be established in the original plan, not declared after seeing results.'}


def review_process_audit(path, receipt_path):
    """Validate the explicit Phase 0 schema; never opens protected outcomes."""
    result = json.loads(Path(path).read_text())
    receipt = json.loads(Path(receipt_path).read_text())
    require(result['schema_version'] == 'process-audit-v1', 'Wrong process audit schema')
    require(receipt['status'] == 'complete', 'Audit receipt is incomplete')
    require(digest(path) == receipt['results_sha256'], 'Audit result checksum differs')
    root = Path(receipt_path).resolve().parent
    for row in receipt['evidence']:
        relative = Path(row['path'])
        require(not relative.is_absolute() and '..' not in relative.parts,
                'Evidence must stay within the audit directory')
        evidence = root / relative
        require(evidence.resolve().is_relative_to(root), 'Evidence escapes audit directory')
        require(digest(evidence) == row['sha256'], 'Audit evidence checksum differs: ' + row['path'])
    require(receipt['evidence'], 'Audit evidence manifest is empty')
    gates = result['gates']
    require({g['id'] for g in gates} == {'AUDIT', 'CLOCKS', 'WINDOWS', 'NEWS', 'RECEIPT'}
            and len(gates) == 5, 'Audit gates missing or duplicated')
    require(all(g['status'] in {'PASS', 'FAIL', 'BLOCKED'} and g['reason'] for g in gates),
            'Invalid audit gate status/reason')
    arms = result['protected_arms']
    expected = {'fp-prospective-1', 'fp-prospective-2', 'fp-games-prospective-1',
                'pm-prospective-1', 'pm-prospective-2'}
    require({a['arm'] for a in arms} == expected and len(arms) == 5, 'Missing/duplicate protected arm')
    for arm in arms:
        if arm['qualified_n'] is not None:
            count(arm['qualified_n'], 'qualified_n')
        count(arm['archive_upper_bound'], 'archive_upper_bound')
        if arm['qualified_n'] is not None:
            require(arm['qualified_n'] <= arm['archive_upper_bound'], 'Qualified count exceeds upper bound')
        require(arm['status'] in {'WAIT', 'BLOCKED', 'SCORED'}, 'Invalid protected arm status')
        require(bool(arm['endpoint']) and bool(arm['release_rule']), 'Missing endpoint/release rule')
        if arm['status'] != 'SCORED':
            require(arm.get('released_at') is None, 'Unscored arm cannot release outcomes')
        else:
            require(arm.get('evaluation_receipt'), 'Scored arm requires a one-time evaluation receipt')
            timestamp(arm['released_at'])
    news = result['news_baseline']
    for key in ('entries', 'eligible_entries', 'matched_entries'):
        count(news[key], key)
    require(news['matched_entries'] <= news['eligible_entries'] <= news['entries'], 'News counts disagree')
    if not news['eligible_entries']:
        require(news['metrics'] is None, 'Protected news outcomes cannot have metrics')
    require(result['prior_2025_uses'], 'Prior uses of 2025 must be disclosed')
    require(result['decision'] in {'stop', 'collect missing input', 'prepare independent test'},
            'Invalid audit decision')
    result = dict(result, results_sha256=digest(path), receipt_sha256=digest(receipt_path))
    return result


def render_process_audit(result):
    lines = ['# Phase 0 decision', '', '**Decision: ' + result['decision'] + '.** ' + result['reason'], '',
             '2025 has repeatedly informed development and is not an independent test.', '',
             '| Gate | Status | Finding |', '| --- | --- | --- |']
    lines += [f"| {cell(g['id'])} | {cell(g['status'])} | {cell(g['reason'])} |" for g in result['gates']]
    lines += ['', '| Protected arm | Qualified n | Archive upper bound | Status |', '| --- | ---: | ---: | --- |']
    for arm in result['protected_arms']:
        n = 'unknown' if arm['qualified_n'] is None else str(arm['qualified_n'])
        lines.append(f"| {cell(arm['arm'])} | {n} | {arm['archive_upper_bound']} | {arm['status']} |")
    news = result['news_baseline']
    lines += ['', f"Human baseline: {news['entries']} logged entries, {news['eligible_entries']} unlocked, "
              f"{news['matched_entries']} matched for scoring. {news['reason']}", '',
              '## Prior uses of 2025', '']
    lines += ['- ' + cell(item) for item in result['prior_2025_uses']]
    lines += ['', '## Verification limits', '',
              'This adapter checks the audit schema, gate coverage, protected-release bookkeeping, '
              'count arithmetic and saved evidence checksums. It does not independently establish '
              'recipe fidelity, timestamp truth, archive completeness or historical model validity.', '',
              f"Results SHA-256: `{result['results_sha256']}`", '',
              f"Receipt SHA-256: `{result['receipt_sha256']}`", '']
    return '\n'.join(lines)


def cell(value):
    return str(value).replace('|', '/').replace('\n', ' ').replace('<', '&lt;')


def model_label(name):
    return cell({'box_ridge': 'Box-score regression',
                 'rich_ridge': 'Regression with richer data',
                 'rich_boost': 'Tree model with richer data',
                 'incumbent': 'Existing formula, common distribution'}.get(name, name))


def render_review(review):
    kind = {'development': 'development data', 'reused-development': 'previously used development data',
            'untouched-test': 'declared untouched test data'}[review['evidence_kind']]
    lines = ['# Research decision brief', '',
             f"Question: does **{model_label(review['candidate'])}** improve forecasts over **{model_label(review['baseline'])}**?", '',
             f"Evidence: **{review['research_year']} / {kind}** (declared by the caller). This brief reads a completed result; it is not a new backtest.", '']
    if review['evidence_kind'] != 'untouched-test':
        lines += ['These development results cannot establish an independent betting edge.', '']
    if review['minimum_gain'] is None:
        lines += ['No minimum useful improvement was supplied. Do not invent one after seeing the result.', '']
    else:
        lines += [f"Minimum useful log-loss improvement: {review['minimum_gain']:.6f}. This is only a decision rule if it was fixed in the original plan; otherwise it is a retrospective sensitivity.", '']
    for label, scenario in sorted(review['scenarios'].items(), key=lambda item: (item[0] != '8h', item[0])):
        lo, hi = scenario['ci95']
        lines += [f"## {cell(label)} information delay: {scenario['conclusion']}", '',
                  f"{scenario['settled_quotes']:,} played outcomes; {scenario['voids']:,} void quotes; {scenario['unresolved_quotes']:,} unresolved quotes; {scenario['dates']} dates.", '',
                  f"Candidate minus baseline: **{scenario['difference']:+.6f}**, with a 95% interval of **{lo:+.6f} to {hi:+.6f}**. Negative favors the candidate. Lower log loss means better probabilities, not higher returns.", '',
                  '| Forecast | Log loss |', '| --- | ---: |',
                  f"| Opening market | {scenario['opener_log_loss']:.6f} |"]
        ordered = sorted(scenario['models'], key=lambda m: m['model'])
        lines += [f"| {model_label(m['model'])} | {m['log_loss']:.6f} |" for m in ordered]
        if scenario['native_incumbent_log_loss'] is not None:
            native = finite(scenario['native_incumbent_log_loss'], 'native log loss')
            lines += ['', f"The corrected incumbent's original distribution scores {native:.6f}; its common-distribution baseline is a different comparison, not an exact deployed-model replay."]
        lines += ['', 'Every stored betting rule is shown; the most profitable rule does not choose the model. The percentage beside each model is the minimum profit per dollar it claimed before selecting a bet. Returns are simulated.', '',
                  '| Model / selection rule | Settled stake | Actual profit | Claimed profit on those bets | Return [95% interval] | Unders | Pending |',
                  '| --- | ---: | ---: | ---: | --- | ---: | ---: |']
        for model in ordered:
            for rule, value in sorted(model['rules'].items(), key=lambda pair: float(pair[0])):
                low, high = value['ci95']
                roi_text = 'Not measurable' if value['roi'] is None else f"{value['roi']:+.2%} [{low:+.2%}, {high:+.2%}]"
                under_text = '—' if value['under_fraction'] is None else f"{value['under_fraction']:.1%}"
                lines.append(f"| {model_label(model['model'])} / {float(rule):.0%} | ${value['stake']:,.0f} | ${value['profit']:+.2f} | ${value['claimed_profit']:+.2f} | {roi_text} | {under_text} | {value['unresolved']} |")
        lines.append('')
    lines += ['## Decision to record', '',
              'Record **stop**, **collect missing information**, or **prepare a separately specified independent test**, with a reason. A completed run or positive simulated return does not itself justify a model change.', '',
              'Before choosing the next experiment, inspect the largest probability errors, the gap between claimed and actual profit, and whether the needed information was available before the prediction. These summaries identify questions; they do not establish the causes of errors.', '',
              '## What was checked', '', review['verification_scope'], '',
              f"Results SHA-256: `{review['results_sha256']}`", '',
              f"Receipt SHA-256: `{review['receipt_sha256']}`", '',
              review['decision_limit'], '']
    return '\n'.join(lines)


class HashedReader:
    def __init__(self, handle):
        self.handle, self.hash = handle, hashlib.sha256()

    def read(self, size=-1):
        value = self.handle.read(size)
        self.hash.update(value)
        return value


def pack_evidence(root, paths, output):
    """Deterministic archive of explicitly named files; no automatic data sweep."""
    root = Path(root).resolve()
    output = Path(output).absolute()
    records = []
    for value in paths:
        original = Path(value)
        path = original.absolute() if original.is_absolute() else root / original
        relative = path.relative_to(root)
        require('..' not in relative.parts, 'Evidence paths cannot contain ..')
        require(not any(part.startswith('.') for part in relative.parts), 'Hidden files are not evidence inputs')
        cursor = root
        for part in relative.parts:
            cursor /= part
            require(not cursor.is_symlink(), 'Evidence symlinks are not supported')
        require(path.is_file(), 'List files explicitly; directories are not accepted: ' + str(value))
        require(path.resolve() != output.resolve(), 'Output cannot be an input')
        require(relative.as_posix() != 'MANIFEST.json', 'MANIFEST.json is reserved for the bundle inventory')
        records.append({'path': relative.as_posix(), 'bytes': path.stat().st_size, 'sha256': digest(path)})
    require(records, 'At least one evidence file is required')
    require(len({r['path'] for r in records}) == len(records), 'Duplicate evidence path')
    records.sort(key=lambda r: r['path'])
    manifest = json.dumps({'schema_version': 1, 'files': records}, indent=2, sort_keys=True).encode() + b'\n'
    with new_output(output) as handle:
        with gzip.GzipFile(fileobj=handle, mode='wb', filename='', mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode='w|', format=tarfile.PAX_FORMAT) as archive:
                for row in records + [{'path': 'MANIFEST.json', 'bytes': len(manifest)}]:
                    entry = tarfile.TarInfo(row['path'])
                    entry.size, entry.mode, entry.mtime = row['bytes'], 0o644, 0
                    if row['path'] == 'MANIFEST.json':
                        archive.addfile(entry, io.BytesIO(manifest))
                    else:
                        with (root / row['path']).open('rb') as source:
                            checked = HashedReader(source)
                            archive.addfile(entry, checked)
                            require(checked.hash.hexdigest() == row['sha256'] and not source.read(1), 'Input changed during packaging: ' + row['path'])
    return {'files': len(records), 'input_bytes': sum(r['bytes'] for r in records),
            'bundle_bytes': output.stat().st_size, 'bundle_sha256': digest(output)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    source = commands.add_parser('source-check', help='Check a small normalized source sample before fitting')
    source.add_argument('csv', type=Path)
    source.add_argument('--expected-rows', type=int, required=True)
    source.add_argument('--min-coverage', type=float, required=True)
    source.add_argument('--time-basis', choices=('observed', 'assumed'), required=True)
    source.add_argument('--time-note', default='')
    source.add_argument('--output', type=Path)
    review = commands.add_parser('review', help='Make a brief from the completed rich-context JSON format')
    review.add_argument('--results', type=Path, required=True)
    review.add_argument('--receipt', type=Path, required=True)
    review.add_argument('--adapter', choices=('rich-context', 'process-audit-v1'), default='rich-context')
    review.add_argument('--comparison')
    review.add_argument('--evidence-kind', choices=('development', 'reused-development', 'untouched-test'), required=True)
    review.add_argument('--minimum-gain', type=float)
    review.add_argument('--json', action='store_true')
    review.add_argument('--output', type=Path)
    pack = commands.add_parser('pack', help='Bundle explicitly named evidence files with checksums')
    pack.add_argument('files', nargs='+')
    pack.add_argument('--root', type=Path, default=Path.cwd())
    pack.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'source-check':
            result = check_source(args.csv, args.expected_rows, args.min_coverage, args.time_basis, args.time_note)
            emit(result, args.output)
            return 2 if result['status'] == 'STOP' else 0
        if args.command == 'review':
            if args.adapter == 'process-audit-v1':
                require(args.evidence_kind == 'reused-development', 'Phase 0 is reused development evidence')
                require(args.comparison is None and args.minimum_gain is None, 'Phase 0 does not compare models')
                result = review_process_audit(args.results, args.receipt)
                emit(result if args.json else render_process_audit(result), args.output)
            else:
                require(args.comparison is not None, '--comparison is required for rich-context')
                result = review_results(args.results, args.receipt, args.comparison, args.evidence_kind, args.minimum_gain)
                emit(result if args.json else render_review(result), args.output)
        if args.command == 'pack':
            emit(pack_evidence(args.root, args.files, args.output))
        return 0
    except (OSError, ValueError, KeyError, TypeError, csv.Error, tarfile.TarError) as error:
        parser.exit(2, f'Research helper stopped: {error}\n')


if __name__ == '__main__':
    raise SystemExit(main())
