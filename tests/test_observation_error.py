"""Synthetic observation-error window contrast. Not an unused-video result."""
import unittest

from blackjack_lab.analysis.contracts import research_rules
from blackjack_lab.analysis.observation_error import (
    apply_ledger_event_error, compare_ledger_delay, compare_ledger_event_error,
    correct_ledger_misread, run_observation_error_study, shown_public_deal_indexes,
)
from blackjack_lab.analysis.research_windows import WINDOW_PRE_DEAL
from blackjack_lab.analysis.shoe_windows import CONSUMPTION_BASIC, CONSUMPTION_STAND, play_round
from blackjack_lab.core.table import ACTION_STAND, DEALER
from blackjack_lab.ledger.events import CARD_DEALT, FACE_HIDDEN
from blackjack_lab.ledger.ledger import EventLedger


class ObservationErrorStudyTest(unittest.TestCase):
    def test_delay_cannot_claim_the_first_round_window(self):
        pack = (10, 10, 9, 9, 8, 7, 6, 5)
        report = run_observation_error_study(pack=pack, seed=3, lag_rounds=1, max_rounds=3,
                                            surrender=None)
        self.assertEqual(WINDOW_PRE_DEAL, report["window"])
        self.assertTrue(report["not_a_reliable_window_claim"])
        self.assertFalse(report["independent_video"])
        first = report["rounds"][0]
        self.assertEqual("OBSERVER_LAG", first["delay"]["reason_code"])
        self.assertIsNone(first["delay"].get("ev"))
        if first["truth"].get("status") == "available" and first["truth"].get("ev", 0) > 0:
            self.assertGreaterEqual(report["summary"]["delay"]["missed_unavailable"], 1)
            self.assertEqual(0, report["summary"]["delay"]["false_negative"])
        self.assertIn("delay_missed_positive_rate", report["summary"])
        self.assertIn("delay_missed_unavailable", report["summary"])
        if report["summary"]["truth_positive"]:
            self.assertGreaterEqual(report["summary"]["delay_missed_positive"], 0)
        self.assertTrue(report["realized_path_is_not_counterfactual_truth"])

    def test_zero_lag_matches_the_truth_snapshot(self):
        pack = (10, 10, 9, 9, 8, 7)
        report = run_observation_error_study(pack=pack, seed=3, lag_rounds=0, max_rounds=2,
                                            surrender=None)
        first = report["rounds"][0]
        self.assertEqual("zero_lag_ideal", first["delay"]["observer_kind"])
        self.assertEqual(first["truth"].get("status"), first["delay"].get("status"))
        if first["truth"].get("ev") is not None:
            self.assertAlmostEqual(first["truth"]["ev"], first["delay"]["ev"], delta=1e-12)
        self.assertEqual(0, report["summary"]["delay"]["missed_unavailable"])

    def test_rank_error_is_counted_not_used_as_a_window_claim(self):
        pack = (10, 10, 9, 9, 8, 7)
        report = run_observation_error_study(pack=pack, seed=8, max_rounds=2, surrender=None)
        self.assertIn("false_positive", report["summary"]["rank_error"])
        self.assertIn("false_negative", report["summary"]["rank_error"])
        self.assertIn("zero_window_truth", report["summary"])
        self.assertTrue(report["not_a_reliable_window_claim"])

    def test_unknown_removal_is_scored_separately_from_rank_swap(self):
        pack = (10, 10, 9, 9, 8, 7, 6, 5)
        report = run_observation_error_study(pack=pack, seed=4, lag_rounds=0, max_rounds=2,
                                            surrender=None)
        self.assertIn("unknown_removal", report["summary"])
        self.assertIn("missed_card", report["summary"])
        self.assertIn("duplicate_card", report["summary"])
        first = report["rounds"][0]
        self.assertIn("unknown_removal_observer", first)
        self.assertEqual("zero_lag_ideal", first["delay"]["observer_kind"])

    def test_basic_and_composition_policies_can_consume_different_cards(self):
        pack = [5, 10, 6, 9, 10, 10, 10, 2]
        stood = play_round(pack, policy=CONSUMPTION_STAND, budget_seconds=2.0, surrender=None)
        basic = play_round(pack, policy=CONSUMPTION_BASIC, budget_seconds=2.0, surrender=None)
        self.assertLess(len(basic["remaining"]), len(stood["remaining"]))

    def test_bust_keeps_unrevealed_hole_in_public_remaining(self):
        pack = [10, 10, 6, 9, 10, 8, 7]
        played = play_round(pack, policy=CONSUMPTION_BASIC, budget_seconds=2.0, surrender=None)
        self.assertLess(played["net"], 0)
        self.assertFalse(played["hole_revealed"])
        self.assertTrue(played["public_remaining_is_not_private_truth"])
        self.assertEqual(len(played["public_remaining"]), len(played["private_remaining"]) + 1)
        self.assertEqual(played["private_remaining"], played["remaining"])
        self.assertIn(9, played["public_remaining"])
        self.assertNotIn(9, played["private_remaining"])

    def test_dealer_blackjack_reveals_the_hole(self):
        pack = [10, 1, 6, 10, 9, 8, 7]
        played = play_round(pack, policy=CONSUMPTION_STAND, budget_seconds=2.0, surrender=None)
        self.assertTrue(played["dealer_bj"])
        self.assertTrue(played["hole_revealed"])
        self.assertEqual(played["public_remaining"], played["private_remaining"])
        self.assertFalse(played["public_remaining_is_not_private_truth"])

    def test_error_study_records_public_observer_separately(self):
        pack = [10, 10, 6, 9, 10, 8, 7]
        report = run_observation_error_study(
            pack=pack, seed=1, lag_rounds=0, max_rounds=1, play_policy=CONSUMPTION_BASIC,
            surrender=None)
        first = report["rounds"][0]
        self.assertIn("public_observer", first)
        self.assertEqual("public-cards-only", first["public_observer"]["observer_kind"])
        self.assertTrue(report["summary"]["public_observer_is_not_private_remaining"])
        self.assertIn("n_hidden_hole_after_round", report["summary"])
        self.assertTrue(report["summary"]["snapshot_remaining_is_not_event_stream"])
        self.assertIn("event_miss", report["summary"])
        self.assertIn("false_positive", report["summary"]["event_miss"])

    def test_error_study_requires_declared_surrender(self):
        with self.assertRaises(ValueError) as caught:
            run_observation_error_study(pack=(10, 10, 9, 9, 8, 7), seed=1, max_rounds=1)
        self.assertIn("不能默认晚投降", str(caught.exception))

    def test_public_events_rebuild_remaining_without_leaking_an_unrevealed_hole(self):
        from blackjack_lab.analysis.observation_error import apply_public_event_error
        from blackjack_lab.analysis.shoe_windows import remaining_after_events
        from random import Random

        revealed = play_round([10, 1, 6, 10, 9, 8, 7], policy=CONSUMPTION_STAND,
                              budget_seconds=2.0, surrender=None)
        self.assertTrue(revealed["dealer_bj"])
        self.assertTrue(revealed["hole_revealed"])
        self.assertEqual([10, 1, 6, 10], revealed["public_dealt"])
        self.assertEqual(
            remaining_after_events([10, 1, 6, 10, 9, 8, 7], revealed["public_dealt"]),
            revealed["private_remaining"])

        pack = [10, 10, 6, 9, 10, 8, 7]
        busted = play_round(pack, policy=CONSUMPTION_BASIC, budget_seconds=2.0, surrender=None)
        self.assertFalse(busted["hole_revealed"])
        self.assertNotIn(9, busted["public_dealt"])
        perfect = remaining_after_events(pack, busted["public_dealt"])
        self.assertEqual(perfect, busted["public_remaining"])
        self.assertNotEqual(perfect, busted["private_remaining"])
        missed, note = apply_public_event_error(
            pack, busted["public_dealt"], Random(1), "miss_public_deal")
        self.assertEqual("miss_public_deal", note["kind"])
        self.assertEqual(len(missed), len(perfect) + 1)
        report = run_observation_error_study(
            pack=pack, seed=1, lag_rounds=0, max_rounds=1, play_policy=CONSUMPTION_BASIC,
            surrender=None)
        first = report["rounds"][0]
        self.assertEqual("event-stream", first["event_miss"]["observer_kind"])
        self.assertEqual("perfect-public-events", first["perfect_public_events"]["observer_kind"])
        self.assertTrue(report["summary"]["snapshot_remaining_is_not_event_stream"])


def _public_deal_ledger():
    ledger = EventLedger("ledger-event-stream")
    ledger.start_session("synthetic ledger event-stream fixture")
    ledger.create_shoe(research_rules(6, surrender=None))
    ledger.start_round(["玩家1"])
    ledger.deal("玩家1", "10")
    six = ledger.deal("玩家1", "6")
    ledger.deal(DEALER, "9")
    hole = ledger.deal(DEALER, None, hidden=True)
    return ledger, six, hole


class LedgerEventStreamErrorTest(unittest.TestCase):
    def test_hidden_hole_is_not_a_public_deal_event(self):
        ledger, _six, hole = _public_deal_ledger()
        events = ledger.to_list()
        shown = shown_public_deal_indexes(events)
        self.assertEqual(3, len(shown))
        self.assertNotIn(next(i for i, event in enumerate(events)
                              if event["event_id"] == hole.event_id), shown)
        self.assertEqual(FACE_HIDDEN, hole.payload["face_state"])
        self.assertEqual(CARD_DEALT, hole.etype)

    def test_missed_shown_deal_rebuilds_observer_remaining_from_the_ledger(self):
        ledger, six, _hole = _public_deal_ledger()
        truth = dict(ledger.replay().current.shoe.remaining)
        contrast = compare_ledger_event_error(ledger, kind="miss_public_deal", shown_index=1)
        self.assertEqual("ledger-event-stream", contrast["source"])
        self.assertTrue(contrast["exact_predeal_not_claimed"])
        self.assertFalse(contrast["accepted"])
        self.assertFalse(contrast["independent_video"])
        self.assertTrue(contrast["not_a_reliable_window_claim"])
        self.assertIsNone(contrast["replay_error"])
        self.assertFalse(contrast["remaining_match"])
        self.assertEqual(six.payload["rank"], contrast["note"]["missed"])
        self.assertEqual(truth["6"] + 1, contrast["observer_remaining"]["6"])
        self.assertEqual(truth["10"], contrast["observer_remaining"]["10"])
        self.assertEqual(contrast["truth_unrevealed_out"], contrast["observer_unrevealed_out"])

    def test_duplicate_and_misread_shown_deals_change_remaining_without_an_ev_claim(self):
        ledger, _six, _hole = _public_deal_ledger()
        truth = dict(ledger.replay().current.shoe.remaining)
        duplicated = compare_ledger_event_error(
            ledger, kind="duplicate_public_deal", shown_index=0)
        self.assertIsNone(duplicated["replay_error"])
        self.assertEqual(truth["10"] - 1, duplicated["observer_remaining"]["10"])
        misread = compare_ledger_event_error(ledger, kind="misread_public_deal", shown_index=0)
        self.assertIsNone(misread["replay_error"])
        self.assertEqual("10", misread["note"]["from"])
        self.assertEqual("9", misread["note"]["to"])
        self.assertEqual(truth["10"] + 1, misread["observer_remaining"]["10"])
        self.assertEqual(truth["9"] - 1, misread["observer_remaining"]["9"])
        self.assertNotIn("ev", misread)

    def test_corrupted_ledger_copy_does_not_rewrite_the_truth_events(self):
        ledger, _six, _hole = _public_deal_ledger()
        before = ledger.to_list()
        observer_events, note = apply_ledger_event_error(
            before, kind="miss_public_deal", shown_index=1)
        self.assertEqual("miss_public_deal", note["kind"])
        self.assertEqual(before, ledger.to_list())
        self.assertEqual(len(before) - 1, len(observer_events))

    def test_zero_lag_matches_and_cannot_claim_a_zero_window(self):
        ledger, _six, _hole = _public_deal_ledger()
        contrast = compare_ledger_delay(ledger, lag_events=0)
        self.assertEqual("zero_lag", contrast["kind"])
        self.assertTrue(contrast["knowledge_match"])
        self.assertIsNone(contrast["replay_error"])
        self.assertFalse(contrast["zero_window"])
        self.assertTrue(contrast["incomplete_cannot_claim_zero_window"])
        self.assertEqual("ROUND_ALREADY_DEALT", contrast["truth_predeal"]["reason_code"])

    def test_hidden_hole_delay_keeps_rank_remaining_but_not_knowledge(self):
        ledger, _six, hole = _public_deal_ledger()
        contrast = compare_ledger_delay(ledger, lag_events=1)
        self.assertEqual("event_lag", contrast["kind"])
        self.assertEqual([hole.event_id], contrast["note"]["dropped_event_ids"])
        self.assertTrue(contrast["remaining_match"])
        self.assertFalse(contrast["knowledge_match"])
        self.assertEqual(0, contrast["observer_unrevealed_out"])
        self.assertEqual(1, contrast["truth_unrevealed_out"])
        self.assertEqual(
            contrast["truth_physical_remaining"] + 1,
            contrast["observer_physical_remaining"])
        self.assertFalse(contrast["zero_window"])

    def test_lagged_round_end_cannot_claim_the_next_predeal_window(self):
        ledger, _six, hole = _public_deal_ledger()
        ledger.reveal(hole.event_id, "8")
        hand_id = ledger.replay().current.table.players["玩家1"].hands[0].hand_id
        ledger.player_action("玩家1", hand_id, ACTION_STAND)
        ledger.end_round(settle=True, observation_status="complete")
        truth = compare_ledger_delay(ledger, lag_events=0)
        delayed = compare_ledger_delay(ledger, lag_events=1)
        self.assertEqual("PREDEAL_SHOE_TOO_LARGE", truth["truth_predeal"]["reason_code"])
        self.assertNotEqual(
            truth["truth_predeal"]["reason_code"],
            delayed["observer_predeal"]["reason_code"])
        self.assertFalse(delayed["zero_window"])
        self.assertTrue(delayed["incomplete_cannot_claim_zero_window"])
        self.assertFalse(delayed["window_reason_codes_match"])

    def test_correction_restores_remaining_after_a_shown_misread(self):
        ledger, _six, _hole = _public_deal_ledger()
        restored = correct_ledger_misread(ledger, shown_index=0)
        self.assertTrue(restored["correction_changed_remaining"])
        self.assertTrue(restored["remaining_match"])
        self.assertTrue(restored["knowledge_match"])
        self.assertFalse(restored["zero_window"])
        self.assertFalse(restored["accepted"])


if __name__ == "__main__":
    unittest.main()
