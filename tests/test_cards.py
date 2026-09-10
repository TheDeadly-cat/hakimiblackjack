# -*- coding: utf-8 -*-
"""测试组 2：多 A 软硬点数、天然 Blackjack 与普通 21 的区分。"""
import unittest

from blackjack_lab.core.cards import (
    Card, TEN_BUCKET, UNKNOWN, hand_total, is_natural_blackjack, ranks_group,
)


class TestHandTotal(unittest.TestCase):
    def test_hard_total(self):
        self.assertEqual(hand_total(["K", "8"]), (18, False))
        self.assertEqual(hand_total(["10", "Q", "2"]), (22, False))

    def test_soft_single_ace(self):
        self.assertEqual(hand_total(["A", "8"]), (19, True))
        self.assertEqual(hand_total(["A", "5"]), (16, True))

    def test_multi_aces_demote(self):
        # 11+11+9=31 → 一张 A 降为 1 → 21，仍有一张按 11 → 软 21
        self.assertEqual(hand_total(["A", "A", "9"]), (21, True))
        # 三张 A + 7：11+1+1+7=20，软
        self.assertEqual(hand_total(["A", "A", "A", "7"]), (20, True))
        # A+A：11+11=22 → 12 软
        self.assertEqual(hand_total(["A", "A"]), (12, True))

    def test_unknown(self):
        self.assertEqual(hand_total([UNKNOWN]), (None, False))
        # 未知牌不猜点值，只按已知的 9 计
        self.assertEqual(hand_total([UNKNOWN, "9"]), (None, False))

    def test_ten_bucket_counts_as_ten(self):
        self.assertEqual(hand_total([TEN_BUCKET, "6"]), (16, False))


class TestNaturalBlackjack(unittest.TestCase):
    def test_natural(self):
        self.assertTrue(is_natural_blackjack(["A", "K"]))
        self.assertTrue(is_natural_blackjack(["Q", "A"]))
        self.assertTrue(is_natural_blackjack(["A", TEN_BUCKET]))

    def test_three_card_21_is_not_natural(self):
        self.assertFalse(is_natural_blackjack(["A", "5", "5"]))

    def test_two_card_non_bj(self):
        self.assertFalse(is_natural_blackjack(["9", "Q"]))  # 19
        self.assertFalse(is_natural_blackjack(["7", "Q"]))  # 17


class TestCardAndGroup(unittest.TestCase):
    def test_invalid_rank_rejected(self):
        with self.assertRaises(ValueError):
            Card(rank="JOKER")
        with self.assertRaises(ValueError):
            Card(rank="A", suit="X")

    def test_group(self):
        self.assertEqual(ranks_group("5"), "small")
        self.assertEqual(ranks_group("8"), "neutral")
        self.assertEqual(ranks_group("K"), "big")
        self.assertEqual(ranks_group(TEN_BUCKET), "big")
        self.assertEqual(ranks_group(UNKNOWN), "unknown")


if __name__ == "__main__":
    unittest.main()
