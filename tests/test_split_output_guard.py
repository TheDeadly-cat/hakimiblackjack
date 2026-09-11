"""Split service output-boundary regressions; backend responses are fault injections.

Synthetic payloads check publication guards only. They are not a numerical oracle.
Run native cases separately; they exercise the real solver, not these fixtures.
"""
import copy
import unittest
from unittest.mock import patch

from blackjack_lab.analysis.information import build_input
from blackjack_lab.analysis.probability import LABELS
from blackjack_lab.analysis.split_contracts import split_research_rules
from blackjack_lab.analysis.split_service import _validate_split_probabilities
from tests.test_analysis_integration import example
from tests.test_split_workflow import split_example


def make_snapshot():
    return build_input(example(6, cards=("10", "6"), up="10",
                               rules=split_research_rules(6)), "玩家1")


def make_pair_snapshot():
    return build_input(example(6, cards=("8", "8"), up="6",
                               rules=split_research_rules(6)), "玩家1")


def make_postsplit_snapshot():
    ledger, _hand_id = split_example()
    return build_input(ledger, "玩家1")


def make_complete_snapshot():
    ledger, first = split_example(pair="A")
    ledger.deal("玩家1", "10", hand_id=first)
    second = build_input(ledger, "玩家1").hands[1].hand_id
    ledger.deal("玩家1", "10", hand_id=second)
    return build_input(ledger, "玩家1")


def valid_transport_payload():
    # Valid types, supports, mass and expectation; NOT a numerical oracle.
    labels = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "T")
    return {
        "actions": {
            "stand": {"ev": -0.5, "net_distribution": {"-1": 0.75, "0": 0.0, "1": 0.25}},
            "hit": {"ev": -0.4, "net_distribution": {"-1": 0.7, "0": 0.0, "1": 0.3}},
            "double": {"ev": -0.8, "net_distribution": {"-2": 0.7, "0": 0.0, "2": 0.3}},
            "surrender": {"ev": -0.5, "net_distribution": {"-0.5": 1.0}},
        },
        "next_draw": dict.fromkeys(labels, 0.1),
        "hit_bust": 0.2,
        "dealer_distribution": {"blackjack": 0.0, "17": 0.0, "18": 1.0,
                                "19": 0.0, "20": 0.0, "21": 0.0, "bust": 0.0},
    }


def valid_two_hand_action():
    joint = {f"{a},{b}": 0.0 for a in (-1, 0, 1) for b in (-1, 0, 1)}
    joint["-1,-1"] = 0.5
    joint["1,1"] = 0.5
    return {
        "ev": 0.0,
        "net_distribution": {"-2": 0.5, "-1": 0.0, "0": 0.0, "1": 0.0, "2": 0.5},
        "joint_distribution": joint,
        "hand_evs": [0.0, 0.0],
    }


class SplitOutputMixin:
    def assert_refused(self, result=None):
        if result is None:
            result = self.run_payload()
        self.assertNotEqual(result["status"], "available",
                            "Invalid backend output was marked available")
        self.assertEqual(result["actions"], {})
        self.assertIsNone(result["probabilities"])
        self.assertIsNone(result["highest_ev_action"])


class TestSplitOutputBoundary(SplitOutputMixin, unittest.TestCase):
    def setUp(self):
        from blackjack_lab.analysis import split_service
        self.service = split_service
        self.snapshot = make_snapshot()
        self.payload = valid_transport_payload()

    def run_payload(self):
        with patch.object(self.service, "solve_presplit_native",
                          return_value=copy.deepcopy(self.payload)) as backend:
            result = self.service.calculate_split(self.snapshot, "review-output-boundary", 5.0)
            backend.assert_called_once()
        return result

    def test_valid_shape_is_available_control(self):
        self.assertEqual(self.run_payload()["status"], "available")

    def test_bad_net_mass_is_refused_control(self):
        self.payload["actions"]["hit"]["net_distribution"]["1"] = 0.5
        self.assert_refused()

    def test_negative_next_draw_probability_is_refused(self):
        self.payload["next_draw"] = {k: 0.0 for k in self.payload["next_draw"]}
        self.payload["next_draw"].update(A=1.2, T=-0.2)
        self.assert_refused()

    def test_hit_bust_above_one_is_refused(self):
        self.payload["hit_bust"] = 1.25
        self.assert_refused()

    def test_bad_dealer_probability_mass_is_refused(self):
        self.payload["dealer_distribution"]["18"] = 1.5
        self.assert_refused()

    def test_nan_action_ev_is_refused(self):
        self.payload["actions"]["stand"]["ev"] = float("nan")
        self.assert_refused()


class TestSplitOutputBoundaryExtended(SplitOutputMixin, unittest.TestCase):
    def setUp(self):
        from blackjack_lab.analysis import split_service
        self.service = split_service
        self.snapshot = make_snapshot()
        self.payload = valid_transport_payload()

    def run_payload(self):
        with patch.object(self.service, "solve_presplit_native",
                          return_value=copy.deepcopy(self.payload)) as backend:
            result = self.service.calculate_split(self.snapshot, "review-output-boundary-extra", 5.0)
            backend.assert_called_once()
        return result

    def test_hit_bust_bool_is_refused(self):
        self.payload["hit_bust"] = True
        self.assert_refused()

    def test_hit_bust_nan_is_refused(self):
        self.payload["hit_bust"] = float("nan")
        self.assert_refused()

    def test_inf_action_ev_is_refused(self):
        self.payload["actions"]["stand"]["ev"] = float("inf")
        self.assert_refused()

    def test_empty_dealer_distribution_is_refused(self):
        self.payload["dealer_distribution"] = {}
        self.assert_refused()

    def test_missing_dealer_key_is_refused_without_zero_fill(self):
        del self.payload["dealer_distribution"]["bust"]
        self.assert_refused()

    def test_extra_dealer_key_is_refused(self):
        self.payload["dealer_distribution"]["22"] = 0.0
        self.assert_refused()

    def test_missing_next_draw_key_is_refused_without_zero_fill(self):
        del self.payload["next_draw"]["T"]
        self.assert_refused()

    def test_extra_next_draw_key_is_refused(self):
        self.payload["next_draw"]["X"] = 0.0
        self.assert_refused()


class TestSplitJointAndPostSplitGuards(SplitOutputMixin, unittest.TestCase):
    def setUp(self):
        from blackjack_lab.analysis import split_service
        self.service = split_service

    def run_presplit_pair(self, payload):
        snapshot = make_pair_snapshot()
        with patch.object(self.service, "solve_presplit_native",
                          return_value=copy.deepcopy(payload)) as backend:
            result = self.service.calculate_split(snapshot, "review-output-boundary-pair", 5.0)
            backend.assert_called_once()
        return result

    def run_postsplit(self, action_payload):
        snapshot = make_postsplit_snapshot()
        payload = {"actions": action_payload}
        with patch.object(self.service, "solve_split_counts",
                          return_value=copy.deepcopy(payload)) as backend:
            result = self.service.calculate_split(snapshot, "review-output-boundary-post", 5.0)
            backend.assert_called_once()
        return result

    def valid_pair_payload(self):
        payload = valid_transport_payload()
        payload["actions"]["split"] = valid_two_hand_action()
        return payload

    def test_pair_valid_split_shape_is_available_control(self):
        result = self.run_presplit_pair(self.valid_pair_payload())
        self.assertEqual(result["status"], "available", result.get("reason"))
        self.assertIn("joint_distribution", result["actions"]["split"])

    def test_missing_joint_is_refused(self):
        payload = self.valid_pair_payload()
        del payload["actions"]["split"]["joint_distribution"]
        self.assert_refused(self.run_presplit_pair(payload))

    def test_incomplete_five_cell_net_is_refused(self):
        payload = self.valid_pair_payload()
        del payload["actions"]["split"]["net_distribution"]["0"]
        self.assert_refused(self.run_presplit_pair(payload))

    def test_extra_net_support_is_refused(self):
        payload = self.valid_pair_payload()
        payload["actions"]["split"]["net_distribution"]["0.5"] = 0.0
        self.assert_refused(self.run_presplit_pair(payload))

    def test_nan_hand_evs_are_refused(self):
        payload = self.valid_pair_payload()
        payload["actions"]["split"]["hand_evs"] = [float("nan"), 0.0]
        self.assert_refused(self.run_presplit_pair(payload))

    def test_joint_marginal_mismatch_is_refused(self):
        payload = self.valid_pair_payload()
        payload["actions"]["split"]["hand_evs"] = [1.0, -1.0]
        self.assert_refused(self.run_presplit_pair(payload))

    def test_postsplit_valid_shape_is_available_control(self):
        result = self.run_postsplit({"deal": valid_two_hand_action()})
        self.assertEqual(result["status"], "available", result.get("reason"))
        self.assertIsNotNone(result["probabilities"])
        self.assertEqual(set(result["probabilities"]["next_target_draw"]), set(LABELS))

    def test_postsplit_nan_hand_evs_are_refused(self):
        action = valid_two_hand_action()
        action["hand_evs"] = [0.0, float("nan")]
        self.assert_refused(self.run_postsplit({"deal": action}))

    def test_postsplit_missing_joint_key_is_refused(self):
        action = valid_two_hand_action()
        del action["joint_distribution"]["0,0"]
        self.assert_refused(self.run_postsplit({"deal": action}))

    def test_complete_valid_shape_has_no_draw_metrics(self):
        snapshot = make_complete_snapshot()
        payload = {"actions": {"complete": valid_two_hand_action()}}
        with patch.object(self.service, "solve_split_counts", return_value=copy.deepcopy(payload)):
            result = self.service.calculate_split(snapshot, "review-output-boundary-complete", 5.0)
        self.assertEqual(result["status"], "available", result.get("reason"))
        self.assertEqual(result["probabilities"], {})
        self.assertNotIn("next_target_draw", result["probabilities"])
        self.assertNotIn("hit_bust", result["probabilities"])

    def test_complete_forged_zero_draw_metrics_are_refused(self):
        snapshot = make_complete_snapshot()
        forged = {
            "next_target_draw": dict.fromkeys(LABELS, 0.0),
            "hit_bust": 0.0,
        }
        with self.assertRaises(ArithmeticError):
            _validate_split_probabilities(snapshot, forged)


class TestSplitOutputNativeFocus(unittest.TestCase):
    def test_real_native_presplit_and_postsplit_pass_validation(self):
        from blackjack_lab.analysis.service import calculate
        presplit = calculate(make_snapshot(), "native-output-guard-presplit", 5.0)
        self.assertEqual(presplit["status"], "available", presplit.get("reason"))
        self.assertIsNotNone(presplit["probabilities"])
        self.assertEqual(set(presplit["probabilities"]["next_target_draw"]), set(LABELS))
        postsplit = calculate(make_postsplit_snapshot(), "native-output-guard-postsplit", 5.0)
        self.assertEqual(postsplit["status"], "available", postsplit.get("reason"))
        item = postsplit["actions"]["deal"]
        self.assertEqual(len(item["hand_evs"]), 2)
        self.assertEqual(set(item["joint_distribution"]),
                         {f"{a},{b}" for a in (-1, 0, 1) for b in (-1, 0, 1)})
        complete = calculate(make_complete_snapshot(), "native-output-guard-complete", 5.0)
        self.assertEqual(complete["status"], "available", complete.get("reason"))
        self.assertEqual(complete["probabilities"], {})


if __name__ == "__main__":
    unittest.main()
