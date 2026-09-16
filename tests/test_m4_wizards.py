"""F11/operator wizards, rule diff, and shoe event drafts stay unaccepted."""
import json
import tempfile
import unittest
from pathlib import Path

from blackjack_lab.analysis.shoe_event_draft import (
    add_event, composition_status, confirm_events, confirm_page, correct_event,
    development_clip_stub, keep_unknown, review_pages, set_event_rank, write_draft,
)
from blackjack_lab.capture.fullscreen_acceptance import accepted
from blackjack_lab.capture.fullscreen_wizard import (
    as_fullscreen_evidence, record_step, skip_untested, start_session,
)
from blackjack_lab.core.table_rules_diff import from_felt_observation, write_diff
from blackjack_lab.observation.operator_wizard import (
    allocate_pair_identity, export_session, record_leg, start_session as start_operator,
)


class FullscreenWizardTest(unittest.TestCase):
    def test_required_steps_do_not_accept_f11(self):
        session = start_session(environment={"not_acceptance": True, "monitor_count": 1})
        for step_id in (
            "enter_f11", "invoke_panel", "enter_two_cards", "correct_one_card",
            "return_to_browser", "interrupt_source",
        ):
            record_step(session, step_id, notes="unit")
        skip_untested(session, "dpi_variants", reason="单屏未测")
        skip_untested(session, "multi_monitor", reason="没有第二块显示器")
        self.assertTrue(session["required_complete"])
        self.assertEqual(["dpi_variants", "multi_monitor"], session["untested_optional"])
        self.assertFalse(session["accepted"])
        self.assertTrue(session["ready_for_human_fullscreen_signoff"])
        body = as_fullscreen_evidence(session)
        self.assertFalse(body["accepted"])
        self.assertFalse(accepted(body["evidence"]))
        self.assertTrue(all(not item["passed"] for item in body["evidence"].values()))

    def test_required_step_cannot_be_marked_untested(self):
        session = start_session()
        with self.assertRaises(ValueError):
            skip_untested(session, "enter_f11", reason="skip")


class OperatorWizardTest(unittest.TestCase):
    def test_software_mints_ids_and_does_not_certify_pairing(self):
        first = allocate_pair_identity(operator_name="Shawn", video_label="12.58.11.02")
        second = allocate_pair_identity(operator_name="Shawn", video_label="12.58.11.02")
        self.assertNotEqual(first["pair_id"], second["pair_id"])
        self.assertTrue(first["user_must_not_hand_craft_ids"])
        self.assertEqual("software", first["generated_by"])
        session = start_operator(identity=first)
        metrics = dict(elapsed_seconds=12, keystrokes=4, clicks=2, backlog_peak=1,
                       missed_cards=0, duplicates=0, repair_seconds=3)
        record_leg(session, "manual", human_run=True, **metrics)
        record_leg(session, "assisted", human_run=True, **metrics)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "pair.json"
            body = export_session(session, path)
            saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(body["declared_pair_ids"])
        self.assertFalse(body["accepted"])
        self.assertFalse(body["paired"])
        self.assertFalse(body["auto_prompt_default"])
        self.assertEqual(first["pair_id"], saved["trials"][0]["pair_id"])
        self.assertEqual(first["pair_id"], saved["trials"][1]["pair_id"])
        self.assertEqual(["manual", "assisted"], [item["condition"] for item in saved["trials"]])


class TableRulesDiffTest(unittest.TestCase):
    def test_felt_observation_does_not_default_decks_or_evolution(self):
        obs = {
            "provider_on_screen": "Pragmatic Play Live",
            "table_label_on_screen": "Stake 二十一点7",
            "url_on_screen": "stake.com/example",
            "visible_felt_text": [
                "BLACKJACK PAYS 3 TO 2",
                "Dealer must draw to 16 and stand on all 17s",
                "INSURANCE PAYS 2 TO 1",
            ],
            "not_visible": [
                "n_decks", "surrender", "double_after_split",
                "american_hole_card", "peek_timing", "cut_card_remaining",
            ],
        }
        body = from_felt_observation(obs)
        self.assertFalse(body["accepted"])
        self.assertFalse(body["applies_to_live_table"])
        self.assertIsNone(body["n_decks"])
        self.assertTrue(body["not_evolution"])
        by_id = {row["id"]: row for row in body["rows"]}
        self.assertEqual("visible_on_felt", by_id["game_name_variant"]["status"])
        self.assertEqual("visible_on_felt", by_id["s17_h17_bj_payout"]["status"])
        self.assertEqual("public_help_unconfirmed", by_id["n_decks_removed_ranks"]["status"])
        self.assertIn("n_decks_removed_ranks", body["uncertain"])
        self.assertFalse(by_id["n_decks_removed_ranks"]["defaulted"])
        self.assertTrue(by_id["n_decks_removed_ranks"]["needs_user"])
        self.assertEqual(8, body["public_help_candidate_n_decks"])
        self.assertFalse(body["public_help_applies_to_this_table"])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "diff.json"
            saved = write_diff(path, body)
            self.assertFalse(saved["accepted"])
            self.assertIsNone(json.loads(path.read_text(encoding="utf-8"))["n_decks"])
            self.assertFalse(by_id["game_name_variant"]["needs_user"])

    def test_printed_felt_observation_does_not_invent_decks_or_table_number(self):
        from blackjack_lab.core.table_rules_diff import printed_felt_observation
        obs = printed_felt_observation(
            still_path="felt-046080.png",
            source_video="Desktop 2026.09.12 - 12.58.11.02.mp4")
        body = from_felt_observation(obs)
        self.assertFalse(body["accepted"])
        self.assertFalse(body["applies_to_live_table"])
        self.assertIsNone(body["n_decks"])
        self.assertIsNone(body["table_label_on_screen"])
        self.assertTrue(body["not_evolution"])
        by_id = {row["id"]: row for row in body["rows"]}
        self.assertEqual("visible_on_felt", by_id["s17_h17_bj_payout"]["status"])
        self.assertTrue(by_id["game_name_variant"]["needs_user"])
        self.assertEqual("public_help_unconfirmed", by_id["n_decks_removed_ranks"]["status"])
        self.assertIn("BJ 3:2", by_id["s17_h17_bj_payout"]["value"])
        self.assertIn("S17", by_id["s17_h17_bj_payout"]["value"])
        self.assertEqual("conflict_in_public_help", by_id["hole_card_peek"]["status"])
        self.assertEqual("public_help_unconfirmed", by_id["double_split_das_surrender"]["status"])
        self.assertTrue(by_id["double_split_das_surrender"]["needs_user"])
        self.assertTrue(body["public_help_peek_conflict"])
        self.assertIsNone(body["n_decks"])

    def test_public_help_candidates_do_not_become_this_table(self):
        from blackjack_lab.core.table_rules_diff import printed_felt_observation
        felt_only = from_felt_observation(
            printed_felt_observation(), include_public_help=False)
        self.assertEqual("not_visible", {
            row["id"]: row for row in felt_only["rows"]
        }["n_decks_removed_ranks"]["status"])
        body = from_felt_observation(printed_felt_observation())
        self.assertFalse(body["accepted"])
        self.assertFalse(body["applies_to_live_table"])
        self.assertFalse(body["public_help_applies_to_this_table"])
        self.assertIsNone(body["n_decks"])
        self.assertEqual(8, body["public_help_candidate_n_decks"])
        by_id = {row["id"]: row for row in body["rows"]}
        self.assertEqual("visible_on_felt", by_id["s17_h17_bj_payout"]["status"])
        self.assertEqual("conflict_in_public_help", by_id["hole_card_peek"]["status"])
        self.assertIn("no peek", by_id["hole_card_peek"]["value"])
        self.assertIn("Vegas peek", by_id["hole_card_peek"]["value"])
        self.assertEqual("public_help_unconfirmed", by_id["double_split_das_surrender"]["status"])
        self.assertIn("DAS", by_id["double_split_das_surrender"]["value"])
        self.assertTrue(by_id["double_split_das_surrender"]["needs_user"])
        self.assertIn("n_decks_removed_ranks", body["uncertain"])
        self.assertIn("hole_card_peek", body["uncertain"])
        urls = [item["url"] for item in body["public_help_sources"]]
        self.assertIn(
            "https://www.pragmaticplay.com/en/live-casino/double-down-after-split/", urls)
        self.assertIn("https://www.btcgosu.com/labs/blackjack/", urls)

    def test_evolution_provider_skips_pragmatic_public_help(self):
        body = from_felt_observation({
            "provider_on_screen": "Evolution",
            "table_label_on_screen": None,
            "visible_felt_text": [],
            "not_visible": ["n_decks"],
            "n_decks": None,
        })
        self.assertEqual("provider_not_stake_or_pragmatic", body["public_help_skipped"])
        self.assertIsNone(body["n_decks"])
        self.assertIsNone(body["public_help_candidate_n_decks"])
        self.assertEqual([], body["public_help_sources"])
        by_id = {row["id"]: row for row in body["rows"]}
        self.assertEqual("not_visible", by_id["n_decks_removed_ranks"]["status"])


    def test_row_decision_needs_a_human_and_does_not_copy_eight_decks(self):
        from blackjack_lab.core.table_rules_diff import (
            ROW_DECISION_MATCH, printed_felt_observation, record_row_decision, review_rows,
        )
        body = from_felt_observation(printed_felt_observation())
        pending = review_rows(body)
        self.assertTrue(pending)
        decks = next(row for row in pending if row["id"] == "n_decks_removed_ranks")
        with self.assertRaises(ValueError):
            record_row_decision(body, decks["id"], decision=ROW_DECISION_MATCH, attested_by="Grok")
        with self.assertRaises(ValueError):
            record_row_decision(body, decks["id"], decision=ROW_DECISION_MATCH, attested_by="  ")
        updated = record_row_decision(
            body, decks["id"], decision=ROW_DECISION_MATCH, attested_by="Shawn")
        self.assertFalse(updated["accepted"])
        self.assertFalse(updated["applies_to_live_table"])
        self.assertIsNone(updated["n_decks"])
        by_id = {row["id"]: row for row in updated["rows"]}
        self.assertEqual(ROW_DECISION_MATCH, by_id["n_decks_removed_ranks"]["user_decision"])
        self.assertFalse(by_id["n_decks_removed_ranks"]["needs_user"])
        self.assertEqual("public_help_unconfirmed", by_id["n_decks_removed_ranks"]["status"])
        self.assertEqual("Shawn", updated["row_decisions"][0]["attested_by"])
        self.assertFalse(updated["row_decisions"][0]["copied_n_decks"])
        self.assertNotIn("n_decks_removed_ranks", [row["id"] for row in review_rows(updated)])


class ShoeEventDraftTest(unittest.TestCase):
    def test_cut_card_is_not_counted_and_unknowns_are_kept(self):
        draft = development_clip_stub(filename="Desktop 2026.09.12 - 12.58.11.02.mp4")
        self.assertEqual("development", draft["role"])
        cut = next(event for event in draft["events"] if event["kind"] == "cut_card")
        self.assertFalse(cut["counted_in_remaining"])
        self.assertTrue(cut["is_process_marker"])
        status = composition_status(draft)
        self.assertGreaterEqual(status["unknown_or_unconfirmed"], 1)
        self.assertFalse(status["complete_remaining"])
        self.assertEqual(1, status["cut_markers"])
        keep_unknown(draft)
        kept = next(event for event in draft["events"] if event["kind"] == "deal")
        self.assertEqual("unknown_kept", kept["status"])
        confirm_events(
            draft, [event["event_id"] for event in draft["events"]
                    if event["kind"] in ("shoe_open", "cut_card")], confirmed_by="Shawn")
        self.assertEqual(
            "confirmed",
            next(event for event in draft["events"] if event["kind"] == "shoe_open")["status"])
        add_event(draft, "deal", round_id="round-1", rank="10", status="draft")
        ranked = draft["events"][-1]
        confirm_events(draft, [ranked["event_id"]], confirmed_by="Shawn")
        self.assertEqual("confirmed", ranked["status"])
        with self.assertRaises(ValueError):
            confirm_events(draft, [ranked["event_id"]], confirmed_by="Grok")
        with self.assertRaises(ValueError):
            development_clip_stub(
                filename="Desktop 2026.09.12 - 12.58.11.02.mp4", role="holdout")
        pages = review_pages(draft)
        self.assertEqual("shoe-open", pages[0]["page_id"])
        self.assertEqual("shoe-end", pages[-1]["page_id"])
        correct_event(draft, cut["event_id"], notes="红牌停手")
        self.assertFalse(cut["counted_in_remaining"])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "draft.json"
            saved = write_draft(path, draft)
            self.assertFalse(saved["accepted"])
            self.assertFalse(saved["composition"]["complete_remaining"])
            self.assertFalse(saved["composition"]["cut_card_counted_in_remaining"])


class StillExtractWithoutRehashTest(unittest.TestCase):
    def test_tiny_video_stills_do_not_hash_source(self):
        from blackjack_lab.vision.deps import cv2_available
        if not cv2_available():
            self.skipTest("optional video dependencies unavailable")
        from blackjack_lab.analysis.review_frames import extract_stills_without_rehash
        from blackjack_lab.analysis.shoe_event_draft import attach_stills_to_draft
        from blackjack_lab.vision.deps import load_cv2, load_numpy
        cv2, np = load_cv2(), load_numpy()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            video = root / "tiny.avi"
            writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 10, (96, 64))
            self.assertTrue(writer.isOpened())
            for index in range(6):
                writer.write(np.full((64, 96, 3), index * 30, dtype=np.uint8))
            writer.release()
            stills = extract_stills_without_rehash(video, root / "stills", indexes=(0, 5))
            self.assertFalse(stills["accepted"])
            self.assertFalse(stills["source_hashed"])
            self.assertNotIn("video_sha256", stills)
            self.assertEqual(2, len(stills["frames"]))
            draft = development_clip_stub(filename="tiny.avi", role="development")
            attach_stills_to_draft(draft, stills)
            self.assertEqual(stills["frames"][0]["path"], next(
                event["still_path"] for event in draft["events"] if event["kind"] == "shoe_open"))
            self.assertIsNone(next(
                event["rank"] for event in draft["events"] if event["kind"] == "deal"))


class RoundScanTest(unittest.TestCase):
    def test_occupancy_rounds_keep_unknown_ranks_and_skip_overlay(self):
        from blackjack_lab.vision.deps import cv2_available
        if not cv2_available():
            self.skipTest("optional video dependencies unavailable")
        from blackjack_lab.analysis.shoe_event_draft import composition_status
        from blackjack_lab.analysis.shoe_round_pages import (
            draft_from_round_scan, nvidia_overlay_likely, scan_round_pages,
        )
        from blackjack_lab.vision.deps import load_cv2, load_numpy
        cv2, np = load_cv2(), load_numpy()
        overlay = np.zeros((64, 96, 3), dtype=np.uint8)
        overlay[:, 48:] = (180, 190, 200)
        empty = np.full((64, 96, 3), 25, dtype=np.uint8)
        cards = np.full((64, 96, 3), 25, dtype=np.uint8)
        cards[10:50, 20:70] = 220
        self.assertTrue(nvidia_overlay_likely(overlay))
        self.assertFalse(nvidia_overlay_likely(empty))
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            video = root / "rounds.avi"
            writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 10, (96, 64))
            self.assertTrue(writer.isOpened())
            for frame in [overlay] * 4 + [empty] * 6 + [cards] * 8 + [empty] * 6:
                writer.write(frame)
            writer.release()
            style = root / "style.json"
            style.write_text(json.dumps({
                "style_id": "tiny-test",
                "source_frame": {"width": 96, "height": 64},
                "source_crop": {"x": 0, "y": 0, "w": 96, "h": 64},
                "felt_crop": {"x": 0, "y": 0, "w": 96, "h": 64},
            }), encoding="utf-8")
            scan = scan_round_pages(
                video, root / "pages", interval_s=0.2, style_path=style)
            self.assertFalse(scan["accepted"])
            self.assertFalse(scan["source_hashed"])
            self.assertGreaterEqual(scan["overlay_samples"], 1)
            self.assertEqual(1, scan["round_count"])
            draft = draft_from_round_scan(scan, filename="rounds.avi")
            deals = [event for event in draft["events"] if event["kind"] == "deal"]
            self.assertEqual(1, len(deals))
            self.assertIsNone(deals[0]["rank"])
            self.assertEqual("unknown_kept", deals[0]["status"])
            cut = next(event for event in draft["events"] if event["kind"] == "cut_card")
            self.assertFalse(cut["counted_in_remaining"])
            self.assertFalse(composition_status(draft)["complete_remaining"])
            self.assertFalse(draft["accepted"])

    def test_waiting_banner_is_not_a_new_deal(self):
        from blackjack_lab.vision.deps import cv2_available
        if not cv2_available():
            self.skipTest("optional video dependencies unavailable")
        from blackjack_lab.analysis.shoe_round_pages import (
            draft_from_round_scan, waiting_next_round_likely,
        )
        from blackjack_lab.vision.deps import load_numpy
        np = load_numpy()
        waiting = np.full((80, 160, 3), 20, dtype=np.uint8)
        waiting[2:10, 55:105] = 10
        waiting[4:8, 70:90] = 220
        live = np.full((80, 160, 3), 20, dtype=np.uint8)
        live[30:70, 40:120] = 210
        self.assertTrue(waiting_next_round_likely(waiting))
        self.assertFalse(waiting_next_round_likely(live))
        scan = {
            "video_path": "clip.mp4", "video_bytes": 1, "round_count": 1,
            "overlay_samples": 0, "interval_s": 8, "samples": [],
            "rounds": [{
                "round_id": "round-1", "start_frame": 1, "end_frame": 2, "samples": 2,
                "peak": {
                    "frame_index": 1, "still_path": None, "occupancy": 0.2,
                    "waiting_next_round": True,
                },
            }],
        }
        draft = draft_from_round_scan(scan, filename="Desktop 2026.09.12 - 12.58.11.02.mp4")
        self.assertEqual(0, sum(1 for event in draft["events"] if event["kind"] == "deal"))
        leftover = next(event for event in draft["events"] if event["kind"] == "between_rounds")
        self.assertEqual("unknown_kept", leftover["status"])
        self.assertIsNone(leftover["rank"])
        self.assertFalse(leftover["counted_in_remaining"])
        self.assertFalse(draft["accepted"])

    def test_in_play_placeholders_stay_unranked(self):
        from blackjack_lab.vision.deps import cv2_available
        if not cv2_available():
            self.skipTest("optional video dependencies unavailable")
        from blackjack_lab.analysis.shoe_event_draft import add_event, empty_draft
        from blackjack_lab.analysis.shoe_round_pages import (
            attach_unknown_placeholders, felt_card_placeholders,
        )
        from blackjack_lab.vision.deps import load_cv2, load_numpy
        cv2, np = load_cv2(), load_numpy()
        felt = np.full((120, 280, 3), 18, dtype=np.uint8)
        for left in (30, 110, 190):
            felt[55:88, left:left + 48] = 220
        boxes = felt_card_placeholders(felt)
        self.assertGreaterEqual(len(boxes), 2)
        self.assertTrue(all("rank" not in box for box in boxes))
        with tempfile.TemporaryDirectory() as folder:
            still = Path(folder) / "inplay.png"
            cv2.imwrite(str(still), felt)
            draft = empty_draft(
                role="development", filename="Desktop 2026.09.12 - 12.58.11.02.mp4")
            add_event(
                draft, "deal", round_id="round-1", rank=None, status="unknown_kept",
                still_path=str(still),
            )
            attach_unknown_placeholders(draft, repo_root=Path(folder))
            deals = [event for event in draft["events"] if event["kind"] == "deal"]
            self.assertGreaterEqual(len(deals), 2)
            self.assertTrue(all(event.get("rank") in (None, "", "unknown") for event in deals))
            self.assertFalse(draft["accepted"])
            attach_unknown_placeholders(draft, repo_root=Path(folder))
            self.assertEqual(
                len(deals),
                sum(1 for event in draft["events"] if event["kind"] == "deal"),
            )

    def test_waiting_still_does_not_add_placeholders(self):
        from blackjack_lab.vision.deps import cv2_available
        if not cv2_available():
            self.skipTest("optional video dependencies unavailable")
        from blackjack_lab.analysis.shoe_event_draft import add_event, empty_draft
        from blackjack_lab.analysis.shoe_round_pages import attach_unknown_placeholders
        from blackjack_lab.vision.deps import load_cv2, load_numpy
        cv2, np = load_cv2(), load_numpy()
        felt = np.full((120, 280, 3), 18, dtype=np.uint8)
        band = felt[0:14, 90:190]
        band[:] = 20
        band[4:10, 20:70] = 220
        felt[55:88, 40:88] = 220
        felt[55:88, 120:168] = 220
        with tempfile.TemporaryDirectory() as folder:
            still = Path(folder) / "wait.png"
            cv2.imwrite(str(still), felt)
            draft = empty_draft(
                role="development", filename="Desktop 2026.09.12 - 12.58.11.02.mp4")
            add_event(
                draft, "deal", round_id="round-wait", rank=None, status="unknown_kept",
                still_path=str(still),
            )
            attach_unknown_placeholders(draft, repo_root=Path(folder))
            deals = [event for event in draft["events"] if event["kind"] == "deal"]
            self.assertEqual(1, len(deals))
            self.assertIsNone(deals[0]["rank"])
            self.assertFalse(draft["accepted"])

    def test_in_play_sequence_stills_are_not_ranks(self):
        from blackjack_lab.vision.deps import cv2_available
        if not cv2_available():
            self.skipTest("optional video dependencies unavailable")
        from blackjack_lab.analysis.shoe_event_draft import add_event, empty_draft, review_pages
        from blackjack_lab.analysis.shoe_round_pages import (
            attach_in_play_sequence, sample_in_play_windows,
        )
        from blackjack_lab.vision.deps import load_cv2, load_numpy
        cv2, np = load_cv2(), load_numpy()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            video = root / "seq.avi"
            writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 10, (96, 64))
            self.assertTrue(writer.isOpened())
            for index in range(24):
                frame = np.full((64, 96, 3), 18, dtype=np.uint8)
                if 4 <= index <= 12:
                    frame[20:50, 20:70] = 220
                writer.write(frame)
            writer.release()
            scan = {
                "felt_abs": [0, 0, 96, 64],
                "rounds": [
                    {
                        "round_id": "round-live",
                        "start_frame": 4, "end_frame": 12, "samples": 3,
                        "peak": {"frame_index": 8, "between_round_ui": False},
                    },
                    {
                        "round_id": "round-wait",
                        "start_frame": 16, "end_frame": 20, "samples": 2,
                        "peak": {"frame_index": 18, "between_round_ui": True,
                                 "waiting_next_round": True},
                    },
                ],
            }
            sample = sample_in_play_windows(
                video, scan, root / "seq-out", step_s=0.2)
            self.assertFalse(sample["accepted"])
            self.assertFalse(sample["source_hashed"])
            self.assertEqual(1, sample["window_count"])
            filtered = sample_in_play_windows(
                video, scan, root / "seq-filter", step_s=0.2, round_ids=["round-wait"])
            self.assertEqual(0, filtered["window_count"])
            self.assertGreaterEqual(sample["windows"][0]["still_count"], 2)
            self.assertTrue(all(item.get("rank") is None
                                for item in sample["windows"][0]["stills"]))
            draft = empty_draft(
                role="development", filename="Desktop 2026.09.12 - 12.58.11.02.mp4")
            add_event(
                draft, "deal", round_id="round-live", rank=None, status="unknown_kept")
            attach_in_play_sequence(draft, sample)
            stills = [event for event in draft["events"] if event["kind"] == "in_play_still"]
            self.assertGreaterEqual(len(stills), 2)
            self.assertTrue(all(event.get("rank") is None for event in stills))
            self.assertFalse(any(event.get("counted_in_remaining") for event in stills))
            pages = review_pages(draft, skip_waiting_only=True)
            live = next(page for page in pages if page["page_id"] == "round-live")
            self.assertGreaterEqual(len(live["sequence_stills"]), 2)
            attach_in_play_sequence(draft, sample)
            self.assertEqual(
                len(stills),
                sum(1 for event in draft["events"] if event["kind"] == "in_play_still"),
            )
            self.assertFalse(draft["accepted"])

    def test_dealer_region_crop_does_not_read_ranks(self):
        from blackjack_lab.vision.deps import cv2_available
        if not cv2_available():
            self.skipTest("optional video dependencies unavailable")
        from blackjack_lab.analysis.shoe_round_pages import crop_style_region
        from blackjack_lab.vision.deps import load_cv2, load_numpy
        cv2, np = load_cv2(), load_numpy()
        felt = np.full((282, 1180, 3), 18, dtype=np.uint8)
        felt[50:90, 500:560] = 220
        with tempfile.TemporaryDirectory() as folder:
            src = Path(folder) / "felt.png"
            dest = Path(folder) / "dealer.png"
            cv2.imwrite(str(src), felt)
            body = crop_style_region(src, "dealer", dest)
            self.assertFalse(body["accepted"])
            self.assertFalse(body["ranks_invented"])
            self.assertEqual("庄家", body["seat_hint"])
            self.assertTrue(dest.is_file())

    def test_chip_strip_is_not_a_new_deal(self):
        from blackjack_lab.vision.deps import cv2_available
        if not cv2_available():
            self.skipTest("optional video dependencies unavailable")
        from blackjack_lab.analysis.shoe_round_pages import chip_strip_likely, draft_from_round_scan
        from blackjack_lab.vision.deps import load_cv2, load_numpy
        cv2, np = load_cv2(), load_numpy()
        strip = np.full((120, 280, 3), 18, dtype=np.uint8)
        for index, color in enumerate(
            ((20, 80, 220), (30, 40, 200), (20, 120, 230), (180, 60, 40),
             (40, 180, 40), (40, 200, 200), (20, 160, 90))
        ):
            cv2.circle(strip, (80 + index * 20, 24), 9, color, -1)
        live = np.full((120, 280, 3), 18, dtype=np.uint8)
        live[70:110, 40:240] = 210
        self.assertTrue(chip_strip_likely(strip))
        self.assertFalse(chip_strip_likely(live))
        scan = {
            "video_path": "clip.mp4", "video_bytes": 1, "round_count": 1,
            "overlay_samples": 0, "interval_s": 8, "samples": [],
            "rounds": [{
                "round_id": "round-1", "start_frame": 1, "end_frame": 2, "samples": 2,
                "peak": {
                    "frame_index": 1, "still_path": None, "occupancy": 0.2,
                    "chip_strip": True, "between_round_ui": True,
                },
            }],
        }
        draft = draft_from_round_scan(scan, filename="Desktop 2026.09.12 - 12.58.11.02.mp4")
        self.assertEqual(0, sum(1 for event in draft["events"] if event["kind"] == "deal"))
        self.assertFalse(draft["accepted"])


class RoundHysteresisTest(unittest.TestCase):
    def test_felt_text_floor_does_not_merge_the_whole_shoe(self):
        from blackjack_lab.analysis.shoe_round_pages import _segment_rounds
        samples = []
        for index, occ in enumerate(
            [0.08, 0.09, 0.05, 0.04, 0.07, 0.08, 0.05, 0.04, 0.07, 0.09]
        ):
            samples.append({
                "frame_index": index * 960, "occupancy": occ, "overlay": False,
                "still_path": None, "time_ms": index * 8000,
            })
        rounds = _segment_rounds(samples)
        self.assertEqual(3, len(rounds))
        self.assertEqual("round-1", rounds[0]["round_id"])
        self.assertLess(rounds[0]["end_frame"], rounds[1]["start_frame"])

    def test_peak_drop_splits_a_late_merged_stretch(self):
        from blackjack_lab.analysis.shoe_round_pages import _segment_rounds
        # 0.075 -> 0.058 is below occupy_on but above occupy_off; drop splits it.
        series = [0.075, 0.076, 0.075, 0.058, 0.052, 0.074, 0.080, 0.043]
        samples = [{
            "frame_index": index * 960, "occupancy": occ, "overlay": False,
            "still_path": None, "time_ms": index * 8000,
        } for index, occ in enumerate(series)]
        rounds = _segment_rounds(samples)
        self.assertGreaterEqual(len(rounds), 2)


class PageConfirmTest(unittest.TestCase):
    def test_confirm_page_needs_a_human_and_cut_card_cannot_take_a_rank(self):
        draft = development_clip_stub(filename="Desktop 2026.09.12 - 12.58.11.02.mp4")
        pages = review_pages(draft)
        self.assertEqual("shoe-open", pages[0]["page_id"])
        self.assertIn("still_path", pages[0])
        with self.assertRaises(ValueError):
            confirm_page(draft, "shoe-open", confirmed_by="Grok")
        confirm_page(draft, "shoe-open", confirmed_by="Shawn")
        shoe_open = next(event for event in draft["events"] if event["kind"] == "shoe_open")
        self.assertEqual("confirmed", shoe_open["status"])
        cut = next(event for event in draft["events"] if event["kind"] == "cut_card")
        with self.assertRaises(ValueError):
            set_event_rank(draft, cut["event_id"], "10")
        self.assertFalse(cut["counted_in_remaining"])
        deal = next(event for event in draft["events"] if event["kind"] == "deal")
        set_event_rank(draft, deal["event_id"], "K")
        self.assertEqual("K", deal["rank"])
        self.assertEqual("draft", deal["status"])
        self.assertFalse(draft["accepted"])
        from blackjack_lab.analysis.shoe_event_draft import set_event_seat
        set_event_seat(draft, deal["event_id"], "庄家")
        self.assertEqual("庄家", deal["seat"])
        with self.assertRaises(ValueError):
            set_event_seat(draft, cut["event_id"], "玩家1")
        self.assertFalse(draft["accepted"])

    def test_skip_waiting_pages_keeps_deal_pages(self):
        from blackjack_lab.analysis.shoe_event_draft import add_event, empty_draft
        draft = empty_draft(
            role="development", filename="Desktop 2026.09.12 - 12.58.11.02.mp4")
        add_event(draft, "shoe_open")
        add_event(draft, "between_rounds", round_id="round-wait", status="unknown_kept")
        add_event(draft, "deal", round_id="round-live", rank=None, status="unknown_kept")
        add_event(draft, "cut_card", status="unknown_kept")
        all_pages = review_pages(draft)
        skip = review_pages(draft, skip_waiting_only=True)
        self.assertTrue(any(page["waiting_only"] for page in all_pages))
        self.assertFalse(any(page["waiting_only"] for page in skip))
        self.assertTrue(any(
            event["kind"] == "deal" for page in skip for event in page["events"]))
        self.assertEqual(len(all_pages) - 1, len(skip))

    def test_unknown_card_slots_and_dealer_template_stay_unranked(self):
        from blackjack_lab.analysis.shoe_event_draft import (
            add_event, add_unknown_card, empty_draft, ensure_standard_dealer_slots,
        )
        draft = empty_draft(
            role="development", filename="Desktop 2026.09.12 - 12.58.11.02.mp4")
        add_event(draft, "deal", round_id="round-live", rank=None, status="unknown_kept")
        ensure_standard_dealer_slots(draft)
        hidden = [event for event in draft["events"] if event["kind"] == "hidden"]
        self.assertEqual(1, len(hidden))
        self.assertEqual("庄家", hidden[0]["seat_hint"])
        self.assertIsNone(hidden[0]["rank"])
        self.assertFalse(hidden[0].get("seat"))
        extra = add_unknown_card(draft, "round-live", kind="deal", seat="玩家3")
        self.assertIsNone(extra["rank"])
        self.assertEqual("玩家3", extra["seat"])
        self.assertFalse(draft["accepted"])
        ensure_standard_dealer_slots(draft)
        self.assertEqual(1, sum(1 for event in draft["events"] if event["kind"] == "hidden"))


class DraftImportTest(unittest.TestCase):
    def test_import_keeps_unknowns_and_does_not_deal_the_cut_card(self):
        from blackjack_lab.analysis.contracts import research_rules
        from blackjack_lab.analysis.research_windows import build_offline_mc_input
        from blackjack_lab.analysis.fixed_policy_mc import POLICY_ALWAYS_STAND
        from blackjack_lab.ledger.draft_import import apply_event_draft
        from blackjack_lab.ledger.events import CARD_DEALT, OBSERVATION_GAP
        from blackjack_lab.ui.controller import SessionController
        draft = development_clip_stub(filename="Desktop 2026.09.12 - 12.58.11.02.mp4")
        deal = next(event for event in draft["events"] if event["kind"] == "deal")
        set_event_rank(draft, deal["event_id"], "K")
        confirm_events(draft, [deal["event_id"]], confirmed_by="Shawn")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ctrl = SessionController(Path(tmp.name) / "draft.db")
        self.addCleanup(ctrl.close)
        with self.assertRaises(ValueError):
            apply_event_draft(ctrl, draft, seat="玩家1")
        ctrl.new_shoe(research_rules(6, surrender=None))
        result = apply_event_draft(ctrl, draft, seat="玩家1")
        self.assertFalse(result["accepted"])
        self.assertFalse(result["offline_mc_ready"])
        self.assertGreaterEqual(result["gaps"], 1)
        self.assertEqual(1, result["card_dealt"])
        dealt = [event for event in ctrl.ledger.events if event.etype == CARD_DEALT]
        self.assertEqual(["K"], [event.payload["rank"] for event in dealt])
        self.assertFalse(any(event.etype == CARD_DEALT and event.payload.get("rank") in (None, "cut")
                             for event in ctrl.ledger.events))
        self.assertTrue(result["cut_card_marker"])
        self.assertFalse(any(
            event.etype == OBSERVATION_GAP and "切牌" in event.payload["reason"]
            for event in ctrl.ledger.events))
        with self.assertRaises(Exception) as caught:
            build_offline_mc_input(
                ctrl.ledger, policy=POLICY_ALWAYS_STAND, n_samples=8, seed=1)
        self.assertIn(caught.exception.code, {
            "PRIOR_ROUND_OBSERVATION", "RECORD_GAP", "COMPOSITION_UNKNOWN",
            "BURN_COUNT_UNKNOWN", "NONZERO_BURN",
        })

    def test_confirmed_dealer_seat_is_not_forced_onto_player_one(self):
        from blackjack_lab.analysis.contracts import research_rules
        from blackjack_lab.analysis.shoe_event_draft import set_event_seat
        from blackjack_lab.ledger.draft_import import apply_event_draft
        from blackjack_lab.ledger.events import CARD_DEALT
        from blackjack_lab.ui.controller import SessionController
        draft = development_clip_stub(filename="Desktop 2026.09.12 - 12.58.11.02.mp4")
        deal = next(event for event in draft["events"] if event["kind"] == "deal")
        set_event_rank(draft, deal["event_id"], "3")
        set_event_seat(draft, deal["event_id"], "庄家")
        confirm_events(draft, [deal["event_id"]], confirmed_by="Shawn")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ctrl = SessionController(Path(tmp.name) / "dealer-seat.db")
        self.addCleanup(ctrl.close)
        ctrl.new_shoe(research_rules(6, surrender=None))
        result = apply_event_draft(ctrl, draft, seat="玩家1")
        dealt = [event for event in ctrl.ledger.events if event.etype == CARD_DEALT]
        self.assertEqual(["庄家"], [event.payload["seat"] for event in dealt])
        self.assertEqual(["3"], [event.payload["rank"] for event in dealt])
        self.assertFalse(result["accepted"])
        self.assertFalse(result["offline_mc_ready"])

    def test_confirmed_hidden_is_a_hole_not_a_guessed_rank(self):
        from blackjack_lab.analysis.contracts import research_rules
        from blackjack_lab.analysis.shoe_event_draft import add_unknown_card
        from blackjack_lab.ledger.draft_import import apply_event_draft
        from blackjack_lab.ledger.events import CARD_DEALT, FACE_HIDDEN
        from blackjack_lab.ui.controller import SessionController
        draft = development_clip_stub(filename="Desktop 2026.09.12 - 12.58.11.02.mp4")
        deal = next(event for event in draft["events"] if event["kind"] == "deal")
        hole = add_unknown_card(draft, deal["round_id"], kind="hidden", seat="庄家")
        confirm_events(draft, [hole["event_id"]], confirmed_by="Shawn")
        self.assertEqual("confirmed", hole["status"])
        self.assertIsNone(hole["rank"])
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ctrl = SessionController(Path(tmp.name) / "hole.db")
        self.addCleanup(ctrl.close)
        ctrl.new_shoe(research_rules(6, surrender=None))
        result = apply_event_draft(ctrl, draft, seat="玩家1")
        holes = [
            event for event in ctrl.ledger.events
            if event.etype == CARD_DEALT and event.payload.get("face_state") == FACE_HIDDEN
        ]
        self.assertEqual(1, len(holes))
        self.assertEqual("庄家", holes[0].payload["seat"])
        self.assertIn(holes[0].payload.get("rank"), (None, "?"))
        self.assertFalse(result["accepted"])
        self.assertFalse(result["offline_mc_ready"])

    def test_occupancy_draft_writes_gaps_not_invented_ranks(self):
        from blackjack_lab.analysis.contracts import research_rules
        from blackjack_lab.analysis.research_windows import build_offline_mc_input
        from blackjack_lab.analysis.fixed_policy_mc import POLICY_ALWAYS_STAND
        from blackjack_lab.ledger.draft_import import apply_event_draft
        from blackjack_lab.ledger.events import CARD_DEALT
        from blackjack_lab.ui.controller import SessionController
        draft = development_clip_stub(filename="Desktop 2026.09.12 - 12.58.11.02.mp4")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ctrl = SessionController(Path(tmp.name) / "occupancy.db")
        self.addCleanup(ctrl.close)
        with self.assertRaises(ValueError):
            apply_event_draft(ctrl, draft, seat="座位8")
        ctrl.new_shoe(research_rules(6, surrender=None))
        result = apply_event_draft(ctrl, draft, seat="庄家")
        self.assertFalse(result["accepted"])
        self.assertFalse(result["offline_mc_ready"])
        self.assertEqual(0, result["card_dealt"])
        self.assertGreaterEqual(result["gaps"], 2)
        self.assertFalse(any(event.etype == CARD_DEALT for event in ctrl.ledger.events))
        with self.assertRaises(Exception) as caught:
            build_offline_mc_input(
                ctrl.ledger, policy=POLICY_ALWAYS_STAND, n_samples=8, seed=1)
        self.assertIn(caught.exception.code, {
            "PRIOR_ROUND_OBSERVATION", "RECORD_GAP", "COMPOSITION_UNKNOWN",
            "BURN_COUNT_UNKNOWN", "NONZERO_BURN",
        })

    def test_missing_burn_is_a_gap_not_zero(self):
        from blackjack_lab.analysis.contracts import research_rules
        from blackjack_lab.analysis.shoe_event_draft import add_event, empty_draft
        from blackjack_lab.ledger.draft_import import apply_event_draft
        from blackjack_lab.ledger.events import CARD_DEALT, OBSERVATION_GAP
        from blackjack_lab.ui.controller import SessionController
        draft = empty_draft(role="development", filename="Desktop 2026.09.12 - 12.58.11.02.mp4")
        add_event(draft, "deal", round_id="round-1", rank=None, status="unknown_kept")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ctrl = SessionController(Path(tmp.name) / "noburn.db")
        self.addCleanup(ctrl.close)
        ctrl.new_shoe(research_rules(6, surrender=None))
        result = apply_event_draft(ctrl, draft, seat="玩家1")
        self.assertEqual(0, result["card_dealt"])
        self.assertTrue(any(event.etype == OBSERVATION_GAP and "烧牌" in event.payload["reason"]
                            for event in ctrl.ledger.events))
        self.assertFalse(any(event.etype == CARD_DEALT for event in ctrl.ledger.events))
        self.assertFalse(result["offline_mc_ready"])

    def test_waiting_only_round_is_not_imported_as_a_deal_round(self):
        from blackjack_lab.analysis.contracts import research_rules
        from blackjack_lab.analysis.shoe_event_draft import add_event, empty_draft
        from blackjack_lab.ledger.draft_import import apply_event_draft
        from blackjack_lab.ledger.events import CARD_DEALT, ROUND_STARTED
        from blackjack_lab.ui.controller import SessionController
        draft = empty_draft(
            role="development", filename="Desktop 2026.09.12 - 12.58.11.02.mp4")
        add_event(draft, "round_start", round_id="round-wait")
        add_event(draft, "between_rounds", round_id="round-wait", status="unknown_kept")
        add_event(draft, "round_end", round_id="round-wait")
        add_event(draft, "deal", round_id="round-live", rank=None, status="unknown_kept")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ctrl = SessionController(Path(tmp.name) / "wait-import.db")
        self.addCleanup(ctrl.close)
        ctrl.new_shoe(research_rules(6, surrender=None))
        result = apply_event_draft(ctrl, draft, seat="玩家1")
        started = [event for event in ctrl.ledger.events if event.etype == ROUND_STARTED]
        self.assertEqual(1, len(started))
        self.assertEqual(0, result["card_dealt"])
        self.assertFalse(any(event.etype == CARD_DEALT for event in ctrl.ledger.events))
        self.assertFalse(result["accepted"])
        self.assertFalse(result["offline_mc_ready"])


class ForegroundObservationTest(unittest.TestCase):
    def test_foreground_snapshot_is_not_acceptance(self):
        from blackjack_lab.capture.window_list import observe_foreground
        body = observe_foreground(screen_width=1920, screen_height=1080)
        self.assertTrue(body["not_acceptance"])
        self.assertFalse(body.get("looks_monitor_sized") and body.get("accepted", False))
        session = start_session(environment={"screen_width": 1920, "screen_height": 1080})
        record_step(session, "enter_f11", notes="unit", observation=body)
        row = session["steps"][0]
        self.assertEqual(body, row["observation"])
        self.assertFalse(row["passed"])
        self.assertFalse(session["accepted"])

    def test_foreground_still_is_not_acceptance(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow unavailable")
        from blackjack_lab.capture.foreground_still import grab_foreground_still
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)

        def grabber(**_kwargs):
            return Image.new("RGB", (16, 12), (12, 18, 24))

        body = grab_foreground_still(tmp.name, hwnd=1, grabber=grabber, step_id="enter_f11")
        self.assertTrue(body["capture_ok"])
        self.assertFalse(body["accepted"])
        self.assertTrue(body["not_acceptance"])
        self.assertTrue(Path(body["path"]).is_file())
        failed = grab_foreground_still(tmp.name, grabber=lambda **k: (_ for _ in ()).throw(RuntimeError("no grab")))
        self.assertFalse(failed["capture_ok"])
        self.assertFalse(failed["accepted"])


class MaterialRoleTest(unittest.TestCase):
    def test_inventory_reuses_hashes_and_refuses_holdout_for_dev_clip(self):
        from blackjack_lab.analysis.material_roles import inventory_from_pack, set_role
        pack = {
            "items": {
                "authorized_shoe_video": {
                    "artifacts": [
                        {"path": r"C:\Videos\Desktop 2026.09.12 - 12.58.11.02.mp4",
                         "sha256": "abc", "bytes": 10},
                        {"path": r"C:\Videos\Desktop 2026.09.12 - 12.57.16.01.mp4",
                         "sha256": "def", "bytes": 11},
                    ]
                }
            }
        }
        inventory = inventory_from_pack(pack)
        self.assertFalse(inventory["accepted"])
        self.assertFalse(inventory["verify_digest"])
        roles = {row["filename"]: row["suggested_role"] for row in inventory["rows"]}
        self.assertEqual("development", roles["Desktop 2026.09.12 - 12.58.11.02.mp4"])
        self.assertEqual("candidate", roles["Desktop 2026.09.12 - 12.57.16.01.mp4"])
        with self.assertRaises(ValueError):
            set_role(inventory, "Desktop 2026.09.12 - 12.58.11.02.mp4", "holdout",
                     confirmed_by="Shawn")
        with self.assertRaises(ValueError):
            set_role(inventory, "Desktop 2026.09.12 - 12.57.16.01.mp4", "holdout",
                     confirmed_by="Grok")
        row = set_role(inventory, "Desktop 2026.09.12 - 12.57.16.01.mp4", "candidate",
                       confirmed_by="Shawn")
        self.assertEqual("Shawn", row["role_confirmed_by"])
        self.assertFalse(inventory["accepted"])


class WizardUsageContractTest(unittest.TestCase):
    def test_existing_wizards_export_evidence_without_accepting(self):
        session = start_session(environment={"not_acceptance": True, "monitor_count": 1})
        for step_id in (
            "enter_f11", "invoke_panel", "enter_two_cards", "correct_one_card",
            "return_to_browser", "interrupt_source",
        ):
            record_step(session, step_id, notes="usage-harness")
        skip_untested(session, "dpi_variants", reason="单屏未测")
        skip_untested(session, "multi_monitor", reason="没有第二块显示器")
        identity = allocate_pair_identity(operator_name="Shawn", video_label="usage-harness")
        operator = start_operator(identity=identity)
        metrics = dict(elapsed_seconds=9, keystrokes=3, clicks=1, backlog_peak=0,
                       missed_cards=0, duplicates=0, repair_seconds=1)
        record_leg(operator, "manual", human_run=True, **metrics)
        record_leg(operator, "assisted", human_run=True, **metrics)
        with tempfile.TemporaryDirectory() as folder:
            f11_path = Path(folder) / "fullscreen-wizard.json"
            pair_path = Path(folder) / "operator-pair.json"
            f11_body = as_fullscreen_evidence(session)
            f11_path.write_text(json.dumps(f11_body, ensure_ascii=False), encoding="utf-8")
            pair_body = export_session(operator, pair_path)
            from blackjack_lab.analysis.acceptance_pack import (
                SCOPE_FULLSCREEN_MANUAL, bind_item_artifact, build_acceptance_pack,
                freeze_status, record_scope_signoff, HUMAN_CONFIRMATION_PHRASE,
            )
            pack = bind_item_artifact(build_acceptance_pack(), "fullscreen_f11", f11_path)
            pack = bind_item_artifact(pack, "operator_pairs", pair_path)
            signed = record_scope_signoff(
                pack, scope=SCOPE_FULLSCREEN_MANUAL, attested_by="Shawn",
                code_commit="abc123",
                criteria="本机输入、纠错、保存恢复与焦点可靠",
                result="accepted", confirmation_phrase=HUMAN_CONFIRMATION_PHRASE)
        self.assertTrue(session["ready_for_human_fullscreen_signoff"])
        self.assertFalse(f11_body["accepted"])
        self.assertFalse(pair_body["accepted"])
        self.assertFalse(pair_body["paired"])
        self.assertFalse(signed["accepted"])
        frozen = freeze_status(
            code_commit="abc123", dirty_worktree=False, tests_bound_to_sha=True,
            pr_body_updated=True, m4_pack=signed, scope=SCOPE_FULLSCREEN_MANUAL)
        self.assertTrue(frozen["ready"])
        self.assertFalse(frozen["accepted"])
        self.assertFalse(signed["items"]["unused_attestation"]["artifacts"])
