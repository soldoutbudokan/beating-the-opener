"""Count-only Phase 0 audit. No prediction, accuracy, profit or model scoring.

Run from the repository root with pyarrow installed. Source outcome columns are
read only inside the isolated ESPN accrual proxy; only aggregate counts leave
that function. That proxy cannot unlock the registered wehoop evaluation.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import unicodedata

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from research.clocks import ET, FIELDS, parse, before

ROOT = Path(__file__).resolve().parents[1]
SOURCE_COMMIT = 'd2b5e685ce10d9670fc93bf8c22810bc8cc5df83'
PR2_COMMIT = '64acad8248ef6536ef7c655d733293f6763263cc'
MARKETS = {393: ['points'], 397: ['rebounds'], 391: ['assists'],
           390: ['three_point_field_goals_made'], 396: ['points', 'rebounds', 'assists'],
           394: ['points', 'assists'], 395: ['points', 'rebounds'], 398: ['rebounds', 'assists'],
           399: ['steals'], 392: ['blocks'], 401: ['turnovers'], 400: ['steals', 'blocks']}
ALIASES = {'PHO': 'PHX', 'WSH': 'WAS', 'NY': 'NYL', 'LA': 'LAS', 'LV': 'LVA',
           'GS': 'GSV', 'POR': 'PDX'}


def digest(data): return hashlib.sha256(data).hexdigest()
def team(value): return ALIASES.get(str(value).upper(), str(value).upper())
def name(value):
    s = unicodedata.normalize('NFKD', str(value)).encode('ascii', 'ignore').decode().lower()
    return re.sub('[^a-z]', '', s)
def utc(value): return value.isoformat().replace('+00:00', 'Z')


class Inputs:
    def __init__(self): self.files = {}

    def read(self, path):
        p = ROOT / path
        data = p.read_bytes()
        self.files[str(path)] = {'sha256': digest(data), 'bytes': len(data)}
        return data

    def json(self, path):
        data = self.read(path)
        return json.loads(gzip.decompress(data) if str(path).endswith('.gz') else data)


def opening(offer):
    sides = {s.get('selection'): s.get('opening_line') or {} for s in offer.get('selections', [])}
    o, u = sides.get('over', {}), sides.get('under', {})
    try:
        line, other = float(o['line']), float(u['line'])
        oc, uc = float(o['cost']), float(u['cost'])
        if abs(oc) < 100 or abs(uc) < 100: return None
        probability = lambda cost: 100 / (100 + cost) if cost > 0 else -cost / (100 - cost)
        if line != other or o['book_id'] != u['book_id'] or not 1 <= probability(oc) + probability(uc) <= 1.15:
            return None
        return line
    except (ValueError, TypeError, KeyError): return None


def espn_accrual_proxy(inputs, offers, events):
    """Outcome masks for a count only. Does not expose or score protected labels."""
    games = defaultdict(list)
    identity_tips = defaultdict(set)
    box_dates, captures, schema_counts = [], [], Counter()
    for path in sorted(ROOT.glob('wnba/data/raw/espn_box/*.json')):
        doc = inputs.json(path.relative_to(ROOT))
        box_dates.append(doc['date'])
        schema_counts[str(doc.get('schema', 1))] += 1
        if doc.get('captured_utc'): captures.append(utc(parse('espn.captured_utc', doc['captured_utc'])))
        for g in doc['games']:
            tip = parse('espn.utc_tip', g['utc_tip'])
            key = (doc['date'], frozenset(team(t) for t in re.split(r'\s*@\s*', g['short_name'])))
            games[key].append(g)
            for p in g.get('players', []):
                identity_tips[(doc['date'], name(p['athlete_display_name']))].add(tip)
    counts = Counter()
    per_date = Counter()
    for e, o, line in offers:
        key = (e['et_date'], frozenset([team(e['home']), team(e['visitor'])]))
        candidates = games.get(key, [])
        if len(candidates) != 1:
            counts['missing_or_ambiguous_game'] += 1
            continue
        player = (o.get('participants') or [{}])[0].get('name', '')
        rows = [p for p in candidates[0]['players'] if name(p['athlete_display_name']) == name(player)]
        if len(rows) != 1:
            counts['missing_or_ambiguous_player'] += 1
            continue
        p = rows[0]
        if p.get('did_not_play') or not p.get('minutes') or float(p['minutes']) <= 0:
            counts['void'] += 1
            continue
        values = [p.get(c) for c in MARKETS[int(o['market_id'])]]
        if any(v is None for v in values):
            counts['missing_stat'] += 1
            continue
        if sum(float(v) for v in values) == line:
            counts['push'] += 1
            continue
        counts['nonpush_played_proxy'] += 1
        per_date[e['et_date']] += 1
    cumulative, first = 0, None
    for day, n in sorted(per_date.items()):
        cumulative += n
        if cumulative >= 3000 and first is None: first = day
    return {'counts': dict(counts), 'by_date': dict(sorted(per_date.items())),
            'first_date_proxy_reaches_3000': first,
            'warning': 'ESPN exact-game count proxy, not the registered wehoop-qualified n; cannot unlock scoring.',
            'box_files': len(box_dates), 'box_date_min': min(box_dates), 'box_date_max': max(box_dates),
            'capture_min': min(captures), 'capture_max': max(captures),
            'schema_counts': dict(schema_counts)}, identity_tips


def audit():
    inputs = Inputs()
    archive = inputs.json('wnba/data/raw/bp/events_2026.json.gz')
    events = []
    for e in archive:
        tip = parse('bp.scheduled', e['scheduled'])
        day = tip.astimezone(ET).date().isoformat()
        if day >= '2026-08-01' and int(e.get('season', 0)) == 2026:
            events.append(dict(e, et_date=day))
    by_status = Counter(e['status'] for e in events)
    counts = Counter()
    offers, seen, source_books = [], set(), Counter()
    clock_counts, clock_errors = Counter(), []
    response_times = []
    identity_tips = defaultdict(set)
    def check(field, value, path):
        if value is None: return
        clock_counts[field] += 1
        try: parse(field, value)
        except (ValueError, TypeError, OverflowError) as error:
            clock_errors.append({'field': field, 'path': str(path), 'error': str(error)})
    for e in events:
        for market in MARKETS:
            path = Path(f"wnba/data/raw/bp/offers/{e['id']}_{market}.json.gz")
            if not (ROOT / path).exists():
                counts['missing_offer_files'] += 1
                continue
            d = inputs.json(path)
            counts['offer_files'] += 1
            if int(d.get('_pagination', {}).get('total_items', 0)) > len(d.get('offers', [])):
                counts['incomplete_offer_files'] += 1
            for field in ['utc', 'ts']:
                check('bp.' + field, d.get(field), path)
            if d.get('utc'): response_times.append(utc(parse('bp.utc', d['utc'])))
            for o in d.get('offers', []):
                if str(o['event_id']) != str(e['id']):
                    counts['event_identity_mismatch'] += 1
                    continue
                counts['offers'] += 1
                for s in o.get('selections', []):
                    check('bp.created', (s.get('opening_line') or {}).get('created'), path)
                    for b in s.get('books', []):
                        for ln in b.get('lines', []): check('bp.updated', ln.get('updated'), path)
                p = (o.get('participants') or [{}])[0].get('name', '')
                identity_tips[(e['et_date'], name(p))].add(parse('bp.scheduled', e['scheduled']))
                key = (str(e['id']), str(o.get('player_id')), int(o['market_id']))
                if key in seen:
                    counts['duplicate_offer_key'] += 1
                    continue
                seen.add(key)
                line = opening(o)
                if line is not None:
                    # An upper bound includes rows without confirmed outcomes/features.
                    offers.append((e, o, line))
                    counts['coherent_unique_openers_upper_bound'] += 1
                    over = next(s['opening_line'] for s in o['selections'] if s['selection'] == 'over')
                    source_books[str(over['book_id'])] += 1
    proxy, box_tips = espn_accrual_proxy(inputs, offers, events)
    for key, tips in box_tips.items(): identity_tips[key].update(tips)
    avail, injuries, lineup_rows = [], Counter(), 0
    for path in sorted(ROOT.glob('wnba/data/raw/avail/*/*.json')):
        rel = path.relative_to(ROOT)
        d = inputs.json(rel)
        captured = parse('espn.captured_utc', d['captured_utc'])
        expected = path.parent.name + ' ' + path.stem[:2] + ':' + path.stem[2:4]
        if parse('espn.captured_utc', expected) != captured:
            clock_errors.append({'field': 'espn.captured_utc', 'path': str(rel), 'error': 'filename disagrees'})
        check('espn.captured_utc', d['captured_utc'], rel)
        for e in d['events']: check('espn.event.date', e['date'], rel)
        injuries.update(str(i.get('status')) for i in d['injuries'])
        lineup_rows += len(d['lineups'])
        avail.append({'path': str(rel), 'captured_utc': utc(captured),
                      'injuries': len(d['injuries']), 'events': len(d['events']), 'lineups': len(d['lineups'])})
    overrides = inputs.json('wnba/live/projections_overrides.json')['entries']
    override_counts, timing_rows = Counter(), []
    for idx, e in enumerate(overrides):
        added = parse('override.added', e['added'])
        check('override.added', e['added'], 'override:' + str(idx))
        if e.get('game_date') is not None: parse('override.game_date', e['game_date'])
        override_counts['status:' + e['status']] += 1
        override_counts['until_cleared' if e.get('game_date') is None else 'event_dated'] += 1
        if e.get('game_date') and e['game_date'] < '2026-08-01': override_counts['before_prospective_window'] += 1
        if e.get('minutes_est') is not None: override_counts['minutes_est_present'] += 1
        if e.get('minutes_range') is not None: override_counts['minutes_range_present'] += 1
        if e.get('superseded_by') is not None: override_counts['superseded_pointer_present'] += 1
        candidates = identity_tips.get((e.get('game_date'), name(e['player'])), set())
        state = 'unresolved_event'
        if len(candidates) == 1:
            state = 'before_tip' if added < next(iter(candidates)) else 'at_or_after_tip'
        elif len(candidates) > 1: state = 'ambiguous_event'
        override_counts['time:' + state] += 1
        timing_rows.append({'entry_index': idx, 'player': e['player'], 'added': utc(added),
                            'game_date': e.get('game_date'), 'status': e['status'], 'timing_check': state,
                            'matched_tip_at': utc(next(iter(candidates))) if len(candidates) == 1 else None,
                            'identity_method': 'unique exact-date player name in BP offers or ESPN event roster; no neighboring dates'})
    seen_doc = inputs.json('wnba/live/news_seen.json')
    import pyarrow.parquet as pq
    market_path = Path('cricket/data/raw/polymarket/markets.parquet')
    price_path = Path('cricket/data/raw/polymarket/prices.parquet')
    inputs.read(market_path); inputs.read(price_path)
    # Do not load outcome_prices or p. Clock/identity columns only.
    mk = pq.read_table(ROOT / market_path, columns=['market_id', 'closed_time', 'game_start_label',
                        'start_date', 'end_date', 'accepting_orders_ts', 'closed']).to_pylist()
    pr = pq.read_table(ROOT / price_path, columns=['market_id', 't']).to_pandas()
    closures, cricket_counts = [], Counter()
    for row in mk:
        for field in ['closed_time', 'game_start_label', 'start_date', 'end_date', 'accepting_orders_ts']:
            check('pm.' + field, row[field], market_path)
        if row['closed_time']:
            moment = parse('pm.closed_time', row['closed_time']); closures.append(utc(moment))
            for lock in ['2026-08-29', '2026-08-30', '2026-09-02']:
                if moment.date().isoformat() > lock: cricket_counts['after_' + lock] += 1
    for value in (int(pr.t.min()), int(pr.t.max())): check('pm.t', value, price_path)
    gap = pr.sort_values(['market_id', 't']).groupby('market_id').t.diff()
    cricket = {'market_rows': len(mk), 'price_rows': len(pr), 'priced_markets': int(pr.market_id.nunique()),
               'closure_max': max(closures), 'closure_counts': dict(cricket_counts),
               'price_time_min': utc(parse('pm.t', int(pr.t.min()))),
               'price_time_max': utc(parse('pm.t', int(pr.t.max()))),
               'median_price_spacing_seconds': float(gap.median()),
               'duplicate_price_ids': int(pr.duplicated(['market_id', 't']).sum()),
               'resolution_warning': 'closed_time is a closure timestamp; no separately validated resolution time is archived.'}
    tracked = subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', SOURCE_COMMIT], cwd=ROOT, text=True).splitlines()
    artifact_paths = [p for p in tracked if any(t in p.lower() for t in ('prospective', 'calibration', 'talent.pkl', 'fp_preds', 'evaluation_receipt'))]
    document_paths = ['CLAUDE.md', 'PROGRESS.md', 'AUDIT.md', 'LESSONS.md', 'research/WORKFLOW.md',
                      'wnba/README.md', 'cricket/README.md', 'wnba/live/PROTOCOL.md']
    source_code = ['wnba/src/' + x for x in ['fp_model.py', 'fp_live.py', 'features.py', 'talent.py',
                   'grade_props.py', 'build_modelset.py', 'build_props.py', 'fp_games2.py', 'news_watch.py',
                   'avail_watch.py', 'fetch_espn_box.py']] + ['cricket/src/' + x for x in
                   ['pm_model2.py', 'pm_prospective.py', 'pm_benchmark.py', 'fetch_polymarket.py']]
    source_hashes = {}
    for path in document_paths + source_code:
        data = subprocess.check_output(['git', 'show', SOURCE_COMMIT + ':' + path], cwd=ROOT)
        source_hashes[path] = digest(data)
    for path in ['baseline.py', 'benchmark.py', 'features.py', 'sources.py', 'run.py']:
        full = 'wnba/research/rich_context/' + path
        source_hashes['pr2:' + full] = digest(subprocess.check_output(['git', 'show', PR2_COMMIT + ':' + full], cwd=ROOT))
    return {'schema_version': 'process-audit-counts-v1', 'source_commit': SOURCE_COMMIT,
            'pr2_commit': PR2_COMMIT, 'wnba': {'event_rows': len(events), 'event_statuses': dict(by_status),
             'event_date_min': min(e['et_date'] for e in events), 'event_date_max': max(e['et_date'] for e in events),
             'counts': dict(counts), 'opener_source_books': dict(source_books),
             'offer_response_time_min': min(response_times), 'offer_response_time_max': max(response_times),
             'espn_count_proxy': proxy},
            'availability': {'snapshots': len(avail), 'days': len({r['captured_utc'][:10] for r in avail}),
             'first': avail[0]['captured_utc'], 'last': avail[-1]['captured_utc'],
             'injury_status_rows': dict(injuries), 'lineup_rows': lineup_rows},
            'overrides': {'entries': len(overrides), 'first_added': min(e['added'] for e in overrides),
             'last_added': max(e['added'] for e in overrides), 'counts': dict(override_counts),
             'eligible_entries': 0, 'metrics': None, 'reason': 'No scored overlapping arm releases post-endpoint outcomes.'},
            'news': {'seen_ids': len(seen_doc['ids']), 'raw_payload_files': len(list(ROOT.glob('wnba/data/raw/news/**/*.*')))},
            'cricket': cricket, 'clocks': {'checked_values': dict(clock_counts), 'errors': clock_errors,
             'wehoop_schedule_dual_column_check': 'BLOCKED: pinned parquet inputs absent in this checkout'},
            'tracked_frozen_artifact_candidates': artifact_paths, 'source_code_and_document_hashes': source_hashes,
            'input_files': inputs.files, 'availability_rows': avail, 'override_timing_rows': timing_rows}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    if args.output.exists(): raise SystemExit('Refusing to overwrite existing audit output')
    result = audit()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as f: json.dump(result, f, indent=2, sort_keys=True, allow_nan=False); f.write('\n')
    print(json.dumps({k: result[k] for k in ['wnba', 'availability', 'overrides', 'news', 'cricket', 'clocks',
                                          'tracked_frozen_artifact_candidates']}, indent=2))


if __name__ == '__main__': main()
