"""Unused holdout gate: no attestation, no independent-video window claim."""
import unittest

from blackjack_lab.analysis.unused_holdout import SCHEMA, HoldoutError, compare_holdout, load_holdout


def _attested(source_kind="synthetic-fixture"):
    return {
        "unused_in_training": True,
        "unused_in_threshold_selection": True,
        "unused_in_model_selection": True,
        "human_reviewed_ranks": True,
        "physical_card_ids": True,
        "attested_by": "unit-test",
        "source_kind": source_kind,
    }


class UnusedHoldoutTest(unittest.TestCase):
    def test_missing_attestation_is_refused(self):
        with self.assertRaises(HoldoutError) as caught:
            load_holdout({"schema": SCHEMA, "rounds": [{"truth_remaining": [10, 9, 8, 7]}]})
        self.assertEqual("NOT_ATTESTED", caught.exception.code)

    def test_training_material_flag_is_refused(self):
        attestation = _attested()
        attestation["unused_in_training"] = False
        with self.assertRaises(HoldoutError) as caught:
            load_holdout({"schema": SCHEMA, "attestation": attestation,
                          "rounds": [{"truth_remaining": [10, 9, 8, 7]}]})
        self.assertEqual("NOT_UNUSED", caught.exception.code)

    def test_synthetic_fixture_never_counts_as_independent_video(self):
        pack = [10, 10, 9, 9, 8, 7]
        report = compare_holdout({
            "schema": SCHEMA,
            "attestation": _attested("synthetic-fixture"),
            "rounds": [{"truth_remaining": pack, "observer_remaining": [10, 9, 9, 9, 8, 7]}],
        }, surrender=None)
        self.assertIsNone(report["surrender"])
        self.assertFalse(report["independent_video"])
        self.assertFalse(report.get("declared_unused_video"))
        self.assertTrue(report["not_a_reliable_window_claim"])
        self.assertEqual(1, report["summary"]["round_count"])
        self.assertIn("false_negative", report["summary"]["observer"])

    def test_unused_video_without_video_id_is_refused(self):
        with self.assertRaises(HoldoutError) as caught:
            load_holdout({
                "schema": SCHEMA,
                "attestation": _attested("unused_video"),
                "rounds": [{"truth_remaining": [10, 9, 8, 7]}],
            })
        self.assertEqual("VIDEO_ID_MISSING", caught.exception.code)

    def test_attested_unused_video_is_still_not_a_reliable_window(self):
        payload = {
            "schema": SCHEMA,
            "attestation": _attested("unused_video"),
            "rounds": [{"truth_remaining": [10, 9, 8, 7], "video_id": "holdout-clip-1"}],
        }
        loaded = load_holdout(payload)
        self.assertFalse(loaded["independent_video"])
        self.assertTrue(loaded["declared_unused_video"])
        self.assertFalse(loaded["accepted"])
        report = compare_holdout(payload, surrender=None)
        self.assertFalse(report["independent_video"])
        self.assertTrue(report["declared_unused_video"])
        self.assertTrue(report["not_a_reliable_window_claim"])
        self.assertEqual("unused_video", report["source_kind"])
        self.assertEqual("declared", report["evidence_level"])
        self.assertFalse(report["accepted"])
        self.assertEqual(["holdout-clip-1"], report["declared_video_ids"])
        self.assertEqual("holdout-clip-1", report["rounds"][0]["video_id"])
        self.assertEqual("holdout-clip-1", report["identity_chain"][0]["video_id"])
        self.assertIsNone(report["identity_chain"][0]["video_sha256"])
        self.assertIsNone(report["video_binding"])
        self.assertTrue(report["attestation"]["physical_card_ids_flag"])

    def test_video_identity_chain_is_kept_without_certifying_unused_footage(self):
        import hashlib
        import tempfile
        from pathlib import Path

        from blackjack_lab.analysis.evidence import LEVEL_EVIDENCE_LINKED

        with tempfile.TemporaryDirectory() as folder:
            clip = Path(folder) / "holdout.mp4"
            clip.write_bytes(b"not-an-authorized-unused-video")
            digest = hashlib.sha256(clip.read_bytes()).hexdigest()
            payload = {
                "schema": SCHEMA,
                "attestation": _attested("unused_video"),
                "rounds": [{
                    "truth_remaining": [10, 9, 8, 7],
                    "video_id": "holdout-clip-1",
                    "video_path": str(clip),
                    "video_sha256": digest,
                    "physical_card_ids": ["card-a", "card-b"],
                }],
            }
            report = compare_holdout(payload, surrender=None)
        self.assertEqual(LEVEL_EVIDENCE_LINKED, report["evidence_level"])
        self.assertFalse(report["independent_video"])
        self.assertFalse(report["accepted"])
        chain = report["identity_chain"][0]
        self.assertEqual("holdout-clip-1", chain["video_id"])
        self.assertEqual(digest, chain["video_sha256"])
        self.assertEqual(["card-a", "card-b"], chain["physical_card_ids"])
        self.assertEqual(digest, report["rounds"][0]["video_sha256"])
        self.assertEqual(["card-a", "card-b"], report["rounds"][0]["physical_card_ids"])

    def test_digest_mismatch_stays_declared_not_independent(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as folder:
            clip = Path(folder) / "holdout.mp4"
            clip.write_bytes(b"bytes")
            payload = {
                "schema": SCHEMA,
                "attestation": _attested("unused_video"),
                "rounds": [{
                    "truth_remaining": [10, 9, 8, 7],
                    "video_id": "holdout-clip-1",
                    "video_path": str(clip),
                    "video_sha256": "0" * 64,
                }],
            }
            report = compare_holdout(payload, surrender=None)
        self.assertEqual("declared", report["evidence_level"])
        self.assertFalse(report["independent_video"])
        self.assertTrue(report["video_binding"]["digest_mismatch"])

    def test_invalid_video_sha_is_refused(self):
        with self.assertRaises(HoldoutError) as caught:
            load_holdout({
                "schema": SCHEMA,
                "attestation": _attested("unused_video"),
                "rounds": [{"truth_remaining": [10, 9, 8, 7], "video_id": "x",
                            "video_sha256": "not-a-digest"}],
            })
        self.assertEqual("VIDEO_SHA_INVALID", caught.exception.code)

    def test_report_script_never_certifies_independent_video(self):
        import json
        import tempfile
        from pathlib import Path

        from scripts.report_unattested_materials import classify

        payload = {
            "schema": SCHEMA,
            "attestation": _attested("unused_video"),
            "rounds": [{"truth_remaining": [10, 9, 8, 7], "video_id": "holdout-clip-1"}],
        }
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "holdout.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            classified = classify(path)
        self.assertEqual("attested_package", classified["status"])
        self.assertTrue(classified["declared_unused_video"])
        self.assertFalse(classified["independent_video"])

    def test_report_script_does_not_honor_hand_edited_accepted_flags(self):
        import json
        import tempfile
        from pathlib import Path

        from scripts.report_unattested_materials import classify

        with tempfile.TemporaryDirectory() as folder:
            operator = Path(folder) / "operator.json"
            operator.write_text(json.dumps({
                "schema": "hakimi-operator-study-v1",
                "accepted": True,
                "paired": True,
                "identity_chain": [{"operator_id": "a", "video_id": "v1", "pair_id": "p1"}],
            }), encoding="utf-8")
            classified = classify(operator)
        self.assertEqual("operator_study_declared", classified["status"])
        self.assertFalse(classified["accepted"])
        self.assertFalse(classified["paired"])
        self.assertFalse(classified["independent_video"])
        self.assertTrue(classified["hand_edited_accepted"])
        self.assertEqual("a", classified["identity_chain"][0]["operator_id"])

    def test_float_remaining_is_rejected(self):
        with self.assertRaises(HoldoutError) as caught:
            load_holdout({
                "schema": SCHEMA,
                "attestation": _attested(),
                "rounds": [{"truth_remaining": [1.8, 10, 10, 10]}],
            })
        self.assertEqual("TRUTH_INVALID", caught.exception.code)

    def test_compare_holdout_requires_declared_surrender(self):
        with self.assertRaises(ValueError) as caught:
            compare_holdout({
                "schema": SCHEMA,
                "attestation": _attested("synthetic-fixture"),
                "rounds": [{"truth_remaining": [10, 10, 9, 9, 8, 7]}],
            })
        self.assertIn("不能默认晚投降", str(caught.exception))
