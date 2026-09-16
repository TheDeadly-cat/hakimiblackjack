"""Draft import must be all-or-nothing on real SQLite and retry-safe."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from blackjack_lab.analysis.contracts import research_rules
from blackjack_lab.analysis.shoe_event_draft import (
    add_event, confirm_events, confirm_round_coverage, coverage_attestation_valid,
    empty_draft, set_event_rank,
)
from blackjack_lab.ledger.draft_import import DraftImportError, apply_event_draft
from blackjack_lab.ledger.events import BURN_CARDS, CARD_DEALT, OBSERVATION_GAP
from blackjack_lab.storage.database import DraftReceiptCorrupt
from blackjack_lab.ui.controller import SessionController


def _two_card_draft(second_rank="Q"):
    draft = empty_draft(
        role="development", filename="atomic-import-fixture.mp4", video_sha256="b" * 64)
    add_event(draft, "burn", status="confirmed", count=0)
    first = add_event(draft, "deal", round_id="round-1", rank="K", status="draft", seat="玩家1")
    second = add_event(draft, "deal", round_id="round-1", rank=second_rank, status="draft", seat="玩家1")
    confirm_events(draft, [first["event_id"], second["event_id"]], confirmed_by="Shawn")
    return draft


def _four_card_draft():
    draft = empty_draft(
        role="development", filename="synthetic-source.mp4", video_sha256="a" * 64)
    add_event(draft, "burn", status="confirmed", count=0)
    ids = []
    for seat, rank in (("玩家1", "K"), ("庄家", "10"), ("玩家1", "6"), ("庄家", "9")):
        event = add_event(draft, "deal", round_id="r1", seat=seat, rank=rank, status="draft")
        ids.append(event["event_id"])
    confirm_events(draft, ids, confirmed_by="fixture-reviewer", recorded_by="regression-test")
    confirm_round_coverage(
        draft, "r1", confirmed_by="fixture-reviewer",
        notes="synthetic fixture coverage; not a real-video attestation")
    return draft


class DraftImportSafetyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "import-safety.db"
        self.ctrl = SessionController(self.db)
        self.addCleanup(self.ctrl.close)
        self.ctrl.new_shoe(research_rules(6, surrender=None))

    def test_second_illegal_card_does_not_keep_the_first(self):
        before = [event.event_id for event in self.ctrl.ledger.events]
        with self.assertRaises(Exception):
            apply_event_draft(self.ctrl, _two_card_draft("ZZ"), seat="玩家1")
        after = SessionController.recover(self.db, self.ctrl.session_id)
        self.addCleanup(after.close)
        self.assertEqual(before, [event.event_id for event in after.ledger.events])
        self.assertFalse(any(event.etype == CARD_DEALT for event in after.ledger.events))

    def test_same_draft_imported_twice_does_not_double_cards(self):
        draft = _two_card_draft()
        first = apply_event_draft(self.ctrl, draft, seat="玩家1")
        second = apply_event_draft(self.ctrl, draft, seat="玩家1")
        self.assertTrue(second["replayed"])
        dealt = [event for event in self.ctrl.ledger.events if event.etype == CARD_DEALT]
        self.assertEqual(2, first["card_dealt"])
        self.assertEqual(2, len(dealt))
        self.assertEqual(["K", "Q"], [event.payload["rank"] for event in dealt])

    def test_restart_retry_does_not_deal_again(self):
        draft = _two_card_draft()
        apply_event_draft(self.ctrl, draft, seat="玩家1")
        session_id = self.ctrl.session_id
        self.ctrl.close()
        recovered = SessionController.recover(self.db, session_id)
        self.addCleanup(recovered.close)
        again = apply_event_draft(recovered, draft, seat="玩家1")
        self.assertTrue(again["replayed"])
        dealt = [event for event in recovered.ledger.events if event.etype == CARD_DEALT]
        self.assertEqual(2, len(dealt))

    def test_confirmed_zero_burn_is_not_an_unknown_gap(self):
        draft = _two_card_draft()
        result = apply_event_draft(self.ctrl, draft, seat="玩家1")
        self.assertEqual(0, result["burn_total"])
        self.assertFalse(any(event.etype == BURN_CARDS for event in self.ctrl.ledger.events))
        self.assertFalse(any(
            event.etype == OBSERVATION_GAP and "烧牌" in event.payload["reason"]
            for event in self.ctrl.ledger.events))

    def test_modified_draft_is_refused_instead_of_reimported(self):
        draft = _two_card_draft()
        apply_event_draft(self.ctrl, draft, seat="玩家1")
        set_event_rank(draft, draft["events"][-1]["event_id"], "A")
        confirm_events(draft, [draft["events"][-1]["event_id"]], confirmed_by="Shawn")
        with self.assertRaises(DraftImportError) as caught:
            apply_event_draft(self.ctrl, draft, seat="玩家1")
        self.assertEqual("DRAFT_CHANGED", caught.exception.code)
        dealt = [event for event in self.ctrl.ledger.events if event.etype == CARD_DEALT]
        self.assertEqual(["K", "Q"], [event.payload["rank"] for event in dealt])

    def test_committed_display_failure_does_not_duplicate_on_retry(self):
        draft = _two_card_draft()
        with patch.object(self.ctrl, "_publish_context_change", side_effect=RuntimeError("ui")):
            with self.assertRaises(DraftImportError) as caught:
                apply_event_draft(self.ctrl, draft, seat="玩家1")
        self.assertEqual("COMMITTED_DISPLAY_FAILED", caught.exception.code)
        self.assertTrue(caught.exception.committed)
        recovered = SessionController.recover(self.db, self.ctrl.session_id)
        self.addCleanup(recovered.close)
        dealt = [event for event in recovered.ledger.events if event.etype == CARD_DEALT]
        self.assertEqual(2, len(dealt))
        again = apply_event_draft(recovered, draft, seat="玩家1")
        self.assertTrue(again["replayed"])
        self.assertEqual(
            2, sum(1 for event in recovered.ledger.events if event.etype == CARD_DEALT))

    def test_metadata_failure_rolls_back_events_and_receipt(self):
        before = [event.to_dict() for event in self.ctrl.ledger.events]
        with patch.object(self.ctrl.store, "_set_meta", side_effect=OSError("injected receipt write failure")):
            with self.assertRaises(DraftImportError):
                apply_event_draft(self.ctrl, _two_card_draft(), seat="玩家1")
        self.assertEqual(before, [event.to_dict() for event in self.ctrl.ledger.events])
        self.assertFalse(self.ctrl.store.list_draft_import_receipts(self.ctrl.session_id))

    def test_dropped_frame_cannot_become_complete(self):
        draft = _four_card_draft()
        add_event(
            draft, "dropped_frame", round_id="r1", status="unknown_kept",
            notes="unresolved possible additional card", affects_composition=True)
        result = apply_event_draft(self.ctrl, draft, seat="玩家1")
        self.assertFalse(result["offline_mc_ready"])
        self.assertGreater(result["gaps"], 0)

    def test_obstruction_cannot_become_complete(self):
        draft = _four_card_draft()
        add_event(
            draft, "obstruction", round_id="r1", status="unknown_kept",
            notes="unresolved possible additional card", affects_composition=True)
        result = apply_event_draft(self.ctrl, draft, seat="玩家1")
        self.assertFalse(result["offline_mc_ready"])
        self.assertGreater(result["gaps"], 0)

    def test_unknown_obstructed_cannot_become_complete(self):
        draft = _four_card_draft()
        add_event(
            draft, "unknown_obstructed", round_id="r1", status="unknown_kept",
            notes="unresolved possible additional card", affects_composition=True)
        result = apply_event_draft(self.ctrl, draft, seat="玩家1")
        self.assertFalse(result["offline_mc_ready"])
        self.assertGreater(result["gaps"], 0)

    def test_coverage_expires_when_a_rank_changes(self):
        draft = _four_card_draft()
        self.assertTrue(coverage_attestation_valid(draft, "r1"))
        deal = next(event for event in draft["events"] if event.get("kind") == "deal")
        set_event_rank(draft, deal["event_id"], "A")
        confirm_events(draft, [deal["event_id"]], confirmed_by="Shawn")
        self.assertFalse(coverage_attestation_valid(draft, "r1"))

    def test_excluded_background_can_be_covered_after_reconfirm(self):
        draft = _four_card_draft()
        add_event(
            draft, "obstruction", round_id="r1", status="confirmed",
            affects_composition=False, notes="felt contour excluded")
        self.assertFalse(coverage_attestation_valid(draft, "r1"))
        confirm_round_coverage(
            draft, "r1", confirmed_by="Shawn",
            notes="synthetic fixture coverage after excluding background")
        result = apply_event_draft(self.ctrl, draft, seat="玩家1")
        self.assertTrue(result["offline_mc_ready"])
        self.assertEqual(4, result["card_dealt"])

    def test_hidden_unknown_rank_can_be_complete_but_composition_stays_unknown(self):
        draft = empty_draft(
            role="development", filename="hole-coverage.mp4", video_sha256="d" * 64)
        add_event(draft, "burn", status="confirmed", count=0)
        ids = []
        for seat, rank, kind in (
            ("玩家1", "K", "deal"),
            ("庄家", "10", "deal"),
            ("玩家1", "6", "deal"),
            ("庄家", None, "hidden"),
        ):
            event = add_event(
                draft, kind, round_id="r1", seat=seat, rank=rank, status="draft")
            ids.append(event["event_id"])
        confirm_events(draft, ids, confirmed_by="Shawn")
        confirm_round_coverage(
            draft, "r1", confirmed_by="Shawn",
            notes="synthetic fixture coverage; hole rank still unknown")
        result = apply_event_draft(self.ctrl, draft, seat="玩家1")
        self.assertEqual(4, result["card_dealt"])
        self.assertFalse(result["offline_mc_ready"])
        self.assertIn(result.get("inspect_reason"), {
            "COMPOSITION_UNKNOWN", "RECORD_GAP", "PRIOR_ROUND_OBSERVATION",
        })

    def test_regenerated_source_ids_cannot_reimport_same_video_as_new(self):
        import copy
        draft = _four_card_draft()
        apply_event_draft(self.ctrl, draft, seat="玩家1")
        before = [event.to_dict() for event in self.ctrl.ledger.events]
        changed = copy.deepcopy(draft)
        for index, event in enumerate(changed["events"]):
            event["event_id"] = f"new-pass-{index}"
        result = apply_event_draft(self.ctrl, changed, seat="玩家1")
        self.assertTrue(result.get("replayed"))
        self.assertEqual(before, [event.to_dict() for event in self.ctrl.ledger.events])

    def test_same_video_overlapping_unknown_span_with_changed_cards_is_blocked(self):
        apply_event_draft(self.ctrl, _four_card_draft(), seat="玩家1")
        before = [event.to_dict() for event in self.ctrl.ledger.events]
        other = empty_draft(
            role="development", filename="synthetic-source-b.mp4", video_sha256="a" * 64)
        add_event(other, "burn", status="confirmed", count=0)
        event = add_event(
            other, "deal", round_id="r2", seat="玩家1", rank="A", status="draft")
        confirm_events(other, [event["event_id"]], confirmed_by="Shawn")
        with self.assertRaises(DraftImportError) as caught:
            apply_event_draft(self.ctrl, other, seat="玩家1")
        self.assertEqual("SOURCE_OVERLAP", caught.exception.code)
        self.assertFalse(caught.exception.committed)
        self.assertEqual(before, [event.to_dict() for event in self.ctrl.ledger.events])

    def test_nonoverlapping_spans_of_same_video_can_import(self):
        first = _four_card_draft()
        first["source_span"] = {"start_frame": 0, "end_frame": 10}
        confirm_round_coverage(
            first, "r1", confirmed_by="Shawn",
            notes="synthetic fixture coverage with span")
        apply_event_draft(self.ctrl, first, seat="玩家1")
        second = empty_draft(
            role="development", filename="synthetic-source-later.mp4", video_sha256="a" * 64)
        second["source_span"] = {"start_frame": 200, "end_frame": 240}
        add_event(second, "burn", status="confirmed", count=0)
        ids = []
        for seat, rank in (("玩家1", "A"), ("庄家", "5"), ("玩家1", "8"), ("庄家", "7")):
            event = add_event(second, "deal", round_id="r2", seat=seat, rank=rank, status="draft")
            ids.append(event["event_id"])
        confirm_events(second, ids, confirmed_by="Shawn")
        confirm_round_coverage(
            second, "r2", confirmed_by="Shawn",
            notes="synthetic fixture coverage later span")
        result = apply_event_draft(self.ctrl, second, seat="玩家1")
        self.assertFalse(result.get("replayed"))
        self.assertEqual(8, sum(1 for event in self.ctrl.ledger.events if event.etype == CARD_DEALT))

    def test_corrupt_receipt_must_not_be_skipped(self):
        apply_event_draft(self.ctrl, _four_card_draft(), seat="玩家1")
        with self.ctrl.store.conn:
            self.ctrl.store.conn.execute(
                "UPDATE meta SET value=? WHERE key LIKE 'draft_import:%'",
                ('{"broken":',),
            )
        with self.assertRaises((ValueError, DraftImportError, DraftReceiptCorrupt)):
            self.ctrl.store.list_draft_import_receipts(self.ctrl.session_id)
        with self.assertRaises(DraftImportError) as caught:
            apply_event_draft(self.ctrl, _four_card_draft(), seat="玩家1")
        self.assertEqual("RECEIPT_CORRUPT", caught.exception.code)
        self.assertFalse(caught.exception.committed)
