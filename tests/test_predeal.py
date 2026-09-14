"""Small-shoe pre-deal EV: independent permutations, BJ branches, no hole leak."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from blackjack_lab.analysis.actions import solve_counts
from blackjack_lab.analysis.contracts import INAPPLICABLE, PENDING, UNSUPPORTED, research_rules
from blackjack_lab.analysis.predeal import calculate_predeal, solve_predeal_counts
from blackjack_lab.analysis.predeal_contracts import PREDEAL_MAX_REMAINING, PREDEAL_RESULT_SCHEMA
from blackjack_lab.analysis.research_windows import (
    WINDOW_PRE_DEAL, build_predeal_input, counts_from_values, format_predeal_result,
    parse_remaining_tokens,
)
from blackjack_lab.analysis.service import calculate
from blackjack_lab.storage.analysis_snapshots import AnalysisSnapshots
from blackjack_lab.ui.controller import SessionController
from tests.predeal_reference import predeal_reference


def _float_dist(dist):
    merged = {}
    for key, probability in dist.items():
        value = float(key)
        merged[value] = merged.get(value, 0.0) + float(probability)
    return merged


class PreDealExactTest(unittest.TestCase):
    def test_empty_call_is_missing_input_not_a_current_hand_result(self):
        with self.assertRaises(Exception) as caught:
            build_predeal_input()
        self.assertEqual("PREDEAL_INPUT_MISSING", caught.exception.code)
        self.assertEqual(PENDING, caught.exception.status)

    def test_full_six_deck_remaining_is_unsupported_exact_entry(self):
        counts = (24,) * 9 + (96,)
        with self.assertRaises(Exception) as caught:
            build_predeal_input(counts=counts)
        self.assertEqual(UNSUPPORTED, caught.exception.status)
        self.assertEqual("PREDEAL_SHOE_TOO_LARGE", caught.exception.code)
        self.assertIn(str(PREDEAL_MAX_REMAINING), caught.exception.reason)

    def test_too_few_cards_are_inapplicable(self):
        with self.assertRaises(Exception) as caught:
            build_predeal_input(counts=counts_from_values((10, 9, 8)))
        self.assertEqual("PREDEAL_TOO_FEW_CARDS", caught.exception.code)
        self.assertEqual(INAPPLICABLE, caught.exception.status)

    def test_small_shoe_matches_independent_permutations(self):
        cards = (10, 10, 9, 9, 8, 7)
        snapshot = build_predeal_input(counts=counts_from_values(cards))
        self.assertEqual(WINDOW_PRE_DEAL, snapshot.window)
        actual = calculate(snapshot)
        expected = predeal_reference(cards)
        self.assertEqual("available", actual["status"])
        self.assertEqual(PREDEAL_RESULT_SCHEMA, actual["schema"])
        self.assertAlmostEqual(actual["ev"], float(expected["ev"]), delta=1e-9)
        self.assertAlmostEqual(sum(actual["net_distribution"].values()), 1.0, delta=1e-10)
        self.assertAlmostEqual(
            actual["outcomes"]["win"] + actual["outcomes"]["push"] + actual["outcomes"]["lose"],
            1.0, delta=1e-10)
        got = _float_dist(actual["net_distribution"])
        want = _float_dist(expected["net_distribution"])
        keys = set(got) | set(want)
        for key in keys:
            self.assertAlmostEqual(got.get(key, 0.0), want.get(key, 0.0), delta=1e-9, msg=key)

    def test_ace_ten_shoe_keeps_dealer_blackjack_branch(self):
        cards = (1, 10, 10, 9, 8, 7)
        legal = solve_predeal_counts(counts_from_values(cards))
        skipped = solve_predeal_counts(counts_from_values(cards), illegal_skip_dealer_bj=True)
        expected = predeal_reference(cards)
        self.assertAlmostEqual(legal["ev"], float(expected["ev"]), delta=1e-9)
        self.assertNotAlmostEqual(legal["ev"], skipped["ev"], delta=1e-6)
        self.assertTrue(any(float(key) < 0 for key in legal["net_distribution"]))
        text = format_predeal_result(calculate_predeal(build_predeal_input(counts=counts_from_values(cards))))
        self.assertIn("发牌前开局优势", text)
        self.assertNotIn("当前已发手牌条件优势", text)
        self.assertIn("合成剩余组成", text)

    def test_play_after_visible_deal_does_not_see_a_resolved_hole(self):
        cards = (10, 10, 9, 8, 7, 6)
        remaining = 6
        calls = []
        real = solve_counts

        def wrapped(counts, player, up, peek, **kwargs):
            calls.append((sum(counts), player, up, peek))
            return real(counts, player, up, peek, **kwargs)

        with patch("blackjack_lab.analysis.predeal.solve_counts", wrapped):
            solve_predeal_counts(counts_from_values(cards))
        self.assertTrue(calls)
        for left, _player, up, peek in calls:
            self.assertEqual(left, remaining - 3)
            self.assertEqual(peek, up in (1, 10))

    def test_predeal_is_not_one_dealt_hand(self):
        cards = (10, 10, 9, 6, 8, 7)
        counts = counts_from_values(cards)
        opening = solve_predeal_counts(counts)
        dealt = list(counts)
        dealt[9] -= 1
        dealt[5] -= 1
        dealt[8] -= 1
        current = solve_counts(tuple(dealt), (10, 6), 9, False,
                               actions=("stand", "hit", "double", "surrender"))
        best = max(item["ev"] for item in current["actions"].values())
        self.assertNotAlmostEqual(opening["ev"], best, delta=1e-4)

    def test_snapshot_roundtrip_keeps_predeal_schema(self):
        snapshot = build_predeal_input(counts=counts_from_values(parse_remaining_tokens("10,10,9,9,8,7")))
        result = calculate(snapshot)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = AnalysisSnapshots(tmp.name)
        saved = store.save(result)
        loaded = store.load(saved["snapshot_id"])
        self.assertEqual(PREDEAL_RESULT_SCHEMA, loaded["result"]["schema"])
        self.assertAlmostEqual(loaded["result"]["ev"], result["ev"], delta=1e-12)

    def test_ledger_full_shoe_and_dealt_round_are_rejected(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ctrl = SessionController(Path(tmp.name) / "predeal.db")
        self.addCleanup(ctrl.close)
        ctrl.new_shoe(research_rules(6))
        with self.assertRaises(Exception) as caught:
            build_predeal_input(ctrl.ledger)
        self.assertEqual("PREDEAL_SHOE_TOO_LARGE", caught.exception.code)
        self.assertEqual(UNSUPPORTED, caught.exception.status)
        ctrl.start_round(["玩家1"])
        with self.assertRaises(Exception) as caught:
            build_predeal_input(ctrl.ledger)
        self.assertEqual("PREDEAL_SHOE_TOO_LARGE", caught.exception.code)
        ctrl.deal_shown("庄家", "9")
        with self.assertRaises(Exception) as caught:
            build_predeal_input(ctrl.ledger)
        self.assertEqual("ROUND_ALREADY_DEALT", caught.exception.code)
        self.assertEqual(INAPPLICABLE, caught.exception.status)
