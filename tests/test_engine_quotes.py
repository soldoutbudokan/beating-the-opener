import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from research.engine.quotes import (MARKET_IDS, QuoteArchive, decimal_odds,
                                   paired_reference, preflight_quotes, score_quote)


def offer(over_at='2025-05-16 23:29:00', under_at='2025-05-16 23:29:10', line=5.5, book=10):
    return {'selections': [dict(selection=side, books=[dict(id=book, lines=[dict(
        line=line, cost=-110, updated=at, active=True, is_off=False)])])
        for side, at in [('over', over_at), ('under', under_at)]]}


def frozen_quote(market='points'):
    return {'season': 2025, 'event_id': '11', 'offer_id': 'offer',
            'game_id': '22', 'athlete_id': '33', 'bp_player_id': '44',
            'market': market, 'open_book': 10, 'open_book_under': 10,
            'open_line': 5.5, 'open_over': -160, 'open_under': 140,
            'open_created_over': '2025-05-16T01:00:00Z',
            'open_created_under': '2025-05-16T01:00:00Z',
            'forecast_at': '2025-05-16T01:00:00Z',
            'tip_at': '2025-05-16T23:30:00Z', 'date': '2025-05-16',
            'p_open': .602, 'box_ridge_p_over_nonpush': .57}


class QuoteTests(unittest.TestCase):
    def reference(self, value):
        return paired_reference(value, 10, 5.5, '2025-05-16T23:30:00Z', '2025-05-16T01:00:00Z')

    def test_same_line_pair_preserves_clocks_and_price(self):
        r = self.reference(offer())
        self.assertEqual(r['status'], 'matched')
        self.assertAlmostEqual(r['p_over_nonpush'], .5)
        self.assertEqual(r['age_seconds'], 50)
        self.assertNotEqual(r['over_at'], r['under_at'])

    def test_after_tip_wrong_line_book_and_skew_are_missing(self):
        for value in [offer(over_at='2025-05-16 23:30:00'), offer(line=6.5), offer(book=13),
                      offer(over_at='2025-05-16 22:30:00')]:
            self.assertEqual(self.reference(value)['status'], 'missing')

    def test_conflicting_latest_prices_are_not_arbitrarily_chosen(self):
        value = offer()
        row = dict(value['selections'][0]['books'][0]['lines'][0], cost=-105)
        value['selections'][0]['books'][0]['lines'].append(row)
        self.assertEqual(self.reference(value)['status'], 'ambiguous')

    def test_invalid_odds(self):
        for odds in [0, 99, float('nan'), float('inf')]:
            with self.assertRaises(ValueError): decimal_odds(odds)
        self.assertEqual(decimal_odds(150), 2.5)
        self.assertEqual(decimal_odds(-200), 1.5)

    def test_power_opener_is_preserved_separately_from_price_contract(self):
        raw = frozen_quote()
        row = score_quote(raw)
        implied = (1 / row['over_odds']) / (1 / row['over_odds'] + 1 / row['under_odds'])
        self.assertEqual(row['p_open_nonpush'], implied)
        self.assertEqual(row['pr2_power_p_open'], raw['p_open'])
        self.assertNotEqual(row['p_open_nonpush'], row['pr2_power_p_open'])
        self.assertEqual(preflight_quotes([raw])['status'], 'PASS')

    def test_core_markets_resolve_exact_offer_and_reject_identity_changes(self):
        expected = {'points': 393, 'rebounds': 397, 'assists': 391, 'threes': 390}
        self.assertEqual(MARKET_IDS, expected)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'offers').mkdir()
            for market, market_id in expected.items():
                raw = frozen_quote(market)
                value = offer()
                value.update(id='offer', event_id=11, market_id=market_id, player_id=44)
                path = root / 'offers' / f'11_{market_id}.json.gz'
                path.write_bytes(gzip.compress(json.dumps({'offers': [value]}).encode()))
                self.assertEqual(QuoteArchive(root).reference(raw)['status'], 'matched')
                for field, bad in [('event_id', 12), ('market_id', 999), ('player_id', 45)]:
                    changed = dict(value, **{field: bad})
                    path.write_bytes(gzip.compress(json.dumps({'offers': [changed]}).encode()))
                    with self.assertRaisesRegex(ValueError, 'identity differs'):
                        QuoteArchive(root).reference(raw)
                path.write_bytes(gzip.compress(json.dumps({'offers': [value]}).encode()))

    def test_preflight_uses_synthetic_outcomes_and_no_scoring(self):
        class OutcomeGuard(dict):
            def __getitem__(self, key):
                if key in ('actual', 'void', 'actual_minutes', 'box_ridge_mean'):
                    raise AssertionError('Preflight read an outcome or model forecast')
                return super().__getitem__(key)
        raw = OutcomeGuard(frozen_quote())
        with patch('research.engine.scoring.build_report', side_effect=AssertionError('scoring forbidden')), \
             patch('research.engine.scoring.score_forecasts', side_effect=AssertionError('scoring forbidden')), \
             patch('research.engine.scoring.score_bets', side_effect=AssertionError('scoring forbidden')):
            self.assertEqual(preflight_quotes([raw])['quotes'], 1)
            with self.assertRaisesRegex(ValueError, 'duplicate'):
                preflight_quotes([raw, raw])
        changed = dict(raw, date='2025-05-17')
        with self.assertRaisesRegex(ValueError, 'Eastern tip date'):
            preflight_quotes([changed])
