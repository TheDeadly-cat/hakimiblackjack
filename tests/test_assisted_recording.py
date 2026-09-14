"""Draft isolation, same-card correction, replay and failure boundaries."""
import json
from pathlib import Path
import tempfile
import unittest

from blackjack_lab.core.rules import RuleProfile
from blackjack_lab.core.table import ACTION_SPLIT
from blackjack_lab.ledger.events import CARD_DEALT, CORRECTION
from blackjack_lab.ledger.ledger import EventLedger
from blackjack_lab.ui.controller import SessionController
from blackjack_lab.ui.assisted_recording import AssistedRecording, DraftError


class AssistedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ctrl = SessionController(Path(self.tmp.name) / "test.db")
        self.ctrl.new_shoe(RuleProfile(n_decks=6, split_match="same_rank"))
        self.ctrl.start_round()
        self.work = AssistedRecording(self.ctrl)
        self.source = Path(self.tmp.name) / "source.png"
        self.source.write_bytes(b"fixture-only")

    def tearDown(self):
        self.work.close()
        self.ctrl.close()
        self.tmp.cleanup()

    def deal(self, rank, seat="玩家1"):
        self.work.seat, self.work.hand_id = seat, None
        d = self.work.manual()
        self.work.edit(rank=rank)
        self.work.confirm(d.draft_id)
        return self.ctrl.ledger._find(d.event_id)

    def test_draft_edit_does_not_change_state_then_single_commit(self):
        before = self.ctrl.ledger.to_list()
        d = self.work.manual()
        self.work.edit(rank="6")
        self.work.edit(rank="K")
        self.assertEqual(before, self.ctrl.ledger.to_list())
        self.work.confirm(d.draft_id)
        with self.assertRaisesRegex(DraftError, "已经处理"):
            self.work.confirm(d.draft_id)
        self.assertEqual(1, self.ctrl.state().current.shoe.exact_out["K"])
        self.assertEqual(0, self.ctrl.state().current.shoe.exact_out["6"])

    def test_rank_correction_preserves_event_identity_and_historical_prefix(self):
        event = self.deal("6")
        before = self.ctrl.ledger.replay(through_seq=event.seq).current.shoe.remaining.copy()
        d = self.work.history(event.event_id)
        self.work.edit(rank="K", reason="原图为 K")
        correction = self.work.confirm(d.draft_id)
        shoe = self.ctrl.state().current.shoe
        self.assertEqual((24, 23), (shoe.remaining["6"], shoe.remaining["K"]))
        self.assertEqual(event.event_id, self.work.records()[0]["event_id"])
        self.assertEqual(CORRECTION, correction.etype)
        self.assertEqual(before, self.ctrl.ledger.replay(through_seq=event.seq).current.shoe.remaining)
        self.assertEqual("6", event.payload["rank"])

    def test_unknown_then_reveal_and_correct_reveal_never_adds_card(self):
        d = self.work.manual()
        self.work.edit(face="unknown")
        original = self.work.confirm(d.draft_id)
        self.assertEqual(1, self.ctrl.state().current.shoe.unrevealed_out)
        d = self.work.history(original.event_id)
        self.work.edit(operation="reveal", rank="6", face="shown")
        reveal = self.work.confirm(d.draft_id)
        d = self.work.history(original.event_id)
        self.work.edit(rank="K", reason="复核牌级")
        correction = self.work.confirm(d.draft_id)
        self.assertEqual(reveal.event_id, correction.payload["target_event_id"])
        self.assertEqual(1, len(self.work.records()))
        self.assertEqual(0, self.ctrl.state().current.shoe.unrevealed_out)
        self.assertEqual(1, self.ctrl.state().current.shoe.exact_out["K"])
        self.assertEqual(1, self.ctrl.ledger.replay(through_seq=original.seq).current.shoe.unrevealed_out)

    def test_historical_duplicate_withdrawal_restores_only_extra_card_and_undo_restores(self):
        first = self.deal("3")
        extra = self.deal("3")
        last = self.deal("2", "玩家2")
        d = self.work.history(extra.event_id)
        self.work.edit(operation="withdraw", reason="重复记录同一张")
        self.work.confirm(d.draft_id)
        self.assertEqual({first.event_id, last.event_id}, {r["event_id"] for r in self.work.records()})
        self.assertEqual(1, self.ctrl.state().current.shoe.exact_out["3"])
        self.assertTrue(self.ctrl.ledger.is_voided(extra.event_id))
        self.ctrl.undo_last()
        self.assertFalse(self.ctrl.ledger.is_voided(extra.event_id))
        self.assertEqual(2, self.ctrl.state().current.shoe.exact_out["3"])

    def test_ownership_correction_and_reveal_follow_the_original_card(self):
        event = self.ctrl.deal_unknown("玩家1", confirm_status="已确认")
        self.ctrl.reveal(event.event_id, "4")
        self.ctrl.correct(event.event_id, {"seat": "玩家2", "hand_id": "p2-first"}, "纠正座位")
        rows = self.work.records()
        self.assertEqual(("玩家2", "4", event.event_id), (rows[0]["seat"], rows[0]["rank"], rows[0]["event_id"]))
        recovered = EventLedger.from_list(self.ctrl.session_id, self.ctrl.ledger.to_list())
        self.assertEqual("4", recovered.replay().current.table.players["玩家2"].hands[0].cards[0].rank)

    def test_correction_cannot_give_two_seats_the_same_hand_identity(self):
        a = self.deal("3", "玩家1")
        b = self.deal("4", "玩家2")
        before = self.ctrl.ledger.to_list()
        with self.assertRaisesRegex(Exception, "属于其他座位"):
            self.ctrl.correct(b.event_id, {"hand_id": a.payload["hand_id"]}, "错误目标")
        self.assertEqual(before, self.ctrl.ledger.to_list())

    def test_split_contradiction_rejected_without_persistence(self):
        event = self.deal("8")
        self.deal("8")
        hand = self.ctrl.state().current.table.players["玩家1"].hands[0]
        self.ctrl.player_action("玩家1", hand.hand_id, ACTION_SPLIT)
        before = self.ctrl.ledger.to_list()
        d = self.work.history(event.event_id)
        self.work.edit(rank="K", reason="导致原分牌不成立")
        with self.assertRaises(Exception):
            self.work.confirm(d.draft_id)
        self.assertEqual(before, self.ctrl.ledger.to_list())
        self.assertEqual(before, self.ctrl.store.load_ledger(self.ctrl.session_id).to_list())

    def test_queue_pins_current_repeated_track_ignored_equal_rank_not_merged(self):
        self.work.connect("source-1", lambda: "")
        def add(key):
            return self.work.enqueue(key=key, rank="Q", original={"prediction": "Q"},
                                     source_image=self.source, crop_image=self.source)
        first, second = add("track-1"), add("track-2")
        self.assertIsNone(add("track-1"))
        self.assertEqual(first.draft_id, self.work.selected_id)
        self.assertEqual(2, len(self.work.pending))
        self.work.next()
        self.assertEqual(second.draft_id, self.work.selected_id)
        self.assertEqual(0, len(self.work.records()))

    def test_source_stops_or_changes_blocks_current_without_retargeting(self):
        state = [""]
        self.work.connect("source-1", lambda: state[0])
        d = self.work.enqueue(key="t", rank="K", original={}, source_image=self.source, crop_image=self.source)
        state[0] = "来源停止"
        with self.assertRaisesRegex(DraftError, "来源停止"):
            self.work.confirm(d.draft_id)
        self.work.connect("source-2", lambda: "")
        with self.assertRaisesRegex(DraftError, "来源已改变"):
            self.work.confirm(d.draft_id)
        self.assertEqual(0, len(self.work.records()))

    def test_end_round_then_undo_does_not_reactivate_old_drafts(self):
        d = self.work.manual()
        self.work.edit(rank="K")
        self.ctrl.end_round_unsettled("测试未结算")
        self.ctrl.undo_last()
        with self.assertRaisesRegex(DraftError, "轮次已改变"):
            self.work.confirm(d.draft_id)

    def test_ledger_changed_while_editing_history_requires_reopen(self):
        event = self.deal("3")
        d = self.work.history(event.event_id)
        self.work.edit(rank="4", reason="改牌")
        self.ctrl.deal_shown("玩家2", "2")
        with self.assertRaisesRegex(DraftError, "账本有变化"):
            self.work.confirm(d.draft_id)

    def test_save_failure_leaves_draft_pending_and_no_ledger_change(self):
        d = self.work.manual()
        self.work.edit(rank="K")
        before = self.ctrl.ledger.to_list()
        from unittest.mock import patch
        with patch.object(self.ctrl.store, "save_event", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.work.confirm(d.draft_id)
        self.assertEqual(before, self.ctrl.ledger.to_list())
        self.assertEqual("pending", d.status)

    def test_notification_failure_after_durable_commit_never_repeats_debit(self):
        def fail():
            raise RuntimeError("view failed")
        self.ctrl.add_context_listener(fail)
        event = self.deal("5")
        self.assertEqual(CARD_DEALT, event.etype)
        self.assertEqual("committed", self.work.selected.status)
        self.assertEqual(1, len(self.work.records()))
        self.ctrl.remove_context_listener(fail)

    def test_feedback_retains_original_and_is_not_training_approval(self):
        event = self.deal("6")
        d = self.work.history(event.event_id)
        self.work.edit(rank="K", reason="原图复核")
        self.work.confirm(d.draft_id)
        receipt = json.loads(Path(self.ctrl.ledger.events[-1].evidence).read_text(encoding="utf-8"))
        self.assertEqual("6", receipt["draft"]["original"]["record_before"]["rank"])
        self.assertEqual("K", receipt["draft"]["rank"])
        self.assertFalse(receipt["human_training_approval"])

    def test_automatic_backlog_does_not_block_manual_supplement_or_correction(self):
        event = self.deal("4")
        self.work.capacity = 1
        self.work.connect("src", lambda: "")
        self.work.enqueue(key="a", rank="3", original={}, source_image=self.source, crop_image=self.source)
        self.assertIsNone(self.work.enqueue(key="b", rank="3", original={}, source_image=self.source, crop_image=self.source))
        self.work.history(event.event_id)
        self.work.edit(rank="5", reason="人工修正")
        self.work.confirm(self.work.selected_id)
        self.deal("2", "玩家2")
        self.assertEqual(2, len(self.work.records()))
        self.assertEqual(1, self.work.overflow)

    def test_missing_or_modified_source_cannot_be_confirmed(self):
        self.work.connect("src", lambda: "")
        d = self.work.enqueue(key="a", rank="3", original={}, source_image=self.source, crop_image=self.source)
        self.source.write_bytes(b"changed")
        with self.assertRaisesRegex(DraftError, "证据缺失或被修改"):
            self.work.confirm(d.draft_id)
        self.assertEqual(0, len(self.work.records()))

    def test_historical_round_correction_keeps_current_round_and_prefix_separate(self):
        event = self.deal("6")
        first_round = event.round_id
        self.ctrl.end_round_unsettled("画面已结束，观察未核对")
        self.ctrl.start_round()
        self.deal("4", "玩家2")
        d = self.work.history(event.event_id)
        self.work.edit(rank="K", reason="历史原图为 K")
        self.work.confirm(d.draft_id)
        self.assertEqual(["4"], [r["rank"] for r in self.work.records()])
        self.assertEqual(["K"], [r["rank"] for r in self.work.records(first_round)])
        self.assertEqual("6", self.ctrl.ledger.replay(event.seq).current.table.players["玩家1"].hands[0].ranks[0])
        d = self.work.history(event.event_id)
        self.work.edit(operation="move", seat="玩家3", hand_id="historical-p3", reason="历史座位修正")
        self.work.confirm(d.draft_id)
        self.assertEqual("玩家3", self.work.records(first_round)[0]["seat"])

    def test_withdrawal_rejects_a_dependent_reveal(self):
        d = self.work.manual()
        self.work.edit(face="unknown")
        event = self.work.confirm(d.draft_id)
        self.ctrl.reveal(event.event_id, "4")
        d = self.work.history(event.event_id)
        self.work.edit(operation="withdraw", reason="需要进一步处理揭示事件")
        before = self.ctrl.ledger.to_list()
        with self.assertRaises(Exception):
            self.work.confirm(d.draft_id)
        self.assertEqual(before, self.ctrl.ledger.to_list())

    def test_restoration_reconciles_actual_commits_and_keeps_old_source_blocked(self):
        self.deal("4")
        self.work.connect("src", lambda: "")
        d = self.work.enqueue(key="a", rank="3", original={}, source_image=self.source, crop_image=self.source)
        self.work.close()
        self.work = AssistedRecording(self.ctrl)
        self.assertEqual(1, len(self.work.records()))
        self.assertEqual(1, len(self.work.pending))
        self.assertIn("恢复", self.work.items[d.draft_id].blocked)

    def test_explicit_same_card_link_does_not_write_ledger(self):
        event = self.deal("4")
        self.work.connect("src", lambda: "")
        d = self.work.enqueue(key="a", rank="4", original={}, source_image=self.source, crop_image=self.source)
        self.work.select(d.draft_id)
        self.work.edit(operation="link", target_id=event.event_id)
        before = self.ctrl.ledger.to_list()
        self.work.confirm(d.draft_id)
        self.assertEqual("linked", d.status)
        self.assertEqual(before, self.ctrl.ledger.to_list())


if __name__ == "__main__":
    unittest.main()
