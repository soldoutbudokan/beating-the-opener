"""Failure-mode and preservation tests for scheduled source collection."""
import copy
import datetime as dt
import gzip
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from research.operations import collection as c

NOW = dt.datetime(2026, 9, 7, 12, tzinfo=dt.timezone.utc)
REPO = Path(__file__).resolve().parents[1]


def offer(book=10, event=1, market=393, player=2, line=12.5):
    return {"id": str(player), "event_id": event, "market_id": market,
            "player_id": player, "active": True, "selections": [
                {"selection": side, "active": True, "books": [{"id": book, "lines": [
                    {"main": True, "active": True, "is_off": False, "cost": -110,
                     "line": line, "updated": c.stamp(NOW)}]}]} for side in ("over", "under")]}


def offers_page(rows, page=1, pages=1, total=None):
    return {"offers": rows, "_pagination": {"page": page, "total_pages": pages,
            "total_items": len(rows) if total is None else total}}


def market(identity="1", closed=False, age=0):
    return {"id": identity, "closed": closed, "question": "Alpha v Beta", "outcomes": '["Alpha","Beta"]',
            "clobTokenIds": '["123"]', "startDate": c.stamp(NOW-dt.timedelta(days=3)),
            "acceptingOrdersTimestamp": c.stamp(NOW-dt.timedelta(days=3)),
            "closedTime": c.stamp(NOW-dt.timedelta(days=age)) if closed else None,
            "umaResolutionStatus": "resolved" if closed else None}


class Response:
    def __init__(self, body, status=200):
        self.body, self.status = io.BytesIO(body), status
        self.headers = {"Date": "Mon, 07 Sep 2026 12:00:00 GMT", "Set-Cookie": "secret-cookie"}
    def read1(self, size):
        return self.body.read(size)
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass


class FakeRecorder:
    def __init__(self, directory, responses):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.responses = iter(responses)
        self.count = 0
        self.last_received = c.stamp(NOW)
        self.calls = []
    def get(self, url, params, validator, headers=None):
        self.count += 1
        self.calls.append((url, dict(params)))
        data = next(self.responses)
        if isinstance(data, Exception):
            raise data
        validator(data)
        return data


class RecorderTests(unittest.TestCase):
    def test_exact_bytes_preserved_before_parse_and_retry(self):
        raw = b'{"history": [{"t": 123, "p": 0.25}]}\n\t'
        failed = urllib.error.HTTPError('url', 503, 'bad', {}, io.BytesIO(b'upstream unavailable'))
        with tempfile.TemporaryDirectory() as tmp:
            opener = unittest.mock.Mock(side_effect=[failed, Response(raw)])
            r = c.Recorder(tmp, opener=opener, clock=lambda: NOW, sleeper=lambda _: None)
            self.assertEqual(r.get(c.CLOB+'/prices-history', {'market': '123'}, c.validate_history)['history'][0]['p'], .25)
            records = [json.loads(x) for x in (Path(tmp)/'requests.jsonl').read_text().splitlines()]
            self.assertEqual([x['status'] for x in records], ['FAILED', 'VALIDATED'])
            self.assertEqual(gzip.decompress((Path(tmp)/records[1]['body_file']).read_bytes()), raw)
            self.assertNotIn('set-cookie', records[1]['response_headers'])
            self.assertNotIn('headers', records[1])
            self.assertEqual(records[1]['requested_at'], c.stamp(NOW))
            self.assertEqual(records[1]['received_at'], c.stamp(NOW))

    def test_http200_malformed_is_failed_and_retained(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = c.Recorder(tmp, opener=lambda *a, **k: Response(b'{}'), sleeper=lambda _: None, tries=1)
            with self.assertRaisesRegex(c.CollectionError, 'SCHEMA_HISTORY'):
                r.get(c.CLOB+'/prices-history', {}, c.validate_history)
            self.assertEqual(gzip.decompress(next(Path(tmp).glob('responses/*')).read_bytes()), b'{}')

    def test_errors_do_not_log_exception_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = c.Recorder(tmp, opener=unittest.mock.Mock(side_effect=ValueError('private-secret')),
                           sleeper=lambda _: None, tries=1)
            with self.assertRaises(c.CollectionError):
                r.get(c.BP+'/events', {}, c.validate_bp_events, {'x-api-key': 'private-secret'})
            self.assertNotIn('private-secret', (Path(tmp)/'requests.jsonl').read_text())

    def test_request_budget_and_disallowed_query(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = c.Recorder(tmp, max_requests=0)
            with self.assertRaisesRegex(c.CollectionError, 'BUDGET'):
                r.get(c.CLOB+'/prices-history', {}, c.validate_history)
            with self.assertRaisesRegex(c.CollectionError, 'CREDENTIAL'):
                r.get(c.BP+'/events', {'api_key': 'secret'}, c.validate_bp_events)

    def test_streaming_deadline_preserves_partial_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = c.Recorder(tmp, opener=lambda *a, **k: Response(b'partial'), sleeper=lambda _: None, tries=1)
            r.deadline = 1
            with patch.object(c.time, 'monotonic', side_effect=[0, 0, 0, 2]), self.assertRaisesRegex(c.CollectionError, 'DEADLINE'):
                r.get(c.BP+'/events', {}, c.validate_bp_events)
            record = json.loads((Path(tmp)/'requests.jsonl').read_text())
            self.assertFalse(record['body_complete'])
            self.assertEqual(gzip.decompress((Path(tmp)/record['body_file']).read_bytes()), b'partial')


class CoverageTests(unittest.TestCase):
    def test_fanduel_and_fanatics_pairs(self):
        result = c.quote_coverage([offer(), offer(book=14)], NOW)
        self.assertEqual(result['any_book_coherent_pairs'], 1)
        for book, name in [('10', 'FanDuel'), ('14', 'Fanatics')]:
            self.assertEqual(result['books'][book]['name'], name)
            self.assertEqual(result['books'][book]['fresh_pairs'], 1)

    def test_mixed_books_lines_bad_vig_and_duplicate_main_do_not_form_pair(self):
        for kind in ['book', 'line', 'vig', 'duplicate']:
            row = offer()
            under = row['selections'][1]['books'][0]
            if kind == 'book': under['id'] = 14
            if kind == 'line': under['lines'][0]['line'] = 13.5
            if kind == 'vig': under['lines'][0]['cost'] = 1000
            if kind == 'duplicate': under['lines'].append(dict(under['lines'][0], cost=-120))
            self.assertEqual(c.quote_coverage([row], NOW)['any_book_coherent_pairs'], 0, kind)

    def test_provider_clock_missing_stale_future_not_fresh(self):
        for value, field in [(None, 'missing_quote_clock_pairs'), (c.stamp(NOW-dt.timedelta(hours=3)), 'stale_pairs'),
                             (c.stamp(NOW+dt.timedelta(minutes=5)), 'stale_pairs')]:
            row = offer()
            row['selections'][0]['books'][0]['lines'][0]['updated'] = value
            result = c.quote_coverage([row], NOW)['books']['10']
            self.assertEqual(result['coherent_pairs'], 1)
            self.assertEqual(result['fresh_pairs'], 0)
            self.assertEqual(result[field], 1)

    def test_all_offer_pages_and_identity_checked(self):
        responses = [{'events': [{'id': 1, 'scheduled': c.stamp(NOW+dt.timedelta(hours=5)), 'status': 'scheduled'}]}]
        for _, mid in c.MARKETS.items():
            responses += [offers_page([offer(market=mid)], pages=2, total=2),
                          offers_page([offer(market=mid, player=3)], page=2, pages=2, total=2)]
        with tempfile.TemporaryDirectory() as tmp:
            recorder = FakeRecorder(tmp, responses)
            result = c.collect_bettingpros(REPO, recorder, NOW)
            self.assertEqual(result['status'], 'OK')
            self.assertEqual(recorder.count, 17)
            self.assertTrue(all(x['offers'] == 2 for x in result['markets'].values()))
        with self.assertRaisesRegex(c.CollectionError, 'MISMATCH'):
            c.validate_bp_offers(offers_page([offer()]), event_id=999, market_id=393)

    def test_empty_slate_distinct_from_zero_offers(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = c.collect_bettingpros(REPO, FakeRecorder(Path(tmp)/'a', [{'events': []}]), NOW)
            self.assertEqual(result['status'], 'NO_EVENTS')
            responses = [{'events': [{'id': 1, 'scheduled': c.stamp(NOW+dt.timedelta(hours=5))}]}]
            responses += [offers_page([]) for _ in c.MARKETS]
            result = c.collect_bettingpros(REPO, FakeRecorder(Path(tmp)/'b', responses), NOW)
            self.assertEqual(result['status'], 'FAILED')
            self.assertTrue(result['zero_data_failure'])

    def test_duplicate_pagination_and_incomplete_page_rejected(self):
        for second in [offers_page([offer()], page=2, pages=2, total=2), offers_page([], page=2, pages=2, total=2)]:
            responses = [{'events': [{'id': 1, 'scheduled': c.stamp(NOW+dt.timedelta(hours=1))}]}]
            responses += [offers_page([offer()], pages=2, total=2), second]
            with tempfile.TemporaryDirectory() as tmp, self.assertRaises(c.CollectionError):
                c.collect_bettingpros(REPO, FakeRecorder(tmp, responses), NOW)


class PolymarketTests(unittest.TestCase):
    def test_old_backfill_cannot_hide_empty_terminal_interval(self):
        closed = market('1', closed=True, age=1)
        closed['startDate'] = closed['acceptingOrdersTimestamp'] = c.stamp(NOW-dt.timedelta(days=20))
        first = [{'events': []}, {'events': [{'id': 1, 'markets': [closed]}]}, {'history': []},
                 {'history': [{'t': int((NOW-dt.timedelta(days=19)).timestamp()), 'p': .5}]}]
        with tempfile.TemporaryDirectory() as tmp:
            recorder = FakeRecorder(Path(tmp)/'first', first)
            report, _ = c.collect_polymarket(REPO, recorder, NOW, {'markets': {}, 'latest': {}})
            payload = json.loads((recorder.directory/'checkpoints.jsonl').read_text().splitlines()[-1])
            self.assertEqual(report['status'], 'DEGRADED')
            self.assertTrue(payload['pending_backfills'])
            self.assertGreater(payload['collected_through'], payload['latest'])
            second = [{'events': []}, {'events': [{'id': 1, 'markets': [closed]}]}, {'history': []}, {'history': []}]
            recorder = FakeRecorder(Path(tmp)/'second', second)
            report, _ = c.collect_polymarket(REPO, recorder, NOW, {'markets': {}, 'latest': {}}, recovery={'1': payload})
            self.assertEqual(report['status'], 'FAILED')
            self.assertGreater(report['history_tasks'], 0)
            self.assertTrue(report['backfill_gaps'])

    def test_terminal_keyset_page_may_omit_cursor(self):
        c.validate_gamma({'events': []})
        with self.assertRaises(c.CollectionError):
            c.validate_gamma({})

    def test_old_closure_returned_by_discovery_also_gets_terminal_history(self):
        old = market()
        old['startDate'] = old['acceptingOrdersTimestamp'] = c.stamp(NOW-dt.timedelta(days=25))
        closed = dict(old, closed=True, closedTime=c.stamp(NOW-dt.timedelta(days=20)))
        point = int((NOW-dt.timedelta(days=20)).timestamp())
        responses = [{'events': [], 'next_cursor': None}, {'events': [{'id': 1, 'markets': [closed]}]},
                     {'history': [{'t': point, 'p': 1.0}]}]
        with tempfile.TemporaryDirectory() as tmp:
            recorder = FakeRecorder(tmp, responses)
            report, _ = c.collect_polymarket(REPO, recorder, NOW,
                {'markets': {'1': old}, 'latest': {'1': point-86400}})
            self.assertEqual(report['status'], 'OK')
            self.assertEqual(report['history_tasks'], 1)

    def test_old_open_market_closed_during_long_outage_gets_terminal_history(self):
        old = market()
        old['startDate'] = old['acceptingOrdersTimestamp'] = c.stamp(NOW-dt.timedelta(days=25))
        closed = dict(old, closed=True, closedTime=c.stamp(NOW-dt.timedelta(days=20)))
        closing_point = int((NOW-dt.timedelta(days=20)).timestamp())
        responses = [{'events': [], 'next_cursor': None}, {'events': [], 'next_cursor': None},
                     closed, {'history': [{'t': closing_point, 'p': 1.0}]}]
        with tempfile.TemporaryDirectory() as tmp:
            recorder = FakeRecorder(tmp, responses)
            report, state = c.collect_polymarket(REPO, recorder, NOW,
                {'markets': {'1': old}, 'latest': {'1': int((NOW-dt.timedelta(days=24)).timestamp())}})
            self.assertEqual(report['status'], 'OK')
            self.assertEqual(report['history_tasks'], 1)
            self.assertEqual(state['latest']['1'], closing_point)
            self.assertEqual(report['backfill_gaps'], [])
            self.assertTrue(recorder.calls[-1][0].endswith('/prices-history'))

    def test_resolved_probe_out_of_range_tick_is_not_backfill(self):
        current, old = market(), market('2', closed=True, age=20)
        current_tick = int(NOW.timestamp())-10
        responses = [{'events': [{'id': 1, 'markets': [current]}], 'next_cursor': None},
                     {'events': [], 'next_cursor': None}, {'history': [{'t': current_tick, 'p': .5}]},
                     old, {'history': [{'t': current_tick, 'p': 1.0}]}]
        with tempfile.TemporaryDirectory() as tmp:
            report, _ = c.collect_polymarket(REPO, FakeRecorder(tmp, responses), NOW,
                {'markets': {'2': old}, 'latest': {}}, probe=True)
            self.assertEqual(report['status'], 'DEGRADED')
            probe = report['resolved_market_probes'][0]
            self.assertEqual(probe['status'], 'EMPTY_HISTORY')
            self.assertEqual(probe['rows'], 0)
            self.assertEqual(probe['out_of_range_rows'], 1)
            self.assertFalse(probe['exact_archive_match_verified'])

    def test_received_revision_is_separate_and_original_tail_preserved(self):
        current, point = market(), int(NOW.timestamp())-300
        responses = [{'events': [{'id': 1, 'markets': [current]}], 'next_cursor': None},
                     {'events': [], 'next_cursor': None}, {'history': [{'t': point, 'p': .7}]}]
        prior = {'markets': {'1': current}, 'latest': {'1': point}, 'recent_observations': {'1': [[point, .5]]}}
        with tempfile.TemporaryDirectory() as tmp:
            report, state = c.collect_polymarket(REPO, FakeRecorder(tmp, responses), NOW, prior)
            self.assertEqual(report['revised_price_observations'], 1)
            self.assertEqual(state['recent_observations']['1'], [[point, .5], [point, .7]])
            self.assertEqual(prior['recent_observations']['1'], [[point, .5]])
            revision = json.loads(gzip.decompress((Path(tmp)/'price-revisions.jsonl.gz').read_bytes()))
            self.assertEqual(revision['previous_prices'], [.5])
            self.assertEqual(revision['received_price'], .7)

    def test_cursor_loop_rejected(self):
        responses = [{'events': [], 'next_cursor': 'same'}, {'events': [], 'next_cursor': 'same'}]
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(c.CollectionError, 'CURSOR'):
            c.collect_polymarket(REPO, FakeRecorder(tmp, responses), NOW, {'markets': {}, 'latest': {}})

    def test_incremental_overlap_out_of_range_preserved_never_advances_clock(self):
        current = market()
        cutoff = int(NOW.timestamp())
        state = {'markets': {'1': current}, 'latest': {'1': cutoff-3600}}
        responses = [{'events': [{'id': 1, 'markets': [current]}], 'next_cursor': None},
                     {'events': [], 'next_cursor': None},
                     {'history': [{'t': cutoff-300, 'p': .5}, {'t': cutoff+300, 'p': .6}]}]
        with tempfile.TemporaryDirectory() as tmp:
            recorder = FakeRecorder(tmp, responses)
            report, new = c.collect_polymarket(REPO, recorder, NOW, state)
            self.assertEqual(report['out_of_range_rows'], 1)
            self.assertEqual(new['latest']['1'], cutoff-300)
            self.assertEqual(state['latest']['1'], cutoff-3600)
            self.assertEqual(recorder.calls[2][1]['startTs'], cutoff-10800)
            rows = gzip.decompress((Path(tmp)/'prices.jsonl.gz').read_bytes()).splitlines()
            self.assertEqual(len(rows), 2)

    def test_revisions_retained_and_empty_history_fails(self):
        for history in [[{'t': int(NOW.timestamp())-50, 'p': .5}, {'t': int(NOW.timestamp())-50, 'p': .6}], []]:
            current = market()
            responses = [{'events': [{'id': 1, 'markets': [current]}], 'next_cursor': None},
                         {'events': [], 'next_cursor': None}, {'history': history}]
            with tempfile.TemporaryDirectory() as tmp:
                report, _ = c.collect_polymarket(REPO, FakeRecorder(tmp, responses), NOW, {'markets': {}, 'latest': {}})
                self.assertEqual(report['status'], 'OK' if history else 'FAILED')
                self.assertEqual(report['price_observations'], len(history))

    def test_resolved_probe_sample_spans_ages_deterministically(self):
        markets = {str(i): market(str(i), True, days) for i, days in enumerate([2, 4, 15, 70, 200, 500])}
        selected = c.resolved_sample(markets, NOW)
        self.assertEqual(len(selected), 5)
        self.assertEqual([m['id'] for m in selected], [m['id'] for m in c.resolved_sample(dict(reversed(list(markets.items()))), NOW)])
        self.assertEqual(len(c.resolved_sample(markets, NOW, limit=100)), 5)

    def test_legacy_selector_keeps_known_scope_no_silent_fix(self):
        select, digest = c.legacy_selector(REPO)
        self.assertTrue(select({'outcomes': '["Over","Under"]', 'question': 'Innings O/U 150.5'}))
        self.assertFalse(select({'outcomes': '["Yes","No"]', 'question': 'Alpha wins'}))
        self.assertEqual(len(digest), 64)


class IntegrationTests(unittest.TestCase):
    def test_request_budget_fairness_visits_deferred_markets_on_next_run(self):
        rows = [market(str(i)) for i in range(1, 4)]
        with tempfile.TemporaryDirectory() as tmp, patch.object(c, 'bootstrap', return_value={'markets': {}, 'latest': {}}):
            visited = []
            for index in range(3):
                responses = [{'events': [{'id': 1, 'markets': rows}]}, {'events': []},
                             {'history': [{'t': int(NOW.timestamp())-60, 'p': .5}]}]
                def factory(path):
                    result = FakeRecorder(path, responses)
                    result.max_requests = 3
                    return result
                report = c.collect(REPO, tmp, 'budget'+str(index), 'polymarket', now=NOW, recorder_factory=factory)
                self.assertEqual(report['status'], 'DEGRADED')
                self.assertEqual(len(report['sources']['polymarket']['history_backlog']), 2)
                source = json.loads((Path(tmp)/'state.json').read_text())['sources']['polymarket']
                self.assertNotIn('cursor', source)
                attempts = c.verified_attempts(Path(tmp), source['market_attempts'])
                visited.append(next(iter(json.loads(line)['market_id'] for line in
                    (Path(tmp)/'runs'/('budget'+str(index))/'polymarket/market-attempts.jsonl').read_text().splitlines())))
                self.assertEqual(len(attempts), index+1)
            self.assertEqual(visited, ['1', '2', '3'])

    def test_market_http_failure_does_not_discard_other_completed_tasks(self):
        rows = [market('1'), market('2')]
        responses = [{'events': [{'id': 1, 'markets': rows}]}, {'events': []},
                     c.CollectionError('REQUEST_FAILED_HTTP_503'), {'history': [{'t': int(NOW.timestamp())-60, 'p': .5}]}]
        with tempfile.TemporaryDirectory() as tmp:
            recorder = FakeRecorder(tmp, responses)
            report, _ = c.collect_polymarket(REPO, recorder, NOW, {'markets': {}, 'latest': {}})
            self.assertEqual(report['status'], 'FAILED')
            self.assertEqual(report['failed_history_tasks'][0]['market_id'], '1')
            self.assertEqual(json.loads((Path(tmp)/'checkpoints.jsonl').read_text())['market_id'], '2')
            self.assertEqual(len((Path(tmp)/'market-attempts.jsonl').read_text().splitlines()), 2)

    def test_repeated_partial_failure_recovers_intervals_without_clearing_gap_or_success(self):
        old, empty = market(), market('2')
        old['startDate'] = old['acceptingOrdersTimestamp'] = c.stamp(NOW-dt.timedelta(days=40))
        start = int((NOW-dt.timedelta(days=40)).timestamp())
        with tempfile.TemporaryDirectory() as tmp, patch.object(c, 'bootstrap', return_value={'markets': {}, 'latest': {}}):
            for index in range(2):
                current = NOW+dt.timedelta(hours=index)
                data = [{'events': [{'id': 1, 'markets': [old, empty]}]}, {'events': []},
                        {'history': [{'t': int(current.timestamp())-60, 'p': .5}]},
                        {'history': [{'t': start+index*13*86400+3600, 'p': .4}]}, {'history': []}]
                factory = lambda path, responses=data: FakeRecorder(path, responses)
                report = c.collect(REPO, tmp, 'partial'+str(index), 'polymarket', now=current, recorder_factory=factory)
                self.assertEqual(report['status'], 'DEGRADED')
                source = json.loads((Path(tmp)/'state.json').read_text())['sources']['polymarket']
                self.assertNotIn('cursor', source)
                self.assertIsNone(report['sources']['polymarket']['last_success_at'])
                recovered = c.verified_recovery(Path(tmp), source['recovery'], None)['1']
                self.assertEqual(recovered['pending_backfills'][0][0], start+(index+1)*13*86400)
                self.assertEqual(sum(g['market_id'] == '1' for g in report['sources']['polymarket']['backfill_gaps']), 1)
                self.assertEqual(recovered['latest'], int(current.timestamp())-60)
            reference = source['recovery']['1']
            with (Path(tmp)/reference['file']).open('ab') as stream:
                stream.write(b'{}\n')
            with self.assertRaisesRegex(c.CollectionError, 'CHANGED'):
                c.verified_recovery(Path(tmp), source['recovery'], None)

    def test_complete_run_idempotent_partial_failure_does_not_advance_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            earlier = c.stamp(NOW-dt.timedelta(days=1))
            prior = {'schema': 'collection-state-v1', 'sources': {'polymarket': {'last_success_at': earlier, 'cursor': {'old': True}}}}
            c.replace_json(output/'state.json', prior)
            failed = {'status': 'DEGRADED', 'received_at': c.stamp(NOW), 'backfill_gaps': [1]}
            fake = lambda path: FakeRecorder(path, [])
            with patch.object(c, 'collect_polymarket', return_value=(failed, {'bad': 'advanced'})):
                report = c.collect(REPO, output, 'test', 'polymarket', now=NOW, recorder_factory=fake)
            self.assertEqual(json.loads((output/'state.json').read_text()), prior)
            before = {p.relative_to(output): p.read_bytes() for p in output.rglob('*') if p.is_file()}
            with patch.object(c, 'collect_polymarket', side_effect=AssertionError('No repeat network')):
                self.assertEqual(c.collect(REPO, output, 'test', 'polymarket'), report)
            self.assertEqual(before, {p.relative_to(output): p.read_bytes() for p in output.rglob('*') if p.is_file()})

    def test_failed_run_saved_success_clock_not_refreshed(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = lambda path: FakeRecorder(path, [])
            with patch.object(c, 'collect_bettingpros', side_effect=ValueError('secret')):
                report = c.collect(REPO, tmp, 'failed', 'bettingpros', now=NOW, recorder_factory=fake)
            self.assertEqual(report['status'], 'FAILED')
            self.assertIsNone(report['sources']['bettingpros']['last_success_at'])
            self.assertNotIn('secret', json.dumps(report))
            self.assertTrue((Path(tmp)/'runs/failed/report.json').exists())

    def test_protected_output_paths_and_incomplete_reuse_refused(self):
        for output in [REPO/'wnba/live/x', REPO/'wnba/data/raw/bp/new', REPO/'cricket/data/raw/polymarket/new']:
            with self.assertRaises(ValueError): c.collect(REPO, output, 'bad')
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp)/'runs/interrupted').mkdir(parents=True)
            with self.assertRaises(FileExistsError): c.collect(REPO, tmp, 'interrupted')
            with self.assertRaises(ValueError): c.collect(REPO, tmp, '../escape')

    def test_process_lock_blocks_concurrency_and_releases_without_deleting_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = (Path(tmp)/'.collection.lock').open('ab')
            c.fcntl.flock(lock.fileno(), c.fcntl.LOCK_EX | c.fcntl.LOCK_NB)
            try:
                with self.assertRaisesRegex(c.CollectionError, 'ALREADY_RUNNING'):
                    c.collect(REPO, tmp, 'locked', 'bettingpros')
            finally:
                lock.close()
            fake = lambda path: FakeRecorder(path, [{'events': []}])
            self.assertEqual(c.collect(REPO, tmp, 'after-release', 'bettingpros', recorder_factory=fake)['status'], 'OK')


if __name__ == '__main__':
    unittest.main()
