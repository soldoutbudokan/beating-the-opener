import gzip
import json
from pathlib import Path
import tempfile
import datetime as dt
from types import SimpleNamespace
import unittest

from research.engine.cricket_capture import Recorder, parallel_histories, preserve_prices


class CricketCaptureTests(unittest.TestCase):
    def test_incomplete_refresh_never_drops_old_rows(self):
        old = [("open", 1, .4), ("open", 2, .5), ("closed", 1, .3)]
        merged, conflicts = preserve_prices(old, [("open", 2, .5), ("new", 3, .6)])
        self.assertTrue(set(old).issubset(merged))
        self.assertEqual(len(merged), 4)
        self.assertEqual(conflicts, 0)

    def test_price_revision_is_preserved_and_flagged(self):
        merged, conflicts = preserve_prices([("m", 1, .4)], [("m", 1, .6)])
        self.assertEqual(len(merged), 2)
        self.assertEqual(conflicts, 1)

    def test_failure_is_recorded_and_not_an_empty_page(self):
        def fail(*args, **kwargs):
            raise TimeoutError("fixture timeout")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "responses.jsonl.gz"
            recorder = Recorder(path, fail, {})
            with self.assertRaisesRegex(RuntimeError, "API request failed"):
                recorder("https://fixture.invalid/events", tries=1)
            with gzip.open(path, "rt") as stream:
                record = json.loads(stream.readline())
            self.assertIn("TimeoutError", record["error"])
            self.assertEqual(recorder.count, 1)

    def test_parallel_history_records_empty_missing_and_success(self):
        start = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
        fetcher = SimpleNamespace(parse_ts=lambda value: value,
                                  price_history=lambda token, lo, hi: [(1, .5)] if token == "has" else [])
        rows = [SimpleNamespace(market_id=k, clob_token_ids=json.dumps(tokens),
                                accepting_orders_ts=start, start_date=start, closed_time=start)
                for k, tokens in [("a", ["has"]), ("b", ["empty"]), ("c", [])]]
        with tempfile.TemporaryDirectory() as temp:
            observations, statuses = parallel_histories(fetcher, rows, start + dt.timedelta(days=1), 4,
                                                        Path(temp) / "status.jsonl")
            self.assertEqual(observations, [("a", 1, .5)])
            self.assertEqual({s["status"] for s in statuses}, {"RETURNED", "EMPTY_HISTORY", "SKIPPED_MISSING_TOKEN"})
            self.assertEqual(len(statuses), 3)

    def test_parallel_failures_cannot_hide_completed_or_empty_tasks(self):
        start = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
        def history(token, lo, hi):
            if token == "bad":
                raise TimeoutError("fixture failure")
            return []
        fetcher = SimpleNamespace(parse_ts=lambda value: value, price_history=history)
        rows = [SimpleNamespace(market_id=k, clob_token_ids=json.dumps([k]),
                                accepting_orders_ts=start, start_date=start, closed_time=start)
                for k in ["bad", "empty"]]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "status.jsonl"
            with self.assertRaisesRegex(RuntimeError, "1 market history tasks failed"):
                parallel_histories(fetcher, rows, start + dt.timedelta(days=1), 4, path)
            statuses = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual({s["status"] for s in statuses}, {"ERROR", "EMPTY_HISTORY"})
            self.assertEqual(len(statuses), 2)


if __name__ == "__main__":
    unittest.main()
