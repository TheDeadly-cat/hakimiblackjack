import copy
from dataclasses import replace
import json
from itertools import permutations
import tempfile
import unittest
from pathlib import Path

from blackjack_lab.analysis.contracts import InputUnavailable
from blackjack_lab.analysis.opening import (build_opening_input, summarize_histogram, SAMPLES, SCHEMA, ENGINE, STRATEGY)
from blackjack_lab.analysis.opening_service import estimate_counts, validate_opening_result
from blackjack_lab.analysis.native_backend import source_digest
from blackjack_lab.analysis.split_contracts import same_value_das_research_rules
from blackjack_lab.core.rules import RuleProfile
from blackjack_lab.core.table import ACTION_STAND
from blackjack_lab.ui.controller import SessionController


class TestOpeningInput(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.db = Path(temporary.name) / 'opening.db'
        self.ctrl = SessionController(self.db)
        self.addCleanup(self.ctrl.close)
        self.ctrl.new_shoe(same_value_das_research_rules(8))

    def test_counts_identity_and_explicit_seat_order(self):
        one = build_opening_input(self.ctrl.ledger)
        self.assertEqual(one.counts, (32,) * 9 + (128,))
        multi = build_opening_input(self.ctrl.ledger, ('玩家1', '玩家3', '玩家7'), '玩家3', 'reverse')
        self.assertEqual(multi.participants, ('玩家7', '玩家3', '玩家1'))
        self.assertEqual(multi.focal, 1)
        self.assertNotEqual(one.input_digest, multi.input_digest)
        self.ctrl.start_round(['玩家1', '玩家3'], deal_direction='reverse')
        frozen = build_opening_input(self.ctrl.ledger, ('玩家1', '玩家2'), '玩家1')
        self.assertEqual(frozen.participants, ('玩家3', '玩家1'))
        self.assertEqual(frozen.focal, 1)

    def test_first_card_blocks_opening_and_undo_restores_same_sampling_seed(self):
        self.ctrl.start_round(['玩家1'])
        initial = build_opening_input(self.ctrl.ledger)
        self.ctrl.deal_shown('玩家1', 'T')
        with self.assertRaises(InputUnavailable) as error:
            build_opening_input(self.ctrl.ledger)
        self.assertEqual(error.exception.code, 'ROUND_ACTIVE')
        self.ctrl.undo_last()
        restored = build_opening_input(self.ctrl.ledger)
        self.assertEqual(restored.seed, initial.seed)
        self.assertNotEqual(restored.input_digest, initial.input_digest)

    def test_hidden_hole_and_incomplete_prior_round_never_use_guessed_counts(self):
        self.ctrl.start_round(['玩家1'])
        self.ctrl.deal_hidden('庄家')
        with self.assertRaises(InputUnavailable) as error:
            build_opening_input(self.ctrl.ledger)
        self.assertEqual(error.exception.code, 'UNREVEALED')
        self.ctrl.end_round_unsettled('independent incomplete fixture', 'unknown')
        with self.assertRaises(InputUnavailable) as error:
            build_opening_input(self.ctrl.ledger)
        self.assertEqual(error.exception.code, 'OBSERVATION_INCOMPLETE')

    def test_completed_round_updates_composition_and_recovery_keeps_input(self):
        c = self.ctrl
        c.start_round(['玩家1'])
        c.deal_shown('玩家1', 'T'); c.deal_shown('庄家', '6'); c.deal_shown('玩家1', '8')
        hole = c.deal_hidden('庄家')
        hand = c.state().current.table.players['玩家1'].hands[0]
        c.player_action('玩家1', hand.hand_id, ACTION_STAND)
        c.reveal(hole.event_id, 'A')
        c.end_round()
        snapshot = build_opening_input(c.ledger)
        self.assertEqual(sum(snapshot.counts), 412)
        self.assertEqual(snapshot.counts[0], 31)
        self.assertEqual(snapshot.counts[-1], 127)
        before = c.ledger.to_list()
        recovered = SessionController.recover(self.db, c.session_id)
        self.addCleanup(recovered.close)
        self.assertEqual(build_opening_input(recovered.ledger).input_digest, snapshot.input_digest)
        self.assertEqual(c.ledger.to_list(), before)

    def test_unknown_rules_unknown_burn_and_invalid_focal_are_refused(self):
        with self.assertRaises(InputUnavailable):
            build_opening_input(self.ctrl.ledger, ('玩家1',), '玩家2')
        self.ctrl.burn(1)
        with self.assertRaises(InputUnavailable):
            build_opening_input(self.ctrl.ledger)
        self.ctrl.undo_last()
        for changes in ({'dealer_soft17': 'H17'}, {'split_match': 'same_rank'}, {'max_split_hands': 4},
                        {'confirm_status': '未确认'}, {'resplit_aces': True}):
            snapshot = build_opening_input(self.ctrl.ledger)
            data = json.loads(snapshot.rules_json); data.update(changes)
            with self.assertRaises(InputUnavailable):
                replace(snapshot, rules_json=json.dumps(data)).validate()


class TestOpeningMath(unittest.TestCase):
    def test_single_ace_three_tens_has_exact_quarter_unit_edge(self):
        # All 24 physical orders: half player natural (+1.5), half dealer natural (-1).
        ranks = (1, 10, 10, 10)
        exact = sum(1.5 if 1 in (ranks[order[0]], ranks[order[2]]) else -1
                    for order in permutations(range(4))) / 24
        self.assertEqual(exact, .25)
        result = estimate_counts((1, 0, 0, 0, 0, 0, 0, 0, 0, 3), samples=200_000)
        self.assertLess(result['interval'][0], .25)
        self.assertGreater(result['interval'][1], .25)
        self.assertEqual(result['sign'], 'positive')
        self.assertEqual({i for i, n in enumerate(result['histogram']) if n}, {6, 11})

    def test_pure_rank_shoes_have_analytically_known_payoffs(self):
        # Aces: hit to soft21 vs soft17 => +1. Twos/threes/eights: split both
        # hands and win each => +2. Other pure ranks tie, including all sevens.
        for index, expected in [(0, 1), (1, 2), (2, 2), (3, 0), (4, 0), (5, 0), (6, 0), (7, 2), (8, 0), (9, 0)]:
            with self.subTest(rank=index + 1):
                counts = tuple((128 if i == 9 else 32) if i == index else 0 for i in range(10))
                result = estimate_counts(counts, samples=1000)
                self.assertEqual(result['ev'], expected)
                self.assertEqual(result['histogram'][8 + 2 * expected], 1000)

    def test_das_stakes_and_multiple_seats_share_finite_shoe(self):
        counts = (32,) * 9 + (128,)
        das = estimate_counts(counts, seats=3, focal=2, samples=100_000)
        no_das = estimate_counts(counts, seats=3, focal=2, das=False, samples=100_000)
        self.assertGreater(sum(das['histogram'][:4]) + sum(das['histogram'][13:]), 0)
        self.assertEqual(sum(no_das['histogram'][:4]) + sum(no_das['histogram'][13:]), 0)
        with self.assertRaises((InputUnavailable, RuntimeError)):
            estimate_counts((0, 32, 0, 0, 0, 0, 0, 0, 0, 0), seats=7, samples=100)

    def test_cross_zero_interval_is_uncertain_and_bad_histogram_is_rejected(self):
        zero = [0] * 17; zero[8] = SAMPLES
        self.assertEqual(summarize_histogram(zero, SAMPLES)['sign'], 'uncertain')
        positive = [0] * 17; positive[10] = SAMPLES
        negative = [0] * 17; negative[6] = SAMPLES
        self.assertEqual(summarize_histogram(positive, SAMPLES)['sign'], 'positive')
        self.assertEqual(summarize_histogram(negative, SAMPLES)['sign'], 'negative')
        for bad in ([0] * 17, [True] * 17, positive[:-1], [-1] + positive[1:]):
            with self.assertRaises(ValueError):
                summarize_histogram(bad, SAMPLES)


def synthetic_result(snapshot, net=1):
    histogram = [0] * 17; histogram[8 + 2 * net] = SAMPLES
    return dict(schema=SCHEMA, status='available', request_id='synthetic-ui-only', input=snapshot.to_dict(),
                input_digest=snapshot.input_digest, rules_digest=snapshot.rules_digest, engine_version=ENGINE,
                strategy_version=STRATEGY, seed=snapshot.seed, samples=SAMPLES, histogram=histogram,
                native_source_digest=source_digest(), **summarize_histogram(histogram, SAMPLES))


class TestOpeningResultGuard(unittest.TestCase):
    setUp = TestOpeningInput.setUp
    def test_no_nan_or_forged_positive_or_wrong_prefix_can_publish(self):
        snapshot = build_opening_input(self.ctrl.ledger)
        result = synthetic_result(snapshot)
        validate_opening_result(result, snapshot)
        for change in ({'ev': float('nan')}, {'advantage_percent': 0}, {'interval': [1, 2]},
                       {'sign': 'negative'}, {'input_digest': 'stale'}, {'samples': 1000}, {'native_source_digest': 'wrong'}):
            bad = copy.deepcopy(result); bad.update(change)
            with self.assertRaises(ValueError):
                validate_opening_result(bad, snapshot)
