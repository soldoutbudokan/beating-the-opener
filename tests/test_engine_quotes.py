import unittest
from research.engine.quotes import paired_reference, decimal_odds


def offer(over_at='2025-05-16 23:29:00', under_at='2025-05-16 23:29:10', line=5.5, book=10):
    return {'selections': [dict(selection=side, books=[dict(id=book, lines=[dict(
        line=line, cost=-110, updated=at, active=True, is_off=False)])])
        for side, at in [('over', over_at), ('under', under_at)]]}


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
