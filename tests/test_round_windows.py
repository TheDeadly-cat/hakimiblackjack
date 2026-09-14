"""3/6-round consumption is not multi-player EV or a retitled current-hand result."""
import unittest

from blackjack_lab.analysis.contracts import UNSUPPORTED
from blackjack_lab.analysis.predeal_contracts import PREDEAL_MAX_REMAINING
from blackjack_lab.analysis.round_windows import play_seated_round, run_round_window_study
from blackjack_lab.analysis.shoe_windows import CONSUMPTION_STAND, play_round
from blackjack_lab.core.rules import CAPABILITY_MATRIX, EXPERIMENTAL


class RoundWindowStudyTest(unittest.TestCase):
    def test_one_player_stand_matches_existing_play_round(self):
        pack = [10, 9, 6, 8, 7, 5, 4, 3]
        seated = play_seated_round(pack, n_players=1, target_policy=CONSUMPTION_STAND)
        single = play_round(pack, policy=CONSUMPTION_STAND)
        self.assertEqual(single["remaining"], seated["remaining"])
        self.assertEqual(single["net"], seated["target_net"])
        self.assertEqual([], seated["other_nets"])
        self.assertTrue(seated["not_multiplayer_ev"])

    def test_seven_players_consume_more_than_one_after_three_rounds(self):
        one = run_round_window_study(n_decks=6, n_players=1, after_rounds=(3,), seed=4)
        seven = run_round_window_study(n_decks=6, n_players=7, after_rounds=(3,), seed=4)
        self.assertEqual("shoe", one["sample_unit"])
        self.assertTrue(seven["not_independent_player_samples"])
        self.assertTrue(seven["not_multiplayer_ev"])
        self.assertFalse(seven["independent_video"])
        self.assertLess(seven["snapshots"][0]["physical_remaining"],
                        one["snapshots"][0]["physical_remaining"])
        self.assertEqual(6, seven["snapshots"][0]["other_seat_count"])
        self.assertGreater(seven["snapshots"][0]["physical_remaining"], PREDEAL_MAX_REMAINING)
        self.assertEqual(UNSUPPORTED, seven["snapshots"][0]["predeal"]["status"])
        self.assertIsNone(seven["snapshots"][0]["predeal"]["ev"])

    def test_seven_and_eight_decks_use_the_same_unsupported_predeal_path(self):
        for decks in (7, 8):
            report = run_round_window_study(n_decks=decks, n_players=1, after_rounds=(3, 6), seed=3)
            self.assertEqual(2, len(report["snapshots"]))
            for snap in report["snapshots"]:
                self.assertEqual(UNSUPPORTED, snap["predeal"]["status"])
                self.assertIsNone(snap["predeal"]["ev"])
                self.assertGreater(snap["physical_remaining"], PREDEAL_MAX_REMAINING)

    def test_six_rounds_leave_fewer_cards_than_three(self):
        report = run_round_window_study(n_decks=6, n_players=1, after_rounds=(3, 6), seed=2)
        first, second = report["snapshots"]
        self.assertEqual(3, first["after_rounds"])
        self.assertEqual(6, second["after_rounds"])
        self.assertLess(second["physical_remaining"], first["physical_remaining"])
        self.assertTrue(report["not_a_reliable_window_claim"])
        self.assertNotIn("player_ranks", first["predeal"])

    def test_small_pack_can_reach_exact_predeal_without_relabeling(self):
        pack = [10, 9, 8, 7, 6, 5, 4, 3, 2, 2, 10, 9, 8, 7, 6, 5, 4, 3]
        report = run_round_window_study(n_decks=6, n_players=1, after_rounds=(1,), seed=1,
                                        pack=pack, target_policy=CONSUMPTION_STAND)
        self.assertTrue(report["snapshots"])
        remaining = report["snapshots"][0]["physical_remaining"]
        self.assertLessEqual(remaining, PREDEAL_MAX_REMAINING)
        self.assertIn(report["snapshots"][0]["predeal"]["status"],
                      ("available", "timeout", "unsupported", "inapplicable", "failed"))
        self.assertNotIn("player_ranks", report["snapshots"][0]["predeal"])

    def test_capability_row_stays_experimental(self):
        status, note = CAPABILITY_MATRIX["前三/六轮消耗对照"]
        self.assertEqual(EXPERIMENTAL, status)
        self.assertIn("独立样本", note)
        self.assertIn("多玩家EV", note)
