"""Evaluation-state and confusion-matrix semantics. Not a window claim."""
import unittest

from blackjack_lab.analysis.contracts import AVAILABLE, FAILED, TIMEOUT, UNSUPPORTED
from blackjack_lab.analysis.research_windows import (
    EV_INDETERMINATE, EV_NONPOSITIVE, EV_POSITIVE, EV_UNAVAILABLE, WINDOW_CURRENT_HAND,
    WINDOW_NONPOSITIVE_SUPPORTED, WINDOW_POSITIVE_SUPPORTED, classify_ev_record,
    classify_opening_window, confusion_matrix, evaluation_scope, window_state,
)
from blackjack_lab.analysis.shoe_windows import KIND_FULL_RESHUFFLE, run_independent_shoes
from blackjack_lab.analysis.unused_holdout import SCHEMA, compare_holdout


def _record(status, ev=None):
    return {"status": status, "ev": ev}


class WindowStatSemanticsTest(unittest.TestCase):
    def test_classify_splits_sign_from_unavailable(self):
        self.assertEqual(EV_POSITIVE, classify_ev_record(_record(AVAILABLE, 0.01)))
        self.assertEqual(EV_NONPOSITIVE, classify_ev_record(_record(AVAILABLE, 0.0)))
        self.assertEqual(EV_NONPOSITIVE, classify_ev_record(_record(AVAILABLE, -0.2)))
        self.assertEqual(EV_UNAVAILABLE, classify_ev_record(_record(UNSUPPORTED, None)))
        self.assertEqual(EV_UNAVAILABLE, classify_ev_record(_record(TIMEOUT, None)))
        self.assertEqual(EV_UNAVAILABLE, classify_ev_record(_record(FAILED, None)))
        self.assertEqual(EV_UNAVAILABLE, classify_ev_record(_record("inapplicable", None)))
        self.assertEqual(EV_UNAVAILABLE, classify_ev_record(_record(AVAILABLE, None)))
        self.assertEqual(EV_UNAVAILABLE, classify_ev_record(None))
        self.assertEqual(EV_INDETERMINATE, classify_ev_record({
            "status": AVAILABLE, "ev": 0.04, "window_claim_allowed": False}))
        self.assertEqual(EV_UNAVAILABLE, classify_opening_window({
            "status": AVAILABLE, "ev": 0.7, "window": WINDOW_CURRENT_HAND}))
        self.assertEqual(WINDOW_POSITIVE_SUPPORTED, window_state(_record(AVAILABLE, 0.01)))
        self.assertEqual(WINDOW_NONPOSITIVE_SUPPORTED, window_state(_record(AVAILABLE, 0.0)))
        self.assertEqual("indeterminate", window_state({
            "status": AVAILABLE, "ev": 0.04, "window_claim_allowed": False}))
        self.assertEqual("unavailable", window_state({
            "status": AVAILABLE, "ev": 0.7, "window_kind": WINDOW_CURRENT_HAND}))

    def test_monte_carlo_point_estimate_is_not_a_proven_window(self):
        records = [{
            "status": AVAILABLE, "ev": 0.04, "window_claim_allowed": False,
        } for _ in range(3)]
        scope = evaluation_scope(records)
        self.assertEqual(3, scope["indeterminate"])
        self.assertEqual(0, scope["positive"])
        self.assertFalse(scope["complete_evaluation"])
        self.assertFalse(scope["zero_window"])
        self.assertTrue(scope["incomplete_cannot_claim_zero_window"])

    def test_current_hand_ev_is_not_an_opening_window(self):
        records = [{"status": AVAILABLE, "ev": 0.7, "window": WINDOW_CURRENT_HAND}]
        scope = evaluation_scope(records)
        self.assertEqual(1, scope["unavailable"])
        self.assertEqual(0, scope["positive"])
        self.assertFalse(scope["zero_window"])
        matrix = confusion_matrix([{"truth": records[0], "observer": records[0]}])
        self.assertEqual(1, matrix["truth_unavailable"])
        self.assertEqual(0, matrix["agree_positive"])
        self.assertEqual(0, matrix["false_positive"])

    def test_all_unsupported_is_incomplete_not_a_zero_window(self):
        records = [_record(UNSUPPORTED) for _ in range(5)]
        scope = evaluation_scope(records)
        self.assertEqual(5, scope["n"])
        self.assertEqual(5, scope["unavailable"])
        self.assertEqual(0, scope["positive"])
        self.assertTrue(scope["no_positive_signal_detected"])
        self.assertTrue(scope["incomplete_cannot_claim_zero_window"])
        self.assertFalse(scope["complete_evaluation"])
        self.assertFalse(scope["zero_window"])
        self.assertFalse(scope["no_positive_window_in_complete_evaluation"])
        self.assertEqual(0, scope["evaluated_count"])
        self.assertEqual(5, scope["unassessable_count"])
        self.assertIsNone(scope["verified_no_positive_over_declared_domain"])
        self.assertTrue(scope["no_positive_detected"])

    def test_all_timeout_keeps_the_denominator_and_is_not_zero_window(self):
        records = [_record(TIMEOUT) for _ in range(3)]
        scope = evaluation_scope(records)
        self.assertEqual(3, scope["unavailable"])
        self.assertFalse(scope["zero_window"])
        self.assertTrue(scope["incomplete_cannot_claim_zero_window"])

    def test_complete_nonpositive_evaluation_is_a_zero_window(self):
        records = [_record(AVAILABLE, 0.0), _record(AVAILABLE, -0.1)]
        scope = evaluation_scope(records)
        self.assertTrue(scope["complete_evaluation"])
        self.assertTrue(scope["zero_window"])
        self.assertTrue(scope["no_positive_window_in_complete_evaluation"])
        self.assertTrue(scope["no_positive_signal_detected"])
        self.assertFalse(scope["incomplete_cannot_claim_zero_window"])
        self.assertEqual(2, scope["evaluated_count"])
        self.assertTrue(scope["verified_no_positive_over_declared_domain"])

    def test_mixed_available_and_unsupported_cannot_claim_zero_window(self):
        records = [_record(AVAILABLE, -0.2), _record(UNSUPPORTED)]
        scope = evaluation_scope(records)
        self.assertFalse(scope["zero_window"])
        self.assertTrue(scope["incomplete_cannot_claim_zero_window"])
        self.assertTrue(scope["no_positive_signal_detected"])
        self.assertEqual(1, scope["nonpositive"])
        self.assertEqual(1, scope["unavailable"])

    def test_both_unavailable_is_not_agree_negative(self):
        rows = [{"truth": _record(UNSUPPORTED), "observer": _record(TIMEOUT)}]
        matrix = confusion_matrix(rows)
        self.assertEqual(1, matrix["n"])
        self.assertEqual(1, matrix["denominator_kept"])
        self.assertEqual(1, matrix["truth_unavailable"])
        self.assertEqual(0, matrix["scored"])
        self.assertEqual(0, matrix["agree_negative"])
        self.assertEqual(0, matrix["false_positive"])
        self.assertEqual(0, matrix["false_negative"])

    def test_truth_missing_observer_positive_is_not_false_positive(self):
        rows = [{"truth": _record("inapplicable"), "observer": _record(AVAILABLE, 0.2)}]
        matrix = confusion_matrix(rows)
        self.assertEqual(1, matrix["truth_unavailable"])
        self.assertEqual(0, matrix["false_positive"])
        self.assertEqual(0, matrix["agree_negative"])

    def test_truth_positive_observer_missing_is_missed_unavailable_not_false_negative(self):
        rows = [{"truth": _record(AVAILABLE, 0.2), "observer": _record("inapplicable")}]
        matrix = confusion_matrix(rows)
        self.assertEqual(1, matrix["missed_unavailable"])
        self.assertEqual(1, matrix["missed_due_to_abstention"])
        self.assertEqual(0, matrix["false_negative"])
        self.assertEqual(0, matrix["classified_false_negative"])
        self.assertEqual(0, matrix["agree_negative"])

    def test_evaluable_disagreement_still_scores_fp_and_fn(self):
        rows = [
            {"truth": _record(AVAILABLE, -0.1), "observer": _record(AVAILABLE, 0.2)},
            {"truth": _record(AVAILABLE, 0.3), "observer": _record(AVAILABLE, -0.4)},
            {"truth": _record(AVAILABLE, -0.2), "observer": _record(AVAILABLE, 0.0)},
            {"truth": _record(AVAILABLE, 0.1), "observer": _record(AVAILABLE, 0.2)},
        ]
        matrix = confusion_matrix(rows)
        self.assertEqual(4, matrix["scored"])
        self.assertEqual(1, matrix["false_positive"])
        self.assertEqual(1, matrix["false_negative"])
        self.assertEqual(1, matrix["agree_negative"])
        self.assertEqual(1, matrix["agree_positive"])
        self.assertEqual(4, matrix["n"])
        self.assertEqual(
            matrix["n"],
            matrix["scored"] + matrix["truth_unavailable"] + matrix["missed_unavailable"]
            + matrix["observer_unavailable_on_nonpositive"] + matrix["indeterminate_pairs"])

    def test_independent_full_reshuffle_does_not_inflate_zero_window_rate(self):
        report = run_independent_shoes(
            kind=KIND_FULL_RESHUFFLE, n_decks=6, n_shoes=2, base_seed=1, max_rounds=2,
            surrender=None)
        self.assertEqual(0, report["summary"]["zero_window_shoes"])
        self.assertEqual(0, report["summary"]["complete_shoes"])
        self.assertIsNone(report["summary"]["zero_window_rate"])
        self.assertTrue(report["summary"]["incomplete_cannot_claim_zero_window"])
        self.assertEqual(2, report["summary"]["incomplete_shoes"])
        self.assertEqual(1.0, report["summary"]["incomplete_shoe_rate"])
        self.assertEqual(1.0, report["summary"]["no_positive_signal_rate"])
        self.assertIsNone(report["summary"]["positive_ev_shoe_rate"])

    def test_holdout_observer_gap_on_positive_truth_is_missed_unavailable(self):
        report = compare_holdout({
            "schema": SCHEMA,
            "attestation": {
                "unused_in_training": True,
                "unused_in_threshold_selection": True,
                "unused_in_model_selection": True,
                "human_reviewed_ranks": True,
                "physical_card_ids": True,
                "attested_by": "unit-test",
                "source_kind": "synthetic-fixture",
            },
            "rounds": [{"truth_remaining": [10, 10, 9, 9, 8, 7]}],
        }, surrender="late")
        self.assertGreater(report["summary"]["truth_positive"], 0)
        self.assertEqual(1, report["summary"]["observer"]["missed_unavailable"])
        self.assertEqual(0, report["summary"]["observer"]["false_negative"])
        self.assertEqual(0, report["summary"]["observer"]["agree_negative"])
        self.assertEqual(1, report["summary"]["observer"]["denominator_kept"])

    def test_holdout_unsupported_truth_does_not_call_observer_a_false_positive(self):
        report = compare_holdout({
            "schema": SCHEMA,
            "attestation": {
                "unused_in_training": True,
                "unused_in_threshold_selection": True,
                "unused_in_model_selection": True,
                "human_reviewed_ranks": True,
                "physical_card_ids": True,
                "attested_by": "unit-test",
                "source_kind": "synthetic-fixture",
            },
            "rounds": [{
                "truth_remaining": [10] * 20,
                "observer_remaining": [10, 10, 9, 9, 8, 7],
            }],
        }, surrender=None)
        self.assertEqual(1, report["summary"]["truth_unavailable"])
        self.assertTrue(report["summary"]["incomplete_cannot_claim_zero_window"])
        self.assertFalse(report["summary"]["zero_window_truth"])
        self.assertEqual(1, report["summary"]["observer"]["truth_unavailable"])
        self.assertEqual(0, report["summary"]["observer"]["false_positive"])
        self.assertEqual(0, report["summary"]["observer"]["agree_negative"])
