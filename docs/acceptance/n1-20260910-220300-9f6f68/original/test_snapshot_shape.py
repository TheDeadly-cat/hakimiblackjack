"""Reviewer regression: one malformed snapshot must not break history enumeration.

Uses temporary folders and synthetic storage envelopes, not real user records.
Run against the full project with its root on PYTHONPATH. This review's local
run used only the three exact, hash-verified modules under isolated/.
"""
import tempfile
import unittest
from pathlib import Path
from blackjack_lab.analysis.contracts import RESULT_SCHEMA, digest
from blackjack_lab.storage.analysis_snapshots import AnalysisSnapshots


class SnapshotShapeReview(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.store = AnalysisSnapshots(self.directory)
        payload = {"synthetic_storage_probe": True}
        # This is a minimal envelope accepted by storage, not a computed EV result.
        self.valid = self.store.save({"schema": RESULT_SCHEMA, "input": payload,
                                      "input_digest": digest(payload)})
        self.valid_path = self.directory / (self.valid["snapshot_id"] + ".json")
        self.valid_bytes = self.valid_path.read_bytes()

    def check_bad_file(self, text):
        path = self.directory / ("a" * 32 + ".json")
        path.write_text(text, encoding="utf-8")
        before = path.read_bytes()
        entries, damaged = self.store.list()
        self.assertEqual([e["snapshot_id"] for e in entries], [self.valid["snapshot_id"]])
        self.assertEqual(len(damaged), 1)
        self.assertEqual(damaged[0]["file"], path.name)
        self.assertEqual(self.valid_path.read_bytes(), self.valid_bytes)
        self.assertEqual(path.read_bytes(), before)

    def test_valid_envelope_lists_without_damage(self):
        entries, damaged = self.store.list()
        self.assertEqual(entries, [self.valid])
        self.assertEqual(damaged, [])

    def test_invalid_json_syntax_is_isolated(self):
        self.check_bad_file("{")

    def test_array_json_root_is_isolated(self):
        self.check_bad_file("[]")

    def test_null_json_root_is_isolated(self):
        self.check_bad_file("null")


if __name__ == "__main__":
    unittest.main()
