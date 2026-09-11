# -*- coding: utf-8 -*-
"""测试组 3：分牌、再分牌、加倍、投降、庄家 Blackjack 的合法性与结算。"""
import unittest

from blackjack_lab.core.rules import RuleProfile
from blackjack_lab.core.table import (
    ACTION_DOUBLE, ACTION_SPLIT, ACTION_SURRENDER, DEALER, PHASE_SETTLED,
    TableError, TableState, player_seat_name,
)


def rules(**kw) -> RuleProfile:
    base = dict(n_decks=6, split_match="same_rank", max_split_hands=4, dealer_soft17="S17",
                double_after_split=True, surrender=None, blackjack_payout=(3, 2))
    base.update(kw)
    return RuleProfile(**base)


class TestSplitLegality(unittest.TestCase):
    def test_same_rank_split_ok(self):
        t = TableState(rules())
        t.start_round([player_seat_name(1)])
        t.add_card(player_seat_name(1), "8")
        t.add_card(player_seat_name(1), "8")
        ok, _ = t._pair_split_eligible(t.players[player_seat_name(1)].hands[0])
        self.assertTrue(ok)

    def test_same_rank_rejects_mixed_value(self):
        t = TableState(rules(split_match="same_rank"))
        t.start_round([player_seat_name(1)])
        t.add_card(player_seat_name(1), "10")
        t.add_card(player_seat_name(1), "Q")
        ok, reason = t._pair_split_eligible(t.players[player_seat_name(1)].hands[0])
        self.assertFalse(ok)
        self.assertIn("相同牌面", reason)

    def test_same_value_allows_10_Q(self):
        t = TableState(rules(split_match="same_value"))
        t.start_round([player_seat_name(1)])
        t.add_card(player_seat_name(1), "10")
        t.add_card(player_seat_name(1), "Q")
        ok, _ = t._pair_split_eligible(t.players[player_seat_name(1)].hands[0])
        self.assertTrue(ok)

    def test_ten_bucket_pair_undecidable(self):
        from blackjack_lab.core.cards import TEN_BUCKET
        t = TableState(rules(split_match="same_rank"))
        t.start_round([player_seat_name(1)])
        t.add_card(player_seat_name(1), TEN_BUCKET)
        t.add_card(player_seat_name(1), "J")
        ok, reason = t._pair_split_eligible(t.players[player_seat_name(1)].hands[0])
        self.assertFalse(ok)
        self.assertIn("无法判断", reason)

    def test_max_split_hands(self):
        t = TableState(rules(max_split_hands=2))
        t.start_round([player_seat_name(1)])
        p = player_seat_name(1)
        t.add_card(p, "8"); t.add_card(p, "8")
        t.enter_play_phase()
        t.apply_action(p, t.players[p].hands[0].hand_id, ACTION_SPLIT)
        # 两只手后达到上限
        hand = t.players[p].hands[0]
        t.add_card(p, "8", hand_id=hand.hand_id)
        ok, reason = t._pair_split_eligible(hand)
        self.assertFalse(ok)
        self.assertIn("最大分牌手数", reason)

    def test_no_resplit_aces(self):
        t = TableState(rules(resplit_aces=False))
        t.start_round([player_seat_name(1)])
        p = player_seat_name(1)
        t.add_card(p, "A"); t.add_card(p, "A")
        t.enter_play_phase()
        t.apply_action(p, t.players[p].hands[0].hand_id, ACTION_SPLIT)
        h1 = t.players[p].hands[0]
        t.add_card(p, "A", hand_id=h1.hand_id)
        ok, reason = t._pair_split_eligible(h1)
        self.assertFalse(ok)
        self.assertIn("再分 A", reason)


class TestDouble(unittest.TestCase):
    def test_double_only_two_cards(self):
        t = TableState(rules())
        t.start_round([player_seat_name(1)])
        p = player_seat_name(1)
        t.add_card(p, "5"); t.add_card(p, "6")
        t.enter_play_phase()
        hid = t.players[p].hands[0].hand_id
        t.apply_action(p, hid, ACTION_DOUBLE)
        self.assertTrue(t.players[p].hands[0].doubled)
        self.assertTrue(t.players[p].hands[0].awaiting_hit)
        # 加倍后只能补一张，补完自动停牌
        t.add_card(p, "10", hand_id=hid)
        self.assertTrue(t.players[p].hands[0].stood)

    def test_double_after_split_disallowed(self):
        t = TableState(rules(double_after_split=False))
        t.start_round([player_seat_name(1)])
        p = player_seat_name(1)
        t.add_card(p, "8"); t.add_card(p, "8")
        t.enter_play_phase()
        info = t.apply_action(p, t.players[p].hands[0].hand_id, ACTION_SPLIT)
        new_hid = info["new_hand_id"]
        t.add_card(p, "3", hand_id=new_hid)
        legal = t.legal_actions(p, new_hid)
        self.assertIn("分牌后加倍", legal[ACTION_DOUBLE])
        with self.assertRaises(TableError):
            t.apply_action(p, new_hid, ACTION_DOUBLE)

    def test_double_total_restriction(self):
        t = TableState(rules(double_on_totals=(9, 10, 11)))
        t.start_round([player_seat_name(1)])
        p = player_seat_name(1)
        t.add_card(p, "A"); t.add_card(p, "6")  # 软 17，不在允许点数
        t.enter_play_phase()
        hid = t.players[p].hands[0].hand_id
        legal = t.legal_actions(p, hid)
        self.assertIn("仅允许", legal[ACTION_DOUBLE])


class TestSurrender(unittest.TestCase):
    def test_unsupported_refused(self):
        t = TableState(rules(surrender=None))
        t.start_round([player_seat_name(1)])
        p = player_seat_name(1)
        t.add_card(p, "9"); t.add_card(p, "8")
        t.enter_play_phase()
        hid = t.players[p].hands[0].hand_id
        with self.assertRaises(TableError):
            t.apply_action(p, hid, ACTION_SURRENDER)

    def test_late_surrender_waits_peek(self):
        t = TableState(rules(surrender="late"))
        t.start_round([player_seat_name(1)])
        p = player_seat_name(1)
        t.add_card(DEALER, "A", hidden=False)
        t.add_card(DEALER, None, hidden=True)
        t.add_card(p, "9"); t.add_card(p, "8")
        t.enter_play_phase()
        hid = t.players[p].hands[0].hand_id
        with self.assertRaises(TableError):
            t.apply_action(p, hid, ACTION_SURRENDER)
        t.mark_peek_negative()
        info = t.apply_action(p, hid, ACTION_SURRENDER)
        self.assertEqual(info["action"], ACTION_SURRENDER)


class TestSettlement(unittest.TestCase):
    def _setup_with_dealer(self, t, dealer_ranks):
        t.start_round([player_seat_name(1)])
        # 庄家第一张明牌，第二张暗牌后揭示
        t.add_card(DEALER, dealer_ranks[0])
        t.add_card(DEALER, None, hidden=True)
        for r in dealer_ranks[2:]:
            t.add_card(DEALER, r)
        return [e for e in t.dealer.hands[0].cards]

    def _reveal_dealer(self, t, rank):
        t.reveal_card(DEALER, t.dealer.hands[0].hand_id, None, rank)

    def test_player_win(self):
        t = TableState(rules())
        self._setup_with_dealer(t, ["10", None])
        p = player_seat_name(1)
        t.add_card(p, "10"); t.add_card(p, "10")  # 20
        self._reveal_dealer(t, "8")               # 庄家 18
        t.enter_play_phase()
        hid = t.players[p].hands[0].hand_id
        t.apply_action(p, hid, "停牌")
        res = t.settle()
        self.assertEqual(res[0]["result"], "胜")
        self.assertEqual(res[0]["net_units"], 1.0)
        self.assertEqual(t.phase, PHASE_SETTLED)

    def test_natural_bj_pays_3_2(self):
        t = TableState(rules(blackjack_payout=(3, 2)))
        self._setup_with_dealer(t, ["10", None])
        p = player_seat_name(1)
        t.add_card(p, "A"); t.add_card(p, "K")
        self._reveal_dealer(t, "7")
        t.enter_play_phase()
        res = t.settle()
        self.assertEqual(res[0]["result"], "玩家BJ")
        self.assertAlmostEqual(res[0]["net_units"], 1.5)

    def test_push_and_bust(self):
        t = TableState(rules())
        self._setup_with_dealer(t, ["K", None])
        p = player_seat_name(1)
        t.add_card(p, "Q"); t.add_card(p, "7")  # 17
        self._reveal_dealer(t, "7")             # 17 平
        t.enter_play_phase()
        t.apply_action(p, t.players[p].hands[0].hand_id, "停牌")
        self.assertEqual(t.settle()[0]["result"], "平")

    def test_dealer_bj_beats_player(self):
        t = TableState(rules())
        self._setup_with_dealer(t, ["A", None])
        p = player_seat_name(1)
        t.add_card(p, "10"); t.add_card(p, "9")
        self._reveal_dealer(t, "J")
        t.enter_play_phase()
        res = t.settle()
        self.assertEqual(res[0]["result"], "庄家BJ负")
        self.assertEqual(res[0]["net_units"], -1.0)

    def test_surrender_loses_half(self):
        t = TableState(rules(surrender="early"))
        self._setup_with_dealer(t, ["9", None])
        p = player_seat_name(1)
        t.add_card(p, "10"); t.add_card(p, "6")
        self._reveal_dealer(t, "7")
        t.enter_play_phase()
        hid = t.players[p].hands[0].hand_id
        t.apply_action(p, hid, ACTION_SURRENDER)
        res = t.settle()
        self.assertEqual(res[0]["result"], "投降")
        self.assertEqual(res[0]["net_units"], -0.5)

    def test_settle_requires_dealer_revealed(self):
        t = TableState(rules())
        t.start_round([player_seat_name(1)])
        t.add_card(DEALER, "10"); t.add_card(DEALER, None, hidden=True)
        p = player_seat_name(1)
        t.add_card(p, "10"); t.add_card(p, "10")
        t.enter_play_phase()
        with self.assertRaises(TableError):
            t.settle()

    def test_split_ace_one_card_only(self):
        t = TableState(rules(split_ace_hit_once=True))
        t.start_round([player_seat_name(1)])
        p = player_seat_name(1)
        t.add_card(p, "A"); t.add_card(p, "A")
        t.enter_play_phase()
        t.apply_action(p, t.players[p].hands[0].hand_id, ACTION_SPLIT)
        h0 = t.players[p].hands[0]
        t.add_card(p, "9", hand_id=h0.hand_id)  # 分 A 限补一张后自动停
        self.assertTrue(h0.stood)


if __name__ == "__main__":
    unittest.main()
