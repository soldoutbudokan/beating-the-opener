"""Regression tests for interrupted captures; no parquet engine or network needed."""
import datetime as dt
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from research.engine.cricket_capture import (
    Recorder,
    captured_observations,
    load_resume_snapshot,
    preserve_prices,
)


class CricketResumeTests(unittest.TestCase):
    ORIGINAL_HASHES = {"markets.parquet": "original-markets", "prices.parquet": "original-prices"}
    CUTOFF = "2026-09-07T10:30:00+00:00"
    METADATA = b"fixture: requested markets include newly discovered market"

    def write_plan(self, directory, inherited=()):
        metadata = directory / "requested-markets.parquet"
        if not metadata.exists():
            metadata.write_bytes(self.METADATA)
        plan = {
            "schema": "cricket-fetch-plan-v1",
            "price_as_of": self.CUTOFF,
            "original_sha256": self.ORIGINAL_HASHES,
            "requested_metadata_sha256": hashlib.sha256(metadata.read_bytes()).hexdigest(),
            "planned_market_ids": ["old-open", "new-market"],
            "inherited_response_files": list(inherited),
        }
        (directory / "fetch-plan.json").write_text(json.dumps(plan))

    @staticmethod
    def history_record(token, points, end_ts=1788000000):
        return {
            "url": "https://fixture.invalid/prices-history",
            "params": {"market": token, "startTs": 1787000000, "endTs": end_ts},
            "status": 200,
            "body": json.dumps({"history": [{"t": t, "p": p} for t, p in points]}),
            "observed_at": "2026-09-07T10:31:00+00:00",
        }

    @staticmethod
    def write_records(path, records):
        with gzip.open(path, "wt", encoding="utf-8") as stream:
            for record in records:
                stream.write(json.dumps(record) + "\n")

    def test_explicit_cutoff_survives_only_earlier_closed_market_requests(self):
        with tempfile.TemporaryDirectory() as temp:
            prior, output = Path(temp) / "prior", Path(temp) / "resumed"
            prior.mkdir()
            output.mkdir()
            self.write_plan(prior)
            record = self.history_record("closed-token", [(1787999000, .4)])
            self.write_records(prior / "responses.jsonl.gz", [record])

            snapshot = load_resume_snapshot(prior, output, self.ORIGINAL_HASHES)

            self.assertEqual(snapshot["price_as_of"], self.CUTOFF)
            self.assertGreater(dt.datetime.fromisoformat(snapshot["price_as_of"]).timestamp(),
                               record["params"]["endTs"])
            self.assertEqual(snapshot["records"], [record])

    def test_requested_metadata_wins_over_stale_staging_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            prior, output = Path(temp) / "prior", Path(temp) / "resumed"
            prior.mkdir()
            output.mkdir()
            self.write_plan(prior)
            (prior / "staging").mkdir()
            stale = prior / "staging/markets.parquet"
            stale.write_bytes(b"fixture: only old markets from before this fetch")

            snapshot = load_resume_snapshot(prior, output, self.ORIGINAL_HASHES)

            self.assertEqual(snapshot["metadata_path"].read_bytes(), self.METADATA)
            self.assertNotEqual(snapshot["metadata_path"].read_bytes(), stale.read_bytes())
            self.assertEqual(snapshot["planned_market_ids"], ["old-open", "new-market"])

    def test_nested_resume_carries_inherited_and_current_response_archives(self):
        with tempfile.TemporaryDirectory() as temp:
            original, first, second = [Path(temp) / name for name in ("original", "first", "second")]
            for directory in (original, first, second):
                directory.mkdir()
            self.write_plan(original)
            inherited = self.history_record("old-token", [(1787999000, .4)])
            current = self.history_record("new-token", [(1787999600, .6)])
            self.write_records(original / "responses.jsonl.gz", [inherited])
            original_bytes = (original / "responses.jsonl.gz").read_bytes()

            first_snapshot = load_resume_snapshot(original, first, self.ORIGINAL_HASHES)
            lineage = [str(path.relative_to(first)) for path in first_snapshot["response_paths"]]
            self.write_plan(first, inherited=lineage)
            self.write_records(first / "responses.jsonl.gz", [current])
            current_bytes = (first / "responses.jsonl.gz").read_bytes()
            second_snapshot = load_resume_snapshot(first, second, self.ORIGINAL_HASHES)

            self.assertEqual(second_snapshot["records"], [inherited, current])
            self.assertEqual([path.read_bytes() for path in second_snapshot["response_paths"]],
                             [original_bytes, current_bytes])
            self.assertEqual(second_snapshot["price_as_of"], self.CUTOFF)
            self.assertEqual((original / "responses.jsonl.gz").read_bytes(), original_bytes)

    def test_legacy_capture_without_explicit_plan_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            prior, output = Path(temp) / "legacy", Path(temp) / "resumed"
            prior.mkdir()
            output.mkdir()
            (prior / "staging").mkdir()
            (prior / "staging/markets.parquet").write_bytes(self.METADATA)
            self.write_records(prior / "responses.jsonl.gz", [self.history_record("token", [])])

            with self.assertRaisesRegex(ValueError, "no explicit fetch plan"):
                load_resume_snapshot(prior, output, self.ORIGINAL_HASHES)

            self.assertEqual(list(output.iterdir()), [])

    def test_probe_additional_observations_and_revisions_join_old_archive(self):
        old = [("resolved", 1787999000, .4)]
        probe = self.history_record("probe-token", [(1787999000, .5), (1787999600, .6)])

        observations = captured_observations([probe], {"probe-token": "resolved"})
        combined, conflicts = preserve_prices(old, observations)

        self.assertEqual(set(combined), {("resolved", 1787999000, .4),
                                         ("resolved", 1787999000, .5),
                                         ("resolved", 1787999600, .6)})
        self.assertEqual(conflicts, 1)

    def test_malformed_successful_history_is_recorded_as_error(self):
        malformed = [{}, {"history": None}, [],
                     {"history": [{"t": "1787999000", "p": .5}]},
                     {"history": [{"t": 1787999000, "p": 1.1}]},
                     {"history": [{"t": 1787999000, "p": float("nan")}]},
                     {"history": [{"t": 1787999000, "p": True}]}]
        for data in malformed:
            with self.subTest(payload=data), tempfile.TemporaryDirectory() as temp:
                body = json.dumps(data)
                response = SimpleNamespace(status_code=200, text=body, content=body.encode(),
                                           headers={}, raise_for_status=lambda: None,
                                           json=lambda: data)
                recorder = Recorder(Path(temp) / "responses.jsonl.gz",
                                    lambda *args, **kwargs: response, {})

                with self.assertRaisesRegex(RuntimeError, "API request failed"):
                    recorder("https://fixture.invalid/prices-history", tries=1)

                with gzip.open(Path(temp) / "responses.jsonl.gz", "rt") as stream:
                    records = [json.loads(line) for line in stream]
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0]["status"], 200)
                self.assertIn("ValueError", records[0]["error"])
                self.assertEqual(records[0]["body"], body)
                self.assertEqual(recorder.count, 1)

    def test_malformed_event_page_is_not_an_empty_month(self):
        for data in [{}, {"error": "unexpected"}, [None]]:
            with self.subTest(payload=data), tempfile.TemporaryDirectory() as temp:
                body = json.dumps(data)
                response = SimpleNamespace(status_code=200, text=body, content=body.encode(),
                                           headers={}, raise_for_status=lambda: None,
                                           json=lambda: data)
                recorder = Recorder(Path(temp) / "responses.jsonl.gz", lambda *args, **kwargs: response, {})
                with self.assertRaisesRegex(RuntimeError, "Events response must be a list"):
                    recorder("https://fixture.invalid/events", tries=1)
                with gzip.open(Path(temp) / "responses.jsonl.gz", "rt") as stream:
                    record = json.loads(stream.readline())
                self.assertIn("error", record)
                self.assertLessEqual(record["requested_at"], record["observed_at"])


if __name__ == "__main__":
    unittest.main()
