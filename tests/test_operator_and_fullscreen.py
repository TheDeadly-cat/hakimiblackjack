"""Operator-efficiency and fullscreen acceptance stay failed without human evidence."""
import json
import tempfile
import unittest
from pathlib import Path

from blackjack_lab.capture.fullscreen_acceptance import accepted, empty_evidence, probe_environment, report
from blackjack_lab.observation.operator_study import conclude, trial, trial_from_usage, write_export


class OperatorStudyTest(unittest.TestCase):
    def test_empty_and_synthetic_runs_do_not_change_auto_prompt_default(self):
        empty = conclude([])
        self.assertEqual("NEED_PAIRED_HUMAN_TRIALS", empty["reason_code"])
        self.assertIs(empty["auto_prompt_default"], False)
        synthetic = trial("assisted", human_run=False, elapsed_seconds=10, keystrokes=4,
                          clicks=2, backlog_peak=0, missed_cards=0, duplicates=0,
                          repair_seconds=0, pause_reconcile_not_realtime=True)
        result = conclude([synthetic])
        self.assertEqual("SYNTHETIC_NOT_HUMAN", result["reason_code"])
        self.assertIs(result["auto_prompt_default"], False)

    def test_pause_reconcile_must_be_declared_not_realtime(self):
        with self.assertRaises(ValueError):
            trial("manual", human_run=True, elapsed_seconds=1, keystrokes=1, clicks=1,
                  backlog_peak=0, missed_cards=0, duplicates=0, repair_seconds=0,
                  pause_reconcile_not_realtime=False)

    def test_human_pairs_still_keep_optional_auto_prompt(self):
        metrics = dict(elapsed_seconds=20, keystrokes=8, clicks=5, backlog_peak=1,
                       missed_cards=0, duplicates=0, repair_seconds=3,
                       pause_reconcile_not_realtime=True)
        result = conclude([
            trial("manual", human_run=True, **metrics),
            trial("assisted", human_run=True, **metrics),
        ])
        self.assertEqual("HUMAN_PAIRS_PRESENT_DEFAULT_UNCHANGED", result["reason_code"])
        self.assertIs(result["auto_prompt_default"], False)

    def test_panel_usage_counters_do_not_attest_a_human_study(self):
        usage = {"started_at": 100.0, "key_presses": 3, "mouse_clicks": 2, "max_pending": 4,
                 "operator": "not_attested"}
        recorded = trial_from_usage("assisted", usage, human_run=False, missed_cards=1,
                                    duplicates=0, repair_seconds=2, now=110.0)
        self.assertEqual(recorded["elapsed_seconds"], 10.0)
        self.assertEqual(recorded["keystrokes"], 3)
        self.assertIs(conclude([recorded])["auto_prompt_default"], False)

    def test_write_export_never_flips_auto_prompt_default(self):
        recorded = trial("manual", human_run=True, elapsed_seconds=12, keystrokes=4,
                         clicks=2, backlog_peak=1, missed_cards=0, duplicates=0,
                         repair_seconds=0, pause_reconcile_not_realtime=True)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "operator.json"
            body = write_export(path, [recorded])
            saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertFalse(body["auto_prompt_default"])
        self.assertFalse(saved["auto_prompt_default"])
        self.assertEqual("NEED_PAIRED_HUMAN_TRIALS", saved["reason_code"])


class FullscreenAcceptanceTest(unittest.TestCase):
    def test_empty_evidence_is_not_acceptance(self):
        self.assertFalse(accepted())
        self.assertFalse(accepted(empty_evidence()))
        body = report()
        self.assertFalse(body["accepted"])
        self.assertIn("源帧", body["note"])
        env = probe_environment()
        self.assertTrue(env["not_acceptance"])
        self.assertFalse(report(environment=env)["accepted"])

    def test_overlay_pixel_fail_does_not_accept_fullscreen(self):
        from blackjack_lab.capture.overlay_exclusion import source_frame_excludes_overlay
        item = source_frame_excludes_overlay(
            [[(12, 43, 20)]], felt_bgr=(40, 90, 30), overlay_bgr=(12, 43, 20))
        evidence = empty_evidence()
        evidence["source_frame_excludes_overlay"] = {
            "passed": item["passed"], "evidence_path": "unit-fixture", "notes": item["note"]}
        self.assertFalse(item["passed"])
        self.assertFalse(accepted(evidence))

    def test_synthetic_pixel_probe_cannot_pass_fullscreen(self):
        from blackjack_lab.capture.fullscreen_acceptance import attach_source_frame_probe
        felt = (40, 90, 30)
        overlay = (0x3A, 0x2B, 0x14)
        evidence = attach_source_frame_probe(
            empty_evidence(), [[felt] * 4] * 3, felt_bgr=felt, overlay_bgr=overlay,
            evidence_path="unit-fixture", source_is_browser_capture=False)
        self.assertFalse(evidence["source_frame_excludes_overlay"]["passed"])
        env = probe_environment()
        self.assertTrue(env["not_acceptance"])
        self.assertFalse(report(evidence, env)["accepted"])
