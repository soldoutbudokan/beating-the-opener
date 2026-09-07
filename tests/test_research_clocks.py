import unittest
from datetime import date, datetime, timezone
from research.clocks import FIELDS, parse, schedule_tip, before


class ClockContracts(unittest.TestCase):
    def test_every_consumed_column(self):
        expected = datetime(2025, 7, 1, 23, tzinfo=timezone.utc)
        for field, (kind, zone, _) in FIELDS.items():
            with self.subTest(field=field):
                if kind == 'date':
                    self.assertEqual(parse(field, '2025-07-01'), date(2025, 7, 1))
                    with self.assertRaises(ValueError): parse(field, expected)
                elif kind == 'epoch':
                    self.assertEqual(parse(field, expected.timestamp()), expected)
                    with self.assertRaises(ValueError): parse(field, expected.timestamp() * 1000)
                else:
                    text = '2025-07-01T19:00:00-04:00' if zone == 'America/New_York' else '2025-07-01T23:00:00Z'
                    self.assertEqual(parse(field, text), expected)
                    with self.assertRaises(ValueError): parse(field, '2025-07-01')
                with self.assertRaises(ValueError): parse(field, None)

    def test_source_zones_and_dst(self):
        self.assertEqual(schedule_tip('2025-07-02 00:00:00', '2025-07-01 20:00:00'),
                         datetime(2025, 7, 2, tzinfo=timezone.utc))
        self.assertEqual(schedule_tip('2025-01-02T01:00:00Z', '2025-01-01T20:00:00-05:00'),
                         datetime(2025, 1, 2, 1, tzinfo=timezone.utc))
        for utc, eastern in [('2025-07-02T00:00:00Z', '2025-07-02T00:00:00Z'),
                             ('2025-07-02T00:00:00Z', '2025-07-01T19:00:00-04:00')]:
            with self.assertRaises(ValueError): schedule_tip(utc, eastern)
        for value in ['2025-11-02 01:30:00', '2025-03-09 02:30:00']:
            with self.assertRaises(ValueError): parse('wehoop.game_date_time', value)
        with self.assertRaises(ValueError): parse('news.published', '2025-07-01 23:00:00')

    def test_strict_information_boundary(self):
        a = parse('forecast.as_of', '2025-07-01T23:00:00Z')
        before(parse('news.published', '2025-07-01T22:59:59Z'), a)
        for value in [a, parse('news.published', '2025-07-02T00:00:00Z'), date(2025, 7, 1)]:
            with self.assertRaises(ValueError): before(value, a)


if __name__ == '__main__': unittest.main()
