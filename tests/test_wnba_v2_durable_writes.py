"""Fail closed when immutable evidence does not reach disk intact."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from research.wnba_v2 import shadow


class FaultyWriter:
    """Use a real file while simulating a storage layer losing some bytes."""

    def __init__(self, handle, mode):
        self.handle, self.mode = handle, mode

    def __enter__(self):
        self.handle.__enter__()
        return self

    def __exit__(self, *args):
        return self.handle.__exit__(*args)

    def write(self, data):
        if self.mode == "corrupt":
            stored = b"!" + data[1:]
        else:
            stored = data[:len(data) // 2]
        written = self.handle.write(stored)
        return written if self.mode == "short" else len(data)

    def flush(self):
        return self.handle.flush()

    def fileno(self):
        return self.handle.fileno()


class DurableWriteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "nested" / "evidence.bin"

    def test_complete_large_and_empty_files_are_exact_and_immutable(self):
        # Cross buffer boundaries using nonuniform content, then preserve the
        # original file when a later caller attempts to reuse its identity.
        payload = bytes(range(256)) * 32769
        shadow.write_once(self.path, payload)
        self.assertEqual(self.path.read_bytes(), payload)
        with self.assertRaises(FileExistsError):
            shadow.write_once(self.path, b"replacement")
        self.assertEqual(self.path.read_bytes(), payload)
        empty = self.path.with_name("empty.bin")
        shadow.write_once(empty, b"")
        self.assertEqual(empty.read_bytes(), b"")

    def exercise_failure(self, mode, message):
        payload = b"complete evidence must remain intact\n"
        fdopen = shadow.os.fdopen
        writers = []

        def faulty(fd, *args, **kwargs):
            writer = FaultyWriter(fdopen(fd, *args, **kwargs), mode)
            writers.append(writer)
            return writer

        with patch.object(shadow.os, "fdopen", side_effect=faulty):
            with self.assertRaisesRegex(OSError, message):
                shadow.write_once(self.path, payload)
        self.assertTrue(writers[0].handle.closed)
        actual = self.path.read_bytes()
        self.assertNotEqual(actual, payload)
        expected = b"!" + payload[1:] if mode == "corrupt" else payload[:len(payload) // 2]
        self.assertEqual(actual, expected)
        # Failure evidence survives and cannot be silently repaired in place.
        with self.assertRaises(FileExistsError):
            shadow.write_once(self.path, payload)
        self.assertEqual(self.path.read_bytes(), actual)

    def test_reported_short_write_fails_and_preserves_partial_file(self):
        self.exercise_failure("short", "EVIDENCE_SHORT_WRITE")

    def test_silent_truncation_fails_even_when_writer_reports_success(self):
        self.exercise_failure("truncate", "EVIDENCE_READBACK_MISMATCH")

    def test_same_length_corruption_fails_exact_readback(self):
        self.exercise_failure("corrupt", "EVIDENCE_READBACK_MISMATCH")


if __name__ == "__main__":
    unittest.main()
