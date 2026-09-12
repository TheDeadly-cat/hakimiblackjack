# -*- coding: utf-8 -*-
"""R2：人工确认后经控制器唯一入账；拒绝不写账本。"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from blackjack_lab.analysis.contracts import research_rules
from blackjack_lab.analysis.information import build_input
from blackjack_lab.analysis.contracts import InputUnavailable
from blackjack_lab.core.rules import RuleProfile
from blackjack_lab.ledger.events import (
    CARD_DEALT, CARD_REVEALED, CONFIRMED, SOURCE_MANUAL, SOURCE_SIMULATOR,
    SOURCE_VISION_CONFIRMED,
)
from blackjack_lab.ledger.ledger import LedgerError
from blackjack_lab.ui.controller import SessionController
from blackjack_lab.ui.vision_bridge import (
    FACE_HIDDEN_LEDGER, FACE_SHOWN_LEDGER, FACE_UNKNOWN_LEDGER,
    OP_CORRECT, OP_NEW, OP_REJECT, OP_REVEAL, ConfirmDecision,
    VisionBridgeError, VisionReviewSession,
)
from blackjack_lab.vision.contracts import (
    FACE_SHOWN, FACE_UNREADABLE, RECOGNITION_SCHEMA_VERSION, REVIEW_PENDING,
    CardObservation, RankHypothesis, RecognitionResult,
)


def _obs(oid, rank="8", score=0.99, face=FACE_SHOWN):
    cands = [RankHypothesis(rank, score, rank)] if rank else []
    return CardObservation(
        observation_id=oid, asset_sha256="a" * 64, crop_sha256=oid * 2,
        bbox={"x": 10, "y": 10, "w": 90, "h": 126}, region_id="player_target",
        layout_profile_id="synthetic-felt-v1", model_id="m", model_digest="d",
        recognition_schema_version=RECOGNITION_SCHEMA_VERSION,
        rank_candidates=cands, reject_reason=None if rank else "待核对",
        face_state_candidate=face, source_declaration="自建合成样式",
        seat_hint="玩家1",
    )


def _result(*obs):
    return RecognitionResult(
        asset_sha256="a" * 64, image_path="x.png",
        layout_profile_id="synthetic-felt-v1", model_id="m", model_digest="d",
        observations=list(obs),
    )


class TestVisionConfirmLedger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "vision.db"
        self.ctrl = SessionController(self.db, recording_source=SOURCE_SIMULATOR)
        self.addCleanup(self.ctrl.close)
        self.ctrl.new_shoe(research_rules(6))
        self.ctrl.start_round(["玩家1"])

    def session(self, *obs):
        return VisionReviewSession(self.ctrl, _result(*obs), Path(self.tmp.name) / "ev")

    def test_reject_does_not_write(self):
        before = self.ctrl.ledger.to_list()
        remaining = dict(self.ctrl.state().current.shoe.remaining)
        sess = self.session(_obs("1" * 32))
        out = sess.confirm(ConfirmDecision("1" * 32, OP_REJECT, seat="玩家1"))
        self.assertEqual(out.status, "rejected")
        self.assertEqual(self.ctrl.ledger.to_list(), before)
        self.assertEqual(self.ctrl.state().current.shoe.remaining, remaining)

    def test_confirm_new_card_debits_once_and_marks_vision_source(self):
        sess = self.session(_obs("2" * 32, "A"))
        before = self.ctrl.state().current.shoe.remaining["A"]
        out = sess.confirm(ConfirmDecision(
            "2" * 32, OP_NEW, seat="玩家1", confirmed_rank="A",
            face_state=FACE_SHOWN_LEDGER))
        self.assertEqual(out.status, "committed")
        self.assertEqual(out.event.source, SOURCE_VISION_CONFIRMED)
        self.assertEqual(out.event.confirm_status, CONFIRMED)
        self.assertTrue(out.event.evidence)
        self.assertEqual(self.ctrl.state().current.shoe.remaining["A"], before - 1)
        self.assertEqual(out.event.payload["track_id"], "vision:" + "2" * 32)

    def test_same_request_is_idempotent(self):
        sess = self.session(_obs("3" * 32, "8"))
        d = ConfirmDecision("3" * 32, OP_NEW, seat="玩家1", confirmed_rank="8")
        first = sess.confirm(d)
        rev = self.ctrl.commit_revision
        events = len(self.ctrl.ledger.events)
        second = sess.confirm(d)
        self.assertEqual(second.status, "duplicate")
        self.assertEqual(self.ctrl.commit_revision, rev)
        self.assertEqual(len(self.ctrl.ledger.events), events)
        self.assertEqual(self.ctrl.state().current.shoe.exact_out["8"], 1)

    def test_controller_event_id_retry_after_new_session_object(self):
        obs = _obs("4" * 32, "9")
        sess = self.session(obs)
        d = ConfirmDecision("4" * 32, OP_NEW, seat="玩家1", confirmed_rank="9")
        first = sess.confirm(d)
        other = VisionReviewSession(self.ctrl, _result(obs), Path(self.tmp.name) / "ev2")
        other.bound = dict(sess.bound)
        again = other.confirm(d)
        self.assertEqual(again.event.event_id, first.event.event_id)
        self.assertEqual(self.ctrl.state().current.shoe.exact_out["9"], 1)

    def test_two_eights_are_two_physical_cards(self):
        sess = self.session(_obs("5" * 32, "8"), _obs("6" * 32, "8"))
        sess.confirm(ConfirmDecision("5" * 32, OP_NEW, seat="玩家1", confirmed_rank="8"))
        sess.confirm(ConfirmDecision("6" * 32, OP_NEW, seat="玩家1", confirmed_rank="8"))
        self.assertEqual(self.ctrl.state().current.shoe.exact_out["8"], 2)
        ranks = self.ctrl.state().current.table.players["玩家1"].hands[0].ranks
        self.assertEqual(ranks, ["8", "8"])

    def test_reveal_reuses_hole_card(self):
        hidden = self.ctrl.deal_hidden("庄家")
        remaining = self.ctrl.state().current.shoe.physical_remaining()
        sess = self.session(_obs("7" * 32, "Q"))
        sess.confirm(ConfirmDecision(
            "7" * 32, OP_REVEAL, seat="庄家", confirmed_rank="Q",
            target_event_id=hidden.event_id))
        seg = self.ctrl.state().current
        self.assertEqual(seg.shoe.physical_remaining(), remaining)
        self.assertEqual(seg.shoe.unrevealed_out, 0)
        self.assertEqual(seg.table.dealer.hands[0].ranks[-1], "Q")
        self.assertEqual(sum(1 for e in self.ctrl.ledger.events if e.etype == CARD_REVEALED), 1)
        self.assertEqual(sum(1 for e in self.ctrl.ledger.events if e.etype == CARD_DEALT), 1)

    def test_stale_after_new_round(self):
        sess = self.session(_obs("8" * 32, "2"))
        self.ctrl.end_round_unsettled("测试换轮", "complete")
        self.ctrl.start_round(["玩家1"])
        with self.assertRaises(VisionBridgeError):
            sess.confirm(ConfirmDecision("8" * 32, OP_NEW, seat="玩家1", confirmed_rank="2"))
        self.assertEqual(self.ctrl.state().current.shoe.exact_out["2"], 0)

    def test_manual_recording_source_still_applied(self):
        event = self.ctrl.deal_shown("玩家1", "K")
        self.assertEqual(event.source, SOURCE_SIMULATOR)
        self.assertNotEqual(event.source, SOURCE_MANUAL)

    def test_vision_source_is_not_overwritten_by_session_default(self):
        sess = self.session(_obs("9" * 32, "K"))
        event = sess.confirm(ConfirmDecision(
            "9" * 32, OP_NEW, seat="玩家1", confirmed_rank="K")).event
        self.assertEqual(event.source, SOURCE_VISION_CONFIRMED)

    def test_unknown_confirm_blocks_analysis_gate(self):
        sess = self.session(_obs("0" * 32, rank=None, face=FACE_UNREADABLE))
        sess.confirm(ConfirmDecision(
            "0" * 32, OP_NEW, seat="玩家1", confirmed_rank=None,
            face_state=FACE_UNKNOWN_LEDGER))
        self.assertEqual(self.ctrl.state().current.shoe.pending_candidates, 1)
        with self.assertRaises(InputUnavailable):
            build_input(self.ctrl.ledger, "玩家1")

    def test_sqlite_failure_does_not_publish(self):
        sess = self.session(_obs("a" * 32, "5"))
        before = self.ctrl.ledger.to_list()
        rev = self.ctrl.commit_revision
        with patch.object(self.ctrl.store, "save_event", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                sess.confirm(ConfirmDecision("a" * 32, OP_NEW, seat="玩家1", confirmed_rank="5"))
        self.assertEqual(self.ctrl.ledger.to_list(), before)
        self.assertEqual(self.ctrl.commit_revision, rev)
        self.assertEqual(self.ctrl.state().current.shoe.exact_out["5"], 0)

    def test_human_can_map_to_ten_bucket_model_keeps_original(self):
        sess = self.session(_obs("b" * 32, "10"))
        event = sess.confirm(ConfirmDecision(
            "b" * 32, OP_NEW, seat="玩家1", confirmed_rank="T")).event
        self.assertEqual(event.payload["rank"], "T")
        evidence = Path(self.tmp.name) / "ev" / event.evidence
        text = evidence.read_text(encoding="utf-8")
        self.assertIn('"rank": "10"', text)
        self.assertIn('"human_rank": "T"', text)

    def test_correction_does_not_deal_again(self):
        original = self.ctrl.deal_shown("玩家1", "6")
        sess = self.session(_obs("c" * 32, "9"))
        sess.confirm(ConfirmDecision(
            "c" * 32, OP_CORRECT, seat="玩家1", confirmed_rank="9",
            target_event_id=original.event_id))
        seg = self.ctrl.state().current
        self.assertEqual(seg.table.players["玩家1"].hands[0].ranks, ["9"])
        self.assertEqual(seg.shoe.exact_out["6"], 0)
        self.assertEqual(seg.shoe.exact_out["9"], 1)

    def test_cannot_auto_debit_without_human_rank(self):
        sess = self.session(_obs("d" * 32, "7"))
        with self.assertRaises(VisionBridgeError):
            sess.confirm(ConfirmDecision("d" * 32, OP_NEW, seat="玩家1", confirmed_rank=None))

    def test_observed_at_uses_capture_clock_not_model_time(self):
        obs = _obs("e" * 32, "4")
        obs.captured_at = 1000.5
        sess = self.session(obs)
        event = sess.confirm(ConfirmDecision(
            "e" * 32, OP_NEW, seat="玩家1", confirmed_rank="4")).event
        self.assertEqual(event.observed_at, 1000.5)
        self.assertGreater(event.event_time, 1000.5)


if __name__ == "__main__":
    unittest.main()
