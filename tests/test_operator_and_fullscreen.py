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
        self.assertFalse(result["accepted"])
        self.assertFalse(result["paired"])
        self.assertFalse(result["declared_pair_ids"])
        self.assertEqual("declared", result["evidence_level"])

    def test_mismatched_operator_video_is_not_a_paired_trial(self):
        metrics = dict(elapsed_seconds=20, keystrokes=8, clicks=5, backlog_peak=1,
                       missed_cards=0, duplicates=0, repair_seconds=3,
                       pause_reconcile_not_realtime=True)
        result = conclude([
            trial("manual", human_run=True, **metrics),
            trial("assisted", human_run=True, **metrics),
        ])
        self.assertFalse(result["paired"])
        mismatched = conclude([
            {**trial("manual", human_run=True, **metrics),
             "operator_id": "a", "video_id": "v1", "pair_id": "p1"},
            {**trial("assisted", human_run=True, **metrics),
             "operator_id": "b", "video_id": "v2", "pair_id": "p2"},
        ])
        self.assertEqual("UNPAIRED_OPERATOR_VIDEO", mismatched["reason_code"])
        self.assertFalse(mismatched["accepted"])
        self.assertFalse(mismatched["paired"])
        self.assertFalse(mismatched["declared_pair_ids"])

    def test_matching_pair_ids_are_declared_not_certified(self):
        metrics = dict(elapsed_seconds=20, keystrokes=8, clicks=5, backlog_peak=1,
                       missed_cards=0, duplicates=0, repair_seconds=3,
                       pause_reconcile_not_realtime=True)
        matched = conclude([
            {**trial("manual", human_run=True, **metrics),
             "operator_id": "a", "video_id": "v1", "pair_id": "p1"},
            {**trial("assisted", human_run=True, **metrics),
             "operator_id": "a", "video_id": "v1", "pair_id": "p1"},
        ])
        self.assertTrue(matched["declared_pair_ids"])
        self.assertFalse(matched["paired"])
        self.assertFalse(matched["accepted"])
        self.assertIs(matched["auto_prompt_default"], False)
        self.assertEqual("declared", matched["evidence_level"])
        self.assertEqual("HUMAN_PAIRS_PRESENT_DEFAULT_UNCHANGED", matched["reason_code"])
        self.assertEqual(2, len(matched["identity_chain"]))
        self.assertEqual("a", matched["identity_chain"][0]["operator_id"])
        self.assertEqual("v1", matched["identity_chain"][0]["video_id"])
        self.assertEqual("p1", matched["identity_chain"][0]["pair_id"])

    def test_trial_keeps_pair_identity_without_certifying_pairing(self):
        metrics = dict(elapsed_seconds=20, keystrokes=8, clicks=5, backlog_peak=1,
                       missed_cards=0, duplicates=0, repair_seconds=3,
                       pause_reconcile_not_realtime=True, operator_id="a",
                       video_id="v1", pair_id="p1")
        recorded = trial("manual", human_run=True, **metrics)
        self.assertEqual("a", recorded["operator_id"])
        self.assertEqual("v1", recorded["video_id"])
        self.assertEqual("p1", recorded["pair_id"])
        with self.assertRaises(ValueError):
            trial("manual", human_run=True, elapsed_seconds=1, keystrokes=1, clicks=1,
                  backlog_peak=0, missed_cards=0, duplicates=0, repair_seconds=0,
                  pause_reconcile_not_realtime=True, operator_id=" ")
        usage = {"started_at": 100.0, "key_presses": 3, "mouse_clicks": 2, "max_pending": 4,
                 "operator_id": "a", "video_id": "v1", "pair_id": "p1"}
        from_usage = trial_from_usage(
            "assisted", usage, human_run=True, missed_cards=0, duplicates=0,
            repair_seconds=0, now=110.0, pause_reconcile_not_realtime=True)
        self.assertEqual("a", from_usage["operator_id"])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "operator.json"
            body = write_export(path, [recorded, from_usage])
            saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertFalse(body["paired"])
        self.assertFalse(body["accepted"])
        self.assertTrue(body["declared_pair_ids"])
        self.assertEqual("p1", saved["identity_chain"][1]["pair_id"])
        self.assertEqual("a", saved["trials"][0]["operator_id"])
        from blackjack_lab.observation.operator_study import optional_identity_fields
        self.assertEqual(
            {"operator_id": "a", "pair_id": "p1"},
            optional_identity_fields(operator_id=" a ", video_id="  ", pair_id="p1"))
        overlay = trial_from_usage(
            "assisted", {"started_at": 1.0, "key_presses": 1, "mouse_clicks": 1, "max_pending": 0},
            human_run=True, missed_cards=0, duplicates=0, repair_seconds=0, now=2.0,
            pause_reconcile_not_realtime=True, operator_id="z", video_id="clip", pair_id="pair-9")
        self.assertEqual("z", overlay["operator_id"])
        self.assertEqual("clip", overlay["video_id"])

    def test_panel_usage_counters_do_not_attest_a_human_study(self):
        usage = {"started_at": 100.0, "key_presses": 3, "mouse_clicks": 2, "max_pending": 4,
                 "operator": "not_attested"}
        recorded = trial_from_usage("assisted", usage, human_run=False, missed_cards=1,
                                    duplicates=0, repair_seconds=2, now=110.0,
                                    pause_reconcile_not_realtime=True)
        self.assertEqual(recorded["elapsed_seconds"], 10.0)
        self.assertEqual(recorded["keystrokes"], 3)
        self.assertIs(conclude([recorded])["auto_prompt_default"], False)

    def test_usage_export_requires_explicit_pause_declaration(self):
        usage = {"started_at": 100.0, "key_presses": 1, "mouse_clicks": 1, "max_pending": 0}
        with self.assertRaises(ValueError) as caught:
            trial_from_usage("assisted", usage, human_run=True, missed_cards=0,
                             duplicates=0, repair_seconds=0)
        self.assertIn("暂停后的对账成功不算实时跟上", str(caught.exception))

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
        self.assertFalse(evidence["source_frame_excludes_overlay"]["probe_passed"])
        env = probe_environment()
        self.assertTrue(env["not_acceptance"])
        self.assertFalse(report(evidence, env)["accepted"])

    def test_browser_capture_probe_cannot_pass_f11_item(self):
        from blackjack_lab.capture.fullscreen_acceptance import attach_source_frame_probe
        felt = (40, 90, 30)
        overlay = (0x3A, 0x2B, 0x14)
        evidence = attach_source_frame_probe(
            empty_evidence(), [[felt] * 4] * 3, felt_bgr=felt, overlay_bgr=overlay,
            evidence_path="unit-fixture", source_is_browser_capture=True)
        self.assertTrue(evidence["source_frame_excludes_overlay"]["probe_passed"])
        self.assertFalse(evidence["source_frame_excludes_overlay"]["passed"])
        self.assertFalse(accepted(evidence))
        self.assertFalse(report(evidence)["accepted"])

    def test_missing_evidence_file_cannot_accept(self):
        evidence = empty_evidence()
        for key in evidence:
            evidence[key] = {"passed": True, "evidence_path": "missing-fullscreen-evidence.bin"}
        self.assertFalse(accepted(evidence))
        self.assertEqual("declared", report(evidence)["evidence_level"])

    def test_real_files_and_checkboxes_cannot_accept_f11(self):
        from blackjack_lab.capture.fullscreen_acceptance import files_linked
        with tempfile.TemporaryDirectory() as folder:
            evidence = empty_evidence()
            for index, key in enumerate(evidence):
                path = Path(folder) / f"item-{index}.bin"
                path.write_bytes(b"not-a-browser-f11-frame")
                evidence[key] = {"passed": True, "evidence_path": str(path)}
            self.assertTrue(files_linked(evidence))
            self.assertFalse(accepted(evidence))
            body = report(evidence)
            self.assertFalse(body["accepted"])
            self.assertEqual("evidence-linked", body["evidence_level"])
            self.assertTrue(all(not item["passed"] for item in body["evidence"].values()))
            self.assertEqual(len(evidence), len(body["identity_chain"]))
            self.assertTrue(all(item["sha256"] for item in body["identity_chain"]))

    def test_digest_mismatch_stays_declared_not_accepted(self):
        with tempfile.TemporaryDirectory() as folder:
            evidence = empty_evidence()
            for index, key in enumerate(evidence):
                path = Path(folder) / f"item-{index}.bin"
                path.write_bytes(b"fullscreen-bytes")
                evidence[key] = {
                    "passed": True, "evidence_path": str(path),
                    "sha256": "0" * 64,
                }
            body = report(evidence)
            self.assertFalse(body["accepted"])
            self.assertEqual("declared", body["evidence_level"])
            self.assertTrue(all(item.get("digest_mismatch") for item in body["evidence"].values()))
            self.assertFalse(all(item["passed"] for item in body["evidence"].values()))
