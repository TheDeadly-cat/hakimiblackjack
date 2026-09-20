"""Original review-handoff gate: required test identity, not an exact method count."""
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import verify_review_handoff as handoff


MATH_OUTPUT_WITH_EXTRA = """\
test_hidden_card_oracle_cannot_inflate_continuation_ev (tests.test_analysis_math.TestIndependentMath.test_hidden_card_oracle_cannot_inflate_continuation_ev) ... ok
test_hit_includes_repeated_decisions_not_forced_stand (tests.test_analysis_math.TestIndependentMath.test_hit_includes_repeated_decisions_not_forced_stand) ... ok
test_peek_changes_both_hole_and_next_card_distribution (tests.test_analysis_math.TestIndependentMath.test_peek_changes_both_hole_and_next_card_distribution) ... ok
test_small_physical_worlds_match_all_outcomes (tests.test_analysis_math.TestIndependentMath.test_small_physical_worlds_match_all_outcomes) ... ok
test_terminal_twenty_vs_twenty_does_not_need_a_draw (tests.test_analysis_math.TestIndependentMath.test_terminal_twenty_vs_twenty_does_not_need_a_draw) ... ok
test_three_deck_counts_actually_change_probabilities_and_ev (tests.test_analysis_math.TestIndependentMath.test_three_deck_counts_actually_change_probabilities_and_ev) ... ok

----------------------------------------------------------------------
Ran 6 tests in 0.094s

OK
"""

HANDOFF_OUTPUT = """\
test_cancel_clears_scheduled_auto_request (test_v02a_review_regressions.TestAnalysisLifecycleReview.test_cancel_clears_scheduled_auto_request) ... ok
test_committed_input_invalidates_inflight_request_even_if_render_fails (test_v02a_review_regressions.TestAnalysisLifecycleReview.test_committed_input_invalidates_inflight_request_even_if_render_fails) ... ok
test_committed_input_removes_displayed_result_even_if_render_fails (test_v02a_review_regressions.TestAnalysisLifecycleReview.test_committed_input_removes_displayed_result_even_if_render_fails) ... ok
test_missing_initial_card_in_unsettled_round_blocks_next_round_analysis (test_v02a_review_regressions.TestRoundIntegrityReview.test_missing_initial_card_in_unsettled_round_blocks_next_round_analysis) ... ok

----------------------------------------------------------------------
Ran 4 tests in 3.480s

OK
"""

PADDED_MATH_OUTPUT = """\
test_small_physical_worlds_match_all_outcomes (tests.test_analysis_math.TestIndependentMath.test_small_physical_worlds_match_all_outcomes) ... ok
test_peek_changes_both_hole_and_next_card_distribution (tests.test_analysis_math.TestIndependentMath.test_peek_changes_both_hole_and_next_card_distribution) ... ok
test_hidden_card_oracle_cannot_inflate_continuation_ev (tests.test_analysis_math.TestIndependentMath.test_hidden_card_oracle_cannot_inflate_continuation_ev) ... ok
test_three_deck_counts_actually_change_probabilities_and_ev (tests.test_analysis_math.TestIndependentMath.test_three_deck_counts_actually_change_probabilities_and_ev) ... ok
test_padding_keeps_the_old_count (tests.test_analysis_math.TestIndependentMath.test_padding_keeps_the_old_count) ... ok

----------------------------------------------------------------------
Ran 5 tests in 0.010s

OK
"""

SKIPPED_REQUIRED_MATH_OUTPUT = """\
test_small_physical_worlds_match_all_outcomes (tests.test_analysis_math.TestIndependentMath.test_small_physical_worlds_match_all_outcomes) ... skipped 'not counted as pass'
test_peek_changes_both_hole_and_next_card_distribution (tests.test_analysis_math.TestIndependentMath.test_peek_changes_both_hole_and_next_card_distribution) ... ok
test_hidden_card_oracle_cannot_inflate_continuation_ev (tests.test_analysis_math.TestIndependentMath.test_hidden_card_oracle_cannot_inflate_continuation_ev) ... ok
test_three_deck_counts_actually_change_probabilities_and_ev (tests.test_analysis_math.TestIndependentMath.test_three_deck_counts_actually_change_probabilities_and_ev) ... ok
test_hit_includes_repeated_decisions_not_forced_stand (tests.test_analysis_math.TestIndependentMath.test_hit_includes_repeated_decisions_not_forced_stand) ... ok

----------------------------------------------------------------------
Ran 5 tests in 0.010s

OK (skipped=1)
"""


def _passing_checks(math_count=6):
    return [
        {"name": "original-math", "exit_code": 0, "test_count": math_count},
        {"name": "original-48-scenarios", "exit_code": 0},
        {"name": "original-handoff", "exit_code": 0, "test_count": 4},
    ]


def _passing_result():
    return {"scenario_count": 48, "passed_count": 48, "max_abs_error": 2.220446049250313e-16}


def _handoff_ok():
    parsed = handoff.parse_unittest_methods(HANDOFF_OUTPUT)
    failures, additional, passed = handoff.required_method_failures(
        "required_handoff_method", handoff.REQUIRED_HANDOFF_METHODS, parsed,
        set(handoff.REQUIRED_HANDOFF_METHODS))
    return failures, additional, passed


class TestReviewHandoffIdentityGate(unittest.TestCase):
    def test_parser_reads_required_and_additional_math_methods(self):
        parsed = handoff.parse_unittest_methods(MATH_OUTPUT_WITH_EXTRA)
        names = [item["name"] for item in parsed]
        self.assertEqual(set(item["status"] for item in parsed), {"ok"})
        for name in handoff.REQUIRED_MATH_METHODS:
            self.assertIn(name, names)
        self.assertIn("test_terminal_twenty_vs_twenty_does_not_need_a_draw", names)
        self.assertEqual(len(parsed), 6)

    def test_live_sources_still_declare_the_original_required_methods(self):
        math_declared = handoff.declared_test_method_names(handoff.MATH_SOURCE)
        handoff_declared = handoff.declared_test_method_names(handoff.HANDOFF_SOURCE)
        for name in handoff.REQUIRED_MATH_METHODS:
            self.assertIn(name, math_declared)
        for name in handoff.REQUIRED_HANDOFF_METHODS:
            self.assertIn(name, handoff_declared)
        self.assertIn("test_terminal_twenty_vs_twenty_does_not_need_a_draw", math_declared)

    def test_additional_math_method_does_not_fail_the_identity_contract(self):
        parsed = handoff.parse_unittest_methods(MATH_OUTPUT_WITH_EXTRA)
        declared = {item["name"] for item in parsed}
        math_failures, additional, passed = handoff.required_method_failures(
            "required_math_method", handoff.REQUIRED_MATH_METHODS, parsed, declared)
        handoff_failures, _, _ = _handoff_ok()
        failed = handoff.evaluate_handoff_conditions(
            _passing_checks(6), True, _passing_result(), True, math_failures, handoff_failures)
        self.assertEqual(math_failures, [])
        self.assertEqual(list(handoff.REQUIRED_MATH_METHODS), passed)
        self.assertEqual(additional, ["test_terminal_twenty_vs_twenty_does_not_need_a_draw"])
        self.assertEqual(failed, [])

    def test_deleting_a_required_math_method_fails_even_if_count_is_padded_to_five(self):
        parsed = handoff.parse_unittest_methods(PADDED_MATH_OUTPUT)
        declared = {item["name"] for item in parsed}
        self.assertEqual(len(parsed), 5)
        self.assertNotIn("test_hit_includes_repeated_decisions_not_forced_stand", declared)
        math_failures, additional, passed = handoff.required_method_failures(
            "required_math_method", handoff.REQUIRED_MATH_METHODS, parsed, declared)
        handoff_failures, _, _ = _handoff_ok()
        failed = handoff.evaluate_handoff_conditions(
            _passing_checks(5), True, _passing_result(), True, math_failures, handoff_failures)
        self.assertEqual(
            math_failures,
            ["required_math_method_missing:test_hit_includes_repeated_decisions_not_forced_stand"],
        )
        self.assertIn(
            "required_math_method_missing:test_hit_includes_repeated_decisions_not_forced_stand",
            failed,
        )
        self.assertEqual(additional, ["test_padding_keeps_the_old_count"])
        self.assertNotIn("test_hit_includes_repeated_decisions_not_forced_stand", passed)

    def test_skipped_required_math_method_is_not_a_pass(self):
        parsed = handoff.parse_unittest_methods(SKIPPED_REQUIRED_MATH_OUTPUT)
        declared = set(handoff.REQUIRED_MATH_METHODS)
        math_failures, additional, passed = handoff.required_method_failures(
            "required_math_method", handoff.REQUIRED_MATH_METHODS, parsed, declared)
        handoff_failures, _, _ = _handoff_ok()
        failed = handoff.evaluate_handoff_conditions(
            _passing_checks(5), True, _passing_result(), True, math_failures, handoff_failures)
        self.assertEqual(
            math_failures,
            ["required_math_method_not_passed:test_small_physical_worlds_match_all_outcomes:skipped"],
        )
        self.assertIn(
            "required_math_method_not_passed:test_small_physical_worlds_match_all_outcomes:skipped",
            failed,
        )
        self.assertEqual(additional, [])
        self.assertNotIn("test_small_physical_worlds_match_all_outcomes", passed)

    def test_receipt_lists_numeric_and_identity_failures_separately(self):
        parsed = handoff.parse_unittest_methods(MATH_OUTPUT_WITH_EXTRA)
        math_failures, _, _ = handoff.required_method_failures(
            "required_math_method", handoff.REQUIRED_MATH_METHODS, parsed,
            {item["name"] for item in parsed})
        failed = handoff.evaluate_handoff_conditions(
            [
                {"name": "original-math", "exit_code": 0, "test_count": 6},
                {"name": "original-48-scenarios", "exit_code": 0},
                {"name": "original-handoff", "exit_code": 1, "test_count": 4},
            ],
            False,
            {"scenario_count": 47, "passed_count": 47, "max_abs_error": 2e-9},
            False,
            math_failures,
            ["required_handoff_method_not_passed:test_cancel_clears_scheduled_auto_request:FAIL"],
        )
        self.assertEqual(failed, [
            "original_handoff_exit_0",
            "required_handoff_method_not_passed:test_cancel_clears_scheduled_auto_request:FAIL",
            "same_48_inputs_as_original",
            "scenario_count_48",
            "passed_count_48",
            "max_abs_error_at_most_1e-10",
            "source_unchanged_during_checks",
        ])

    def test_artifact_manifest_is_a_whitelist_not_the_evidence_tree(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            (output / "receipt.json").write_text("{}", encoding="utf-8")
            (output / "original-math.txt").write_text("ok", encoding="utf-8")
            (output / "secret-video.bin").write_text("private", encoding="utf-8")
            extra = output / "private-media"
            extra.mkdir()
            (extra / "table.mp4").write_text("no", encoding="utf-8")
            manifest = handoff.write_artifact_manifest(output)
            listed = {item["path"] for item in manifest["files"]}
            self.assertIn("receipt.json", listed)
            self.assertIn("original-math.txt", listed)
            self.assertNotIn("secret-video.bin", listed)
            self.assertNotIn("private-media/table.mp4", listed)
            self.assertNotIn("artifact-manifest.json", listed)
            saved = json.loads((output / "artifact-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["whitelist"], list(handoff.ARTIFACT_WHITELIST))


class TestCurrentCheckoutHandoff(unittest.TestCase):
    def test_current_checkout_passes_identity_gate_with_extra_math_method(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "handoff"
            with patch.object(sys, "argv", ["verify_review_handoff.py", "--output", str(output)]):
                with patch("sys.stdout", io.StringIO()):
                    code = handoff.main()
            receipt = json.loads((output / "receipt.json").read_text(encoding="utf-8"))
            manifest = json.loads((output / "artifact-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(code, 0, receipt.get("failed_conditions"))
        self.assertEqual(receipt["failed_conditions"], [])
        self.assertTrue(receipt["passed"])
        self.assertEqual(receipt["checks"][0]["test_count"], 6)
        self.assertEqual(receipt["required_math_passed"], list(handoff.REQUIRED_MATH_METHODS))
        self.assertEqual(
            receipt["additional_math_methods"],
            ["test_terminal_twenty_vs_twenty_does_not_need_a_draw"],
        )
        self.assertEqual(receipt["required_handoff_passed"], list(handoff.REQUIRED_HANDOFF_METHODS))
        self.assertEqual(receipt["current_max_abs_error"], 2.220446049250313e-16)
        listed = {item["path"] for item in manifest["files"]}
        self.assertEqual(listed, {
            "receipt.json",
            "original-math.txt",
            "original-48-scenarios.txt",
            "original-handoff.txt",
            "supplementary/supplementary-math-rerun.json",
        })


if __name__ == "__main__":
    unittest.main()
