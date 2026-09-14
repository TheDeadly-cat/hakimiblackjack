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
        })
        self.assertFalse(report["independent_video"])
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
        report = compare_holdout({
            "schema": SCHEMA,
            "attestation": _attested("unused_video"),
            "rounds": [{"truth_remaining": [10, 9, 8, 7], "video_id": "holdout-clip-1"}],
        })
        self.assertTrue(report["independent_video"])
        self.assertTrue(report["not_a_reliable_window_claim"])
        self.assertEqual("unused_video", report["source_kind"])
