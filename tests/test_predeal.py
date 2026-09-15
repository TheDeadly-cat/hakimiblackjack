"""Small-shoe pre-deal EV: independent permutations, BJ branches, no hole leak."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from blackjack_lab.analysis.actions import solve_counts
from blackjack_lab.analysis.contracts import FAILED, INAPPLICABLE, PENDING, UNSUPPORTED, research_rules
from blackjack_lab.analysis.predeal import calculate_predeal, solve_predeal_counts
from blackjack_lab.analysis.predeal_contracts import (
    PREDEAL_MAX_REMAINING, PREDEAL_RESULT_SCHEMA, PreDealInput,
)
from blackjack_lab.analysis.research_windows import (
    WINDOW_PRE_DEAL, build_predeal_input, counts_from_values, format_predeal_result,
    parse_remaining_tokens,
)
from blackjack_lab.analysis.service import calculate
from blackjack_lab.core.rules import RuleProfile
from blackjack_lab.storage.analysis_snapshots import AnalysisSnapshots
from blackjack_lab.ui.controller import SessionController
from tests.independent_six_card_oracle import PACK as SIX_CARD_PACK, compute as six_card_oracle
from tests.independent_small_shoe import (
    ACE_THREE_TENS, FOUR_TENS, ace_six_vs_two_three_tens, ace_three_tens,
    five_five_vs_nine_three_tens, four_tens,
)
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

    def test_missing_rules_are_not_silently_late_surrender(self):
        with self.assertRaises(Exception) as caught:
            build_predeal_input(counts=counts_from_values(SIX_CARD_PACK))
        self.assertEqual("PREDEAL_RULES_MISSING", caught.exception.code)
        self.assertEqual(FAILED, caught.exception.status)
        with self.assertRaises(ValueError) as solver:
            solve_predeal_counts(counts_from_values(SIX_CARD_PACK))
        self.assertIn("不能默认晚投降", str(solver.exception))

    def test_small_shoe_matches_independent_permutations(self):
        cards = (10, 10, 9, 9, 8, 7)
        snapshot = build_predeal_input(
            counts=counts_from_values(cards), rules=research_rules(6, surrender="late"))
        self.assertEqual(WINDOW_PRE_DEAL, snapshot.window)
        self.assertEqual("late", snapshot.surrender)
        actual = calculate(snapshot)
        expected = predeal_reference(cards)
        oracle = six_card_oracle(True)
        self.assertEqual("available", actual["status"])
        self.assertEqual(PREDEAL_RESULT_SCHEMA, actual["schema"])
        self.assertAlmostEqual(actual["ev"], float(expected["ev"]), delta=1e-9)
        self.assertAlmostEqual(actual["ev"], 5 / 36, delta=1e-9)
        self.assertAlmostEqual(actual["ev"], oracle["ev"], delta=1e-9)
        self.assertEqual("positive_supported", actual["window_state"])
        self.assertIn("surrender", actual["legal_actions"])
        self.assertAlmostEqual(sum(actual["net_distribution"].values()), 1.0, delta=1e-10)
        self.assertAlmostEqual(
            actual["outcomes"]["win"] + actual["outcomes"]["push"] + actual["outcomes"]["lose"],
            1.0, delta=1e-10)
        got = _float_dist(actual["net_distribution"])
        want = _float_dist(expected["net_distribution"])
        keys = set(got) | set(want)
        for key in keys:
            self.assertAlmostEqual(got.get(key, 0.0), want.get(key, 0.0), delta=1e-9, msg=key)

    def test_no_surrender_six_card_builder_matches_independent_oracle(self):
        cards = SIX_CARD_PACK
        snapshot = build_predeal_input(
            counts=counts_from_values(cards), rules=research_rules(6, surrender=None))
        self.assertIsNone(snapshot.surrender)
        self.assertIn("no-surrender", snapshot.support_scope)
        actual = calculate(snapshot)
        expected = predeal_reference(cards, surrender=None)
        oracle = six_card_oracle(False)
        self.assertEqual("available", actual["status"])
        self.assertIsNone(actual["surrender"])
        self.assertNotIn("surrender", actual["legal_actions"])
        self.assertAlmostEqual(actual["ev"], 0.0, delta=1e-9)
        self.assertAlmostEqual(actual["ev"], float(expected["ev"]), delta=1e-9)
        self.assertAlmostEqual(actual["ev"], oracle["ev"], delta=1e-9)
        self.assertEqual("nonpositive_supported", actual["window_state"])
        self.assertEqual("0", oracle["ev_fraction"])
        for key, probability in actual["net_distribution"].items():
            if abs(float(key) + 0.5) < 1e-9:
                self.assertAlmostEqual(float(probability), 0.0, delta=1e-15, msg=key)
        text = format_predeal_result(actual)
        self.assertIn("无投降", text)
        self.assertNotIn("晚投降", text)
        self.assertIn("窗口状态：可评且非正", text)

    def test_format_does_not_invent_late_surrender_when_rule_is_missing(self):
        result = calculate(build_predeal_input(
            counts=counts_from_values(SIX_CARD_PACK), rules=research_rules(6, surrender=None)))
        stripped = dict(result)
        del stripped["surrender"]
        info = dict(stripped.get("input") or {})
        info.pop("surrender", None)
        stripped["input"] = info
        text = format_predeal_result(stripped)
        strategy = [line for line in text.splitlines() if line.startswith("策略")]
        self.assertEqual(["策略：投降规则未写入结果，不能按研究模板补全。"], strategy)
        self.assertNotIn("晚投降", text)

    def test_unknown_surrender_token_does_not_display_as_late(self):
        result = calculate(build_predeal_input(
            counts=counts_from_values(SIX_CARD_PACK), rules=research_rules(6, surrender=None)))
        forged = dict(result)
        forged["surrender"] = "early"
        info = dict(forged.get("input") or {})
        info["surrender"] = "early"
        forged["input"] = info
        text = format_predeal_result(forged)
        self.assertNotIn("晚投降", text)
        self.assertIn("投降规则未写入结果，不能按研究模板补全。", text)

    def test_late_surrender_six_card_is_not_the_no_surrender_result(self):
        cards = SIX_CARD_PACK
        late = calculate(build_predeal_input(
            counts=counts_from_values(cards), rules=research_rules(6, surrender="late")))
        none = calculate(build_predeal_input(
            counts=counts_from_values(cards), rules=research_rules(6, surrender=None)))
        oracle_late = six_card_oracle(True)
        self.assertAlmostEqual(late["ev"], 5 / 36, delta=1e-9)
        self.assertEqual("5/36", oracle_late["ev_fraction"])
        self.assertGreater(late["ev"] - none["ev"], 0.1)
        self.assertTrue(any(abs(float(key) + 0.5) < 1e-9 and float(p) > 0
                            for key, p in late["net_distribution"].items()))
        self.assertIn("surrender", late["legal_actions"])
        self.assertIn("晚投降", format_predeal_result(late))

    def test_recompute_from_saved_rules_keeps_no_surrender(self):
        snapshot = build_predeal_input(
            counts=counts_from_values(SIX_CARD_PACK),
            rules=research_rules(6, surrender=None),
            session_id="synthetic-predeal")
        rules = RuleProfile.from_json(snapshot.rules_json)
        again = build_predeal_input(
            counts=snapshot.counts, rules=rules, session_id=snapshot.session_id)
        self.assertIsNone(again.surrender)
        self.assertEqual(snapshot.rules_digest, again.rules_digest)

    def test_ace_ten_shoe_keeps_dealer_blackjack_branch(self):
        cards = (1, 10, 10, 9, 8, 7)
        legal = solve_predeal_counts(counts_from_values(cards), surrender="late")
        skipped = solve_predeal_counts(
            counts_from_values(cards), illegal_skip_dealer_bj=True, surrender="late")
        expected = predeal_reference(cards, surrender="late")
        self.assertAlmostEqual(legal["ev"], float(expected["ev"]), delta=1e-9)
        self.assertNotAlmostEqual(legal["ev"], skipped["ev"], delta=1e-6)
        self.assertTrue(any(float(key) < 0 for key in legal["net_distribution"]))
        text = format_predeal_result(calculate_predeal(build_predeal_input(
            counts=counts_from_values(cards), rules=research_rules(6, surrender="late"))))
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
            solve_predeal_counts(counts_from_values(cards), surrender="late")
        self.assertTrue(calls)
        for left, _player, up, peek in calls:
            self.assertEqual(left, remaining - 3)
            self.assertEqual(peek, up in (1, 10))

    def test_predeal_is_not_one_dealt_hand(self):
        cards = (10, 10, 9, 6, 8, 7)
        counts = counts_from_values(cards)
        opening = solve_predeal_counts(counts, surrender="late")
        dealt = list(counts)
        dealt[9] -= 1
        dealt[5] -= 1
        dealt[8] -= 1
        current = solve_counts(tuple(dealt), (10, 6), 9, False,
                               actions=("stand", "hit", "double", "surrender"))
        best = max(item["ev"] for item in current["actions"].values())
        self.assertNotAlmostEqual(opening["ev"], best, delta=1e-4)

    def test_snapshot_roundtrip_keeps_predeal_schema(self):
        snapshot = build_predeal_input(
            counts=counts_from_values(parse_remaining_tokens("10,10,9,9,8,7")),
            rules=research_rules(6, surrender="late"))
        result = calculate(snapshot)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = AnalysisSnapshots(tmp.name)
        saved = store.save(result)
        loaded = store.load(saved["snapshot_id"])
        self.assertEqual(PREDEAL_RESULT_SCHEMA, loaded["result"]["schema"])
        self.assertAlmostEqual(loaded["result"]["ev"], result["ev"], delta=1e-12)
        self.assertEqual("late", loaded["result"]["surrender"])
        self.assertIn("surrender", loaded["result"]["legal_actions"])
        self.assertEqual("synthetic-composition", loaded["result"]["source_mode"])
        self.assertFalse(loaded["result"]["timely"])
        self.assertTrue(loaded["result"]["not_a_reliable_window_claim"])
        self.assertFalse(loaded["timely_live_claim"])

    def test_synthetic_predeal_is_not_a_timely_live_claim(self):
        from blackjack_lab.analysis.contracts import canonical
        from blackjack_lab.analysis.predeal_contracts import PREDEAL_STRATEGY_VERSION
        from blackjack_lab.storage.analysis_snapshots import SnapshotFormatError
        snapshot = build_predeal_input(
            counts=counts_from_values(SIX_CARD_PACK), rules=research_rules(6, surrender=None))
        result = calculate(snapshot)
        self.assertEqual(WINDOW_PRE_DEAL, result["window_kind"])
        self.assertEqual("synthetic-composition", result["source_mode"])
        self.assertEqual(PREDEAL_STRATEGY_VERSION, result["strategy_id"])
        self.assertEqual(snapshot.prefix_digest, result["ledger_prefix_digest"])
        self.assertEqual(0, result["information_cutoff"])
        self.assertFalse(result["timely"])
        self.assertTrue(result["not_a_reliable_window_claim"])
        self.assertEqual("nonpositive_supported", result["window_state"])
        self.assertIsNotNone(result["result_ready_at"])
        self.assertIsNone(result["decision_deadline"])
        payload = snapshot.to_dict()
        info = json.loads(payload["information_json"])
        info["source"] = "ledger-prefix"
        payload["information_json"] = canonical(info)
        ledger_like = calculate(PreDealInput.from_dict(payload))
        self.assertEqual("ledger-prefix", ledger_like["source_mode"])
        self.assertFalse(ledger_like["timely"])
        self.assertFalse(ledger_like["not_a_reliable_window_claim"])
        forged = dict(result)
        forged["timely"] = True
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with self.assertRaises(SnapshotFormatError):
            AnalysisSnapshots(tmp.name).save(forged)
        forged_state = dict(result)
        forged_state["window_state"] = "positive_supported"
        with self.assertRaises(SnapshotFormatError):
            AnalysisSnapshots(tmp.name).save(forged_state)

    def test_snapshot_roundtrip_keeps_no_surrender_and_rejects_stripped_rule(self):
        from blackjack_lab.storage.analysis_snapshots import SnapshotFormatError
        snapshot = build_predeal_input(
            counts=counts_from_values(SIX_CARD_PACK), rules=research_rules(6, surrender=None))
        result = calculate(snapshot)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = AnalysisSnapshots(tmp.name)
        saved = store.save(result)
        loaded = store.load(saved["snapshot_id"])
        body = loaded["result"]
        self.assertIsNone(body["surrender"])
        self.assertNotIn("surrender", body["legal_actions"])
        self.assertAlmostEqual(body["ev"], 0.0, delta=1e-12)
        for key, probability in body["net_distribution"].items():
            if abs(float(key) + 0.5) < 1e-9:
                self.assertAlmostEqual(float(probability), 0.0, delta=1e-15)
        stripped = dict(body)
        del stripped["surrender"]
        with self.assertRaises(SnapshotFormatError):
            store.save(stripped)
        missing_input = dict(body)
        missing_input["input"] = dict(body["input"])
        del missing_input["input"]["surrender"]
        with self.assertRaises(SnapshotFormatError):
            store.save(missing_input)
        with self.assertRaises(ValueError) as caught:
            PreDealInput.from_dict({key: value for key, value in snapshot.to_dict().items()
                                    if key != "surrender"})
        self.assertIn("不能默认晚投降", str(caught.exception))

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

    def test_settled_leftover_cards_are_not_the_next_round_deal(self):
        from blackjack_lab.core.table import PHASE_SETTLED
        from blackjack_lab.analysis.information import build_input
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ctrl = SessionController(Path(tmp.name) / "predeal-settled.db")
        self.addCleanup(ctrl.close)
        ctrl.new_shoe(research_rules(6, surrender=None))
        ctrl.start_round(["玩家1"])
        ctrl.deal_shown("庄家", "9")
        ctrl.deal_shown("庄家", "8")
        ctrl.deal_shown("玩家1", "10")
        ctrl.deal_shown("玩家1", "6")
        ctrl.end_round()
        table = ctrl.ledger.replay().current.table
        self.assertEqual(PHASE_SETTLED, table.phase)
        self.assertTrue(any(card for seat in (table.dealer, *table.players.values())
                            for hand in seat.hands for card in hand.cards))
        with self.assertRaises(Exception) as current_hand:
            build_input(ctrl.ledger, "玩家1")
        self.assertEqual("ROUND_INACTIVE", current_hand.exception.code)
        with self.assertRaises(Exception) as caught:
            build_predeal_input(ctrl.ledger)
        self.assertEqual("PREDEAL_SHOE_TOO_LARGE", caught.exception.code)
        self.assertNotEqual("ROUND_ALREADY_DEALT", caught.exception.code)
        self.assertEqual(UNSUPPORTED, caught.exception.status)

    def test_unsettled_leftover_cards_are_not_the_next_round_deal(self):
        from blackjack_lab.core.table import PHASE_UNSETTLED
        from blackjack_lab.analysis.information import build_input
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ctrl = SessionController(Path(tmp.name) / "predeal-unsettled.db")
        self.addCleanup(ctrl.close)
        ctrl.new_shoe(research_rules(6, surrender=None))
        ctrl.start_round(["玩家1"])
        ctrl.deal_shown("庄家", "9")
        ctrl.deal_shown("庄家", "8")
        ctrl.deal_shown("玩家1", "10")
        ctrl.deal_shown("玩家1", "6")
        ctrl.end_round_unsettled("尚不统计收益", observation_status="complete")
        table = ctrl.ledger.replay().current.table
        self.assertEqual(PHASE_UNSETTLED, table.phase)
        self.assertTrue(any(card for seat in (table.dealer, *table.players.values())
                            for hand in seat.hands for card in hand.cards))
        with self.assertRaises(Exception) as current_hand:
            build_input(ctrl.ledger, "玩家1")
        self.assertEqual("ROUND_INACTIVE", current_hand.exception.code)
        with self.assertRaises(Exception) as caught:
            build_predeal_input(ctrl.ledger)
        self.assertEqual("PREDEAL_SHOE_TOO_LARGE", caught.exception.code)
        self.assertNotEqual("ROUND_ALREADY_DEALT", caught.exception.code)
        self.assertEqual(UNSUPPORTED, caught.exception.status)

    def test_unsettled_incomplete_observation_is_not_already_dealt(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ctrl = SessionController(Path(tmp.name) / "predeal-unsettled-gap.db")
        self.addCleanup(ctrl.close)
        ctrl.new_shoe(research_rules(6, surrender=None))
        ctrl.start_round(["玩家1"])
        ctrl.deal_shown("庄家", "9")
        ctrl.deal_shown("玩家1", "10")
        ctrl.end_round_unsettled("上一轮漏录", observation_status="unknown")
        with self.assertRaises(Exception) as caught:
            build_predeal_input(ctrl.ledger)
        self.assertNotEqual("ROUND_ALREADY_DEALT", caught.exception.code)
        self.assertIn(caught.exception.code, ("PRIOR_ROUND_OBSERVATION", "RECORD_GAP"))

    def test_float_bool_nan_inf_are_not_coerced_to_counts(self):
        import math
        with self.assertRaises(ValueError):
            counts_from_values((1.8, 10, 10, 10))
        with self.assertRaises(ValueError):
            counts_from_values((True, 10, 10, 10))
        with self.assertRaises(ValueError):
            counts_from_values((10, 10, 10, -1))
        with self.assertRaises(ValueError):
            counts_from_values((10, 10, 10, math.nan))
        with self.assertRaises(ValueError):
            counts_from_values((10, 10, 10, math.inf))
        with self.assertRaises(ValueError):
            parse_remaining_tokens("1.8,10,10,10")
        with self.assertRaises(Exception) as caught:
            build_predeal_input(counts=(1.9,) + (0,) * 9)
        self.assertEqual("PREDEAL_COUNTS_INVALID", caught.exception.code)
        self.assertEqual(FAILED, caught.exception.status)
        with self.assertRaises(Exception) as caught:
            build_predeal_input(counts=(True,) + (0,) * 9)
        self.assertEqual("PREDEAL_COUNTS_INVALID", caught.exception.code)
        self.assertEqual(16, PREDEAL_MAX_REMAINING)

    def test_four_tens_terminal_is_push_not_insufficient_cards(self):
        oracle = four_tens()
        self.assertEqual("0", oracle["ev_fraction"])
        self.assertFalse(oracle["needs_draw"])
        snapshot = build_predeal_input(
            counts=counts_from_values(FOUR_TENS), rules=research_rules(6, surrender=None))
        actual = calculate(snapshot)
        expected = predeal_reference(FOUR_TENS, surrender=None)
        self.assertEqual("available", actual["status"])
        self.assertAlmostEqual(actual["ev"], 0.0, delta=1e-9)
        self.assertAlmostEqual(actual["outcomes"]["push"], 1.0, delta=1e-9)
        self.assertAlmostEqual(actual["ev"], oracle["ev"], delta=1e-9)
        self.assertAlmostEqual(actual["ev"], float(expected["ev"]), delta=1e-9)
        self.assertAlmostEqual(sum(actual["net_distribution"].values()), 1.0, delta=1e-10)

    def test_ace_three_tens_keeps_natural_and_dealer_bj_branches(self):
        oracle = ace_three_tens()
        self.assertEqual("1/4", oracle["ev_fraction"])
        snapshot = build_predeal_input(
            counts=counts_from_values(ACE_THREE_TENS), rules=research_rules(6, surrender=None))
        actual = calculate(snapshot)
        expected = predeal_reference(ACE_THREE_TENS, surrender=None)
        self.assertEqual("available", actual["status"])
        self.assertAlmostEqual(actual["ev"], 0.25, delta=1e-9)
        self.assertAlmostEqual(actual["ev"], oracle["ev"], delta=1e-9)
        self.assertAlmostEqual(actual["ev"], float(expected["ev"]), delta=1e-9)
        self.assertTrue(any(abs(float(key) - 1.5) < 1e-9 and float(p) > 0
                            for key, p in actual["net_distribution"].items()))
        self.assertTrue(any(float(key) < 0 and float(p) > 0
                            for key, p in actual["net_distribution"].items()))

    def test_five_five_vs_nine_double_matches_independent_oracle(self):
        oracle = five_five_vs_nine_three_tens()
        self.assertEqual("double", oracle["best"])
        self.assertEqual("2", oracle["double_ev_fraction"])
        solved = solve_counts(
            counts_from_values(oracle["remaining_including_hole"]),
            tuple(oracle["player"]), oracle["up"], oracle["peek"],
            actions=("stand", "hit", "double"))
        self.assertAlmostEqual(solved["actions"]["stand"]["ev"], oracle["stand_ev"], delta=1e-12)
        self.assertAlmostEqual(solved["actions"]["hit"]["ev"], oracle["hit_ev"], delta=1e-12)
        self.assertAlmostEqual(solved["actions"]["double"]["ev"], oracle["double_ev"], delta=1e-12)
        self.assertGreater(solved["actions"]["double"]["ev"], solved["actions"]["hit"]["ev"])
        self.assertGreater(solved["actions"]["hit"]["ev"], solved["actions"]["stand"]["ev"])

    def test_soft_seventeen_hit_converts_ace_instead_of_busting(self):
        oracle = ace_six_vs_two_three_tens()
        self.assertTrue(oracle["soft_converts"])
        self.assertEqual(-1.0, oracle["hit_if_ace_stays_eleven"])
        solved = solve_counts(
            counts_from_values(oracle["remaining_including_hole"]),
            tuple(oracle["player"]), oracle["up"], oracle["peek"],
            actions=("stand", "hit"))
        self.assertAlmostEqual(solved["actions"]["stand"]["ev"], oracle["stand_ev"], delta=1e-12)
        self.assertAlmostEqual(solved["actions"]["hit"]["ev"], oracle["hit_ev"], delta=1e-12)
        self.assertNotAlmostEqual(solved["actions"]["hit"]["ev"], oracle["hit_if_ace_stays_eleven"])

    def test_unsplit_t_bucket_does_not_block_point_value_predeal(self):
        import json
        from blackjack_lab.analysis.contracts import canonical
        from blackjack_lab.core.cards import TEN_BUCKET

        snapshot = build_predeal_input(
            counts=counts_from_values(SIX_CARD_PACK), rules=research_rules(6, surrender=None))
        payload = snapshot.to_dict()
        info = json.loads(payload["information_json"])
        info["t_bucket_out"] = 1
        info["ten_rank_identity_known"] = False
        payload["information_json"] = canonical(info)
        restored = PreDealInput.from_dict(payload)
        restored.validate()
        actual = calculate(restored)
        self.assertEqual("available", actual["status"])
        self.assertAlmostEqual(actual["ev"], 0.0, delta=1e-12)
        self.assertNotIn(-0.5, {float(key) for key in actual["net_distribution"]})
        text = format_predeal_result(actual)
        self.assertIn("十点未细分", text)
        self.assertNotIn("晚投降", text)

        info["unrevealed_out"] = 1
        payload["information_json"] = canonical(info)
        with self.assertRaises(ValueError):
            PreDealInput.from_dict(payload).validate()

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ctrl = SessionController(Path(tmp.name) / "predeal-t-bucket.db")
        self.addCleanup(ctrl.close)
        ctrl.new_shoe(research_rules(6, surrender=None))
        ctrl.start_round(["玩家1"])
        ctrl.deal_shown("庄家", "9")
        ctrl.deal_shown("庄家", "8")
        ctrl.deal_shown("玩家1", TEN_BUCKET)
        ctrl.deal_shown("玩家1", "6")
        ctrl.end_round()
        self.assertGreater(ctrl.ledger.replay().current.shoe.t_bucket_out, 0)
        with self.assertRaises(Exception) as caught:
            build_predeal_input(ctrl.ledger)
        self.assertEqual("PREDEAL_SHOE_TOO_LARGE", caught.exception.code)
        self.assertNotEqual("COMPOSITION_UNKNOWN", caught.exception.code)

    def test_independent_oracles_do_not_import_production(self):
        from pathlib import Path
        root = Path(__file__).resolve().parent
        for name in ("independent_six_card_oracle.py", "independent_small_shoe.py"):
            text = (root / name).read_text(encoding="utf-8")
            self.assertNotIn("blackjack_lab", text)
            self.assertNotIn("solve_predeal_counts", text)
            self.assertNotIn("solve_counts", text)
