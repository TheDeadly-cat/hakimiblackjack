"""Evidence levels never auto-accept missing or mismatched artifacts."""
import tempfile
import unittest
from pathlib import Path

from blackjack_lab.analysis.evidence import (
    LEVEL_DECLARED, LEVEL_EVIDENCE_LINKED, LEVEL_MISSING, LEVEL_REVIEWED,
    EvidenceError, classify_review, run_manifest, sha256_file, write_manifest,
)


class EvidenceBindingTest(unittest.TestCase):
    def test_missing_and_mismatched_files_stay_declared(self):
        empty = classify_review(artifacts=[])
        self.assertEqual(LEVEL_MISSING, empty["evidence_level"])
        self.assertFalse(empty["accepted"])
        missing = classify_review(artifacts=[{"path": "no-such-file.bin", "role": "video"}])
        self.assertEqual(LEVEL_DECLARED, missing["evidence_level"])
        self.assertTrue(missing["missing_paths"])

    def test_matching_digest_is_linked_not_accepted(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "clip.bin"
            path.write_bytes(b"holdout-bytes")
            digest = sha256_file(path)
            linked = classify_review(
                artifacts=[{"path": str(path), "sha256": digest, "role": "video"}])
            self.assertEqual(LEVEL_EVIDENCE_LINKED, linked["evidence_level"])
            self.assertFalse(linked["accepted"])
            reviewed = classify_review(
                artifacts=[{"path": str(path), "sha256": digest, "role": "video"}],
                attested_by="shawn", human_reviewed=True)
            self.assertEqual(LEVEL_REVIEWED, reviewed["evidence_level"])
            self.assertFalse(reviewed["accepted"])
            with self.assertRaises(EvidenceError) as caught:
                classify_review(
                    artifacts=[{"path": str(path), "sha256": digest, "role": "video"}],
                    attested_by="shawn", human_reviewed=True, accepted=True)
            self.assertEqual("AI_CANNOT_ACCEPT", caught.exception.code)

    def test_run_manifest_records_identity_without_accepting(self):
        body = run_manifest(code_commit="3bcd627", dirty_worktree=True,
                            rules_digest="abc", strategy_id="always-stand-v1",
                            seed=1, n_decks=6)
        self.assertFalse(body["accepted"])
        self.assertEqual(LEVEL_MISSING, body["evidence_level"])
        self.assertEqual("3bcd627", body["code_commit"])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "manifest.json"
            write_manifest(path, body)
            self.assertTrue(path.is_file())
