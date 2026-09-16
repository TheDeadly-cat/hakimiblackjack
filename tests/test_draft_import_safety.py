"""Draft import must be all-or-nothing on real SQLite and retry-safe."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from blackjack_lab.analysis.contracts import research_rules
from blackjack_lab.analysis.shoe_event_draft import (
    add_event, confirm_events, empty_draft, set_event_rank,
)
from blackjack_lab.ledger.draft_import import DraftImportError, apply_event_draft
from blackjack_lab.ledger.events import BURN_CARDS, CARD_DEALT, OBSERVATION_GAP
from blackjack_lab.ui.controller import SessionController


def _two_card_draft(second_rank="Q"):
    draft = empty_draft(role="development", filename="atomic-import-fixture.mp4")
    add_event(draft, "burn", status="confirmed", count=0)
    first = add_event(draft, "deal", round_id="round-1", rank="K", status="draft", seat="玩家1")
    second = add_event(draft, "deal", round_id="round-1", rank=second_rank, status="draft", seat="玩家1")
    confirm_events(draft, [first["event_id"], second["event_id"]], confirmed_by="Shawn")
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
