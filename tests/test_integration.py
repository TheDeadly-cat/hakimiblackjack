# -*- coding: utf-8 -*-
"""集成测试：完整录牌场景 + 三种牌副数同一流程守恒（V0.1 验收主线）。"""
import tempfile
import unittest
from pathlib import Path

from blackjack_lab.core.rules import RuleProfile
from blackjack_lab.ledger.ledger import EventLedger
from blackjack_lab.storage.database import LocalStore
from blackjack_lab.storage.export import export_json, import_json


def run_full_session(n_decks: int) -> EventLedger:
    """两轮换靴前完整流程：暗牌/揭示/分牌/加倍/结算/撤销/纠错。"""
    led = EventLedger(f"s-{n_decks}")
    led.start_session()
    led.create_shoe(RuleProfile(
        n_decks=n_decks, split_match="same_rank",
        double_after_split=True, surrender="early", blackjack_payout=(3, 2)))
    # ---- 第 1 轮：玩家1 对 8 分牌，其中一手加倍 ----
    led.start_round()
    led.deal("玩家1", "8"); led.deal("玩家1", "8")
    led.deal("庄家", "9"); led.deal("庄家", None, hidden=True)
    table = led.replay().current.table
    hid = table.players["玩家1"].hands[0].hand_id
    info = led.player_action("玩家1", hid, "分牌").payload
    h1, h2 = hid, info["new_hand_id"]
    led.deal("玩家1", "3", hand_id=h1)
    led.deal("玩家1", "2", hand_id=h2)
    led.player_action("玩家1", h1, "加倍")
    led.deal("玩家1", "10", hand_id=h1)   # 加倍补一张自动停
    led.deal("玩家1", "K", hand_id=h2)
    led.player_action("玩家1", h2, "停牌")
    # 揭示庄家暗牌并补牌 19 点
    hidden = [e for e in led.events if e.payload.get("face_state") == "hidden"][0]
    led.reveal(hidden.event_id, "10")
    led.end_round()
    r1 = led.replay().current.settlements
    assert len(r1) == 2, f"分牌后应有两手结算，实际 {len(r1)}"

    # ---- 第 2 轮：误记一张后纠错；新轮不重置牌靴 ----
    led.start_round()
    wrong = led.deal("玩家2", "5")
    led.correct(wrong.event_id, {"rank": "6"}, reason="误录纠正")
    led.deal("玩家2", "K")
    led.deal("庄家", "A"); led.deal("庄家", None, hidden=True)
    hid2 = led.replay().current.table.players["玩家2"].hands[0].hand_id
    led.player_action("玩家2", hid2, "停牌")
    hidden2 = [e for e in led.events if e.payload.get("face_state") == "hidden"][-1]
    led.reveal(hidden2.event_id, "Q")  # 庄家 BJ
    led.end_round()

    # 撤销最后一个结算事件（ROUND_ENDED）后可重新结束本轮
    led.undo_last()
    led.end_round()

    seg = led.replay().current
    ok, note = seg.shoe.conservation_check()
    assert ok, f"{n_decks}副守恒失败：{note}"
    return led


class TestFullSessionAllDecks(unittest.TestCase):
    def test_6_7_8_decks(self):
        for n in (6, 7, 8):
            led = run_full_session(n)
            seg = led.replay().current
            self.assertEqual(seg.table.round_no, 2)
            ok, note = seg.shoe.conservation_check()
            self.assertTrue(ok, f"{n}副：{note}")
            # 纠错后 5 没有被扣，扣的是 6
            self.assertEqual(seg.shoe.exact_out["5"], 0)
            self.assertEqual(seg.shoe.exact_out["6"], 1)
            # 物理剩余 = 总牌 - 全部已说明移除
            removed = (sum(seg.shoe.exact_out.values())
                       + seg.shoe.t_bucket_out + seg.shoe.unrevealed_out
                       + seg.shoe.burn_unknown)
            self.assertEqual(seg.shoe.physical_remaining(),
                             seg.shoe.total_cards - removed)

    def test_persistence_roundtrip_of_full_session(self):
        with tempfile.TemporaryDirectory() as d:
            led = run_full_session(8)
            store = LocalStore(Path(d) / "full.db")
            store.save_ledger(led)
            recovered = store.load_ledger(led.session_id)
            store.close()
            a = led.replay().current
            b = recovered.replay().current
            self.assertEqual(a.shoe.remaining, b.shoe.remaining)
            self.assertEqual(a.settlements, b.settlements)

            p = export_json(recovered, Path(d) / "full.json")
            led3 = import_json(p)
            c = led3.replay().current
            self.assertEqual(c.shoe.remaining, a.shoe.remaining)


class TestEmptyAndEdge(unittest.TestCase):
    def test_deal_before_shoe_rejected(self):
        led = EventLedger("sx")
        led.start_session()
        with self.assertRaises(Exception):
            led.deal("玩家1", "A")

    def test_double_display_consistency(self):
        """分牌两手合并记账：结算条目数=子手数，EV 口径属 V0.2 不在此断言。"""
        led = run_full_session(6)
        results = led.replay().segments[0].settlements
        seats = {r["seat"] for r in results}
        self.assertIn("玩家1", seats)
        self.assertEqual(sum(1 for r in results if r["seat"] == "玩家1"), 2)


if __name__ == "__main__":
    unittest.main()
