# -*- coding: utf-8 -*-
"""测试组 4/5/7：幂等去重、暗牌、撤销纠错、新轮不重置、换靴隔离、缺口。"""
import unittest

from blackjack_lab.core.cards import TEN_BUCKET
from blackjack_lab.core.rules import RuleProfile
from blackjack_lab.core.shoe import STATE_INCOMPLETE
from blackjack_lab.ledger.events import Event, new_event_id
from blackjack_lab.ledger.ledger import EventLedger, LedgerError
from blackjack_lab.ledger import events as E


def rules(n=6, **kw):
    return RuleProfile(n_decks=n, split_match="same_value",
                       double_after_split=True, surrender="early", **kw)


def fresh(n=6):
    led = EventLedger("s1")
    led.start_session()
    led.create_shoe(rules(n))
    led.start_round()
    return led


class TestIdempotent(unittest.TestCase):
    def test_same_event_id_only_once(self):
        led = fresh(6)
        eid = new_event_id()
        e1 = led.deal("玩家1", "A", event_id=eid)
        e2 = led.deal("玩家1", "A", event_id=eid)  # 同 id 且内容相同才是幂等
        self.assertIs(e1, e2)
        seg = led.replay().current
        self.assertEqual(len(seg.table.players["玩家1"].hands[0].cards), 1)

    def test_same_track_dealt_once(self):
        led = fresh(6)
        led.deal("玩家1", "A", track_id="phys-1")
        # 同一物理牌的第二帧/双展示不得再扣
        with self.assertRaises(LedgerError):
            led.deal("玩家1", "A", track_id="phys-1")
        seg = led.replay().current
        self.assertEqual(seg.shoe.exact_out["A"], 1)

    def test_same_track_allowed_after_undo(self):
        led = fresh(6)
        led.deal("玩家1", "A", track_id="phys-9")
        led.undo_last()
        led.deal("玩家1", "A", track_id="phys-9")  # 撤销后同物理牌可重新入账
        seg = led.replay().current
        self.assertEqual(seg.shoe.exact_out["A"], 1)


class TestHiddenAndUnknown(unittest.TestCase):
    def test_hidden_reveal_flow(self):
        led = fresh(8)
        led.deal("庄家", None, hidden=True)
        seg = led.replay().current
        self.assertEqual(seg.shoe.unrevealed_out, 1)
        self.assertEqual(seg.shoe.remaining["Q"], 32)  # 未揭示不认定牌面
        # 找到暗牌事件并揭示
        hidden_ev = [e for e in led.events if e.payload.get("face_state") == "hidden"][0]
        led.reveal(hidden_ev.event_id, "Q")
        seg = led.replay().current
        self.assertEqual(seg.shoe.unrevealed_out, 0)
        self.assertEqual(seg.shoe.remaining["Q"], 31)
        ok, note = seg.shoe.conservation_check()
        self.assertTrue(ok, note)

    def test_unknown_card_pending(self):
        led = fresh(6)
        led.deal("玩家1", None, unknown=True)
        seg = led.replay().current
        self.assertEqual(seg.shoe.pending_candidates, 1)
        self.assertEqual(seg.shoe.integrity_state(), "待核对")

    def test_burn_and_gap(self):
        led = fresh(6)
        led.burn(2)
        led.gap("漏牌")
        seg = led.replay().current
        self.assertEqual(seg.shoe.burn_unknown, 2)
        self.assertEqual(seg.shoe.integrity_state(), STATE_INCOMPLETE)


class TestUndoAndCorrection(unittest.TestCase):
    def test_undo_reverse_order(self):
        led = fresh(6)
        led.deal("玩家1", "A")
        led.deal("玩家1", "K")
        led.undo_last()  # 撤销 K
        seg = led.replay().current
        ranks = seg.table.players["玩家1"].hands[0].ranks
        self.assertEqual(ranks, ["A"])
        self.assertEqual(seg.shoe.exact_out["K"], 0)
        # 原始事件仍在审计链：会话/牌靴/轮次 + 两次发牌 + 一条撤销
        self.assertEqual(len(led.events), 6)

    def test_correction_equals_replay_from_correct_start(self):
        # 路径甲：误记为 K，事后纠错为 Q
        led = fresh(6)
        wrong = led.deal("玩家1", "K")
        led.correct(wrong.event_id, {"rank": "Q"}, reason="看错牌面")
        seg_a = led.replay().current
        # 路径乙：一开始就正确记 Q
        led2 = fresh(6)
        led2.deal("玩家1", "Q")
        seg_b = led2.replay().current
        self.assertEqual(seg_a.shoe.remaining, seg_b.shoe.remaining)
        self.assertEqual(
            seg_a.table.players["玩家1"].hands[0].ranks,
            seg_b.table.players["玩家1"].hands[0].ranks)
        # 原错误事件未被删除
        self.assertTrue(any(e.event_id == wrong.event_id for e in led.events))

    def test_new_round_keeps_shoe(self):
        led = fresh(6)
        led.deal("玩家1", "A")
        led.end_round()
        led.start_round()
        seg = led.replay().current
        self.assertEqual(seg.table.round_no, 2)
        self.assertEqual(seg.shoe.exact_out["A"], 1)  # 牌靴不重置

    def test_new_shoe_isolated(self):
        led = EventLedger("s2")
        led.start_session()
        led.create_shoe(rules(6))
        led.start_round()
        led.deal("玩家1", "A")
        led.end_round()
        led.end_shoe()
        led.create_shoe(rules(8))
        replay = led.replay()
        self.assertEqual(len(replay.segments), 2)
        first, cur = replay.segments[0], replay.current
        self.assertEqual(first.shoe.n_decks, 6)
        self.assertEqual(cur.shoe.n_decks, 8)
        # 新靴不混入上一靴：A 仍是满的
        self.assertEqual(cur.shoe.exact_out["A"], 0)
        self.assertEqual(cur.shoe.remaining["A"], 32)


class TestInformationLeak(unittest.TestCase):
    """记录层防泄漏：暗牌未揭示前，未来是什么牌不影响当时组成。"""
    def test_hidden_future_invisible(self):
        def build(future_rank):
            led = fresh(6)
            led.deal("庄家", None, hidden=True)
            before = led.replay().current.shoe.remaining.copy()
            hidden_ev = [e for e in led.events
                         if e.payload.get("face_state") == "hidden"][0]
            led.reveal(hidden_ev.event_id, future_rank)
            after = led.replay().current.shoe.remaining
            return before, after

        before_k, after_k = build("K")
        before_q, after_q = build("Q")
        self.assertEqual(before_k, before_q)        # 揭示前完全一致
        self.assertNotEqual(after_k, after_q)       # 揭示后才产生差异


if __name__ == "__main__":
    unittest.main()
