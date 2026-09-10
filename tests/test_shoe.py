# -*- coding: utf-8 -*-
"""测试组 1：6/7/8 副初始化、各牌面数量、极限扣牌与未知移除的守恒。"""
import unittest

from blackjack_lab.core.cards import RANKS, TEN_BUCKET, TEN_RANKS
from blackjack_lab.core.shoe import (
    ConsistencyError, ShoeState, STATE_ANALYZABLE, STATE_INCOMPLETE,
)

EXPECTED = {  # n_decks: (总牌数, 每牌面张数, 小牌, 大牌, 中性)
    6: (312, 24, 120, 120, 72),
    7: (364, 28, 140, 140, 84),
    8: (416, 32, 160, 160, 96),
}


class TestInit(unittest.TestCase):
    def test_counts_for_all_decks(self):
        for n, (total, per, small, big, neutral) in EXPECTED.items():
            shoe = ShoeState(n)
            self.assertEqual(shoe.total_cards, total)
            for r in RANKS:
                self.assertEqual(shoe.remaining[r], per, f"{n}副的{r}初始数量错误")
            g = shoe.group_remaining()
            self.assertEqual(g["small"], small)
            self.assertEqual(g["big"], big)
            self.assertEqual(g["neutral"], neutral)
            ok, _ = shoe.conservation_check()
            self.assertTrue(ok)
            self.assertEqual(shoe.physical_remaining(), total)
            self.assertEqual(shoe.integrity_state(), STATE_ANALYZABLE)

    def test_only_678(self):
        for bad in (1, 5, 9):
            with self.assertRaises(ValueError):
                ShoeState(bad)


class TestConservation(unittest.TestCase):
    def test_remove_and_conservation(self):
        for n in EXPECTED:
            shoe = ShoeState(n)
            shoe.remove_known("A")
            shoe.remove_known("10")
            shoe.remove_known(TEN_BUCKET)
            self.assertEqual(shoe.remaining["A"], 4 * n - 1)
            # A、10、T桶各移除一张，大牌组共少 3；T 桶不扣具体牌面
            self.assertEqual(shoe.group_remaining()["big"], 5 * 4 * n - 3)
            ok, note = shoe.conservation_check()
            self.assertTrue(ok, note)

    def test_never_negative(self):
        shoe = ShoeState(6)
        for _ in range(24):
            shoe.remove_known("A")
        with self.assertRaises(ConsistencyError):
            shoe.remove_known("A")

    def test_exhaust_whole_shoe(self):
        shoe = ShoeState(6)
        per = 24
        for r in RANKS:
            for _ in range(per):
                shoe.remove_known(r)
        self.assertEqual(shoe.physical_remaining(), 0)
        ok, note = shoe.conservation_check()
        self.assertTrue(ok, note)
        with self.assertRaises(ConsistencyError):
            shoe.remove_known("2")

    def test_ten_bucket_capacity(self):
        shoe = ShoeState(6)
        # 10/J/Q/K 共 96 张
        for _ in range(96):
            shoe.remove_known(TEN_BUCKET)
        with self.assertRaises(ConsistencyError):
            shoe.remove_known(TEN_BUCKET)
        ok, _ = shoe.conservation_check()
        self.assertTrue(ok)


class TestUnknownRemoval(unittest.TestCase):
    def test_hidden_then_reveal(self):
        shoe = ShoeState(8)
        shoe.place_unrevealed()          # 庄家暗牌
        self.assertEqual(shoe.unrevealed_out, 1)
        self.assertEqual(shoe.physical_remaining(), 416 - 1)
        # 暗牌期间不能凭空认定牌面：各牌面剩余不变
        self.assertEqual(shoe.remaining["K"], 32)
        shoe.reveal_unrevealed("K")
        self.assertEqual(shoe.unrevealed_out, 0)
        self.assertEqual(shoe.remaining["K"], 31)
        ok, note = shoe.conservation_check()
        self.assertTrue(ok, note)

    def test_reveal_without_hidden_rejected(self):
        shoe = ShoeState(6)
        with self.assertRaises(ConsistencyError):
            shoe.reveal_unrevealed("A")

    def test_burn_known_count(self):
        shoe = ShoeState(6)
        shoe.burn_known_count(2)
        self.assertEqual(shoe.burn_unknown, 2)
        self.assertEqual(shoe.physical_remaining(), 310)
        ok, _ = shoe.conservation_check()
        self.assertTrue(ok)
        with self.assertRaises(ValueError):
            shoe.burn_known_count(0)

    def test_burn_too_many_rejected(self):
        shoe = ShoeState(6)
        shoe.burn_known_count(312)
        with self.assertRaises(ConsistencyError):
            shoe.burn_known_count(1)

    def test_gap_makes_state_incomplete(self):
        shoe = ShoeState(6)
        shoe.remove_known("2")
        shoe.mark_gap("断流 40 秒，可能漏牌")
        self.assertIsNone(shoe.physical_remaining())
        self.assertEqual(shoe.integrity_state(), STATE_INCOMPLETE)
        ok, note = shoe.conservation_check()
        self.assertTrue(ok)  # 缺口下只验证不等式
        self.assertIn("观察缺口", note)


if __name__ == "__main__":
    unittest.main()
