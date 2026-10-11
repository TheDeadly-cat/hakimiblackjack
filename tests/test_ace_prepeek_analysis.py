"""Before-peek mixtures, strict identity/storage, and one real bounded worker."""
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
import json
import math
from pathlib import Path
import tempfile
from time import perf_counter, sleep
import unittest
from unittest.mock import patch

from blackjack_lab.analysis.ace_prepeek_contracts import (
    AcePrepeekInput, ACE_PREPEEK_INPUT_SCHEMA, ACE_PREPEEK_RESULT_SCHEMA)
from blackjack_lab.analysis.ace_prepeek_service import compose_ace_prepeek_result, validate_ace_prepeek_result
from blackjack_lab.analysis.contracts import InputUnavailable, canonical, digest, research_rules
from blackjack_lab.analysis.information import build_input
from blackjack_lab.analysis.probability import FiniteModel, LABELS, total
from blackjack_lab.analysis.service import AnalysisService, calculate, parse_analysis_input
from blackjack_lab.ledger.ledger import EventLedger
from blackjack_lab.storage.analysis_snapshots import AnalysisSnapshots, SnapshotFormatError
from blackjack_lab.ui.controller import SessionController
from blackjack_lab.ui.table_modes import bclc_rules


def observed_rules():
    return replace(bclc_rules(), start_from_new_shoe=True, burn_cards_known=True, initial_burn_count=0)


def initial_ledger(cards=('9','8'), participants=None):
    ledger = EventLedger('owned-ace-prepeek-fixture')
    ledger.start_session('Owned synthetic source; not a real table')
    ledger.create_shoe(observed_rules())
    ledger.start_round(participants or ['玩家1'])
    ledger.deal('庄家', 'A'); ledger.deal('庄家', hidden=True)
    for rank in cards:
        ledger.deal('玩家1', rank)
    if participants and len(participants) > 1:
        for seat in participants[1:]:
            ledger.deal(seat, 'A'); ledger.deal(seat, 'T')
    return ledger


def conditional_fixture(snapshot):
    """Owned mathematical branch data; tests the adapter, not native accuracy."""
    draw = FiniteModel(1, True).target_draw(snapshot.counts)
    bust = math.fsum(p for i,p in enumerate(draw)
        if total(sum(snapshot.player)+i+1, 1 in snapshot.player or i == 0) > 21)
    actions = {}
    for action in snapshot.kernel_actions:
        if snapshot.natural_blackjack:
            dist = {'0':0., '1.5':1.}
        elif action == 'double':
            dist = {'-2':.2, '0':.3, '2':.5}
        elif action == 'split':
            dist = {'-2':.25, '-1':0., '0':.25, '1':0., '2':.5}
        else:
            dist = {'-1':.3, '0':.1, '1':.6}
        item = dict(ev=math.fsum(float(net)*p for net,p in dist.items()), net_distribution=dist)
        if action == 'split':
            joint = {f'{a},{b}':0. for a in (-1,0,1) for b in (-1,0,1)}
            joint.update({'-1,-1':.25, '0,0':.25, '1,1':.5})
            item.update(joint_distribution=joint, hand_evs=[.25,.25])
        actions[action] = item
    return dict(actions=actions, next_draw=dict(zip(LABELS, draw)), hit_bust=bust,
        dealer_distribution=dict(blackjack=0., **{'17':.2,'18':.2,'19':.2,'20':.2,'21':.1,'bust':.1}))


class AcePrepeekAnalysisTests(unittest.TestCase):
    def snapshot(self, cards=('9','8'), participants=None):
        ledger = initial_ledger(cards, participants)
        return ledger, build_input(ledger, '玩家1', other_players_stand=bool(participants))

    def test_public_fact_actual_pool_probability_and_parser_never_write_peek(self):
        ledger = initial_ledger()
        before = ledger.to_list()
        snapshot = build_input(ledger, '玩家1')
        self.assertIsInstance(snapshot, AcePrepeekInput)
        self.assertEqual(snapshot.dealer_bj_fraction, Fraction(128,413))
        self.assertEqual(snapshot.physical_remaining, 412)
        self.assertFalse(snapshot.peek_negative)
        self.assertFalse(json.loads(snapshot.information_json)['negative_peek'])
        self.assertEqual(parse_analysis_input(snapshot.to_dict()).input_digest, snapshot.input_digest)
        self.assertEqual(ledger.to_list(), before)
        self.assertFalse(any(e.etype == 'PEEK_NEGATIVE' for e in ledger.events))

    def test_double_split_mix_only_original_bet_on_bj_and_keep_conditional_joint(self):
        ledger, snapshot = self.snapshot(('8','8'))
        before = ledger.to_list(); native = conditional_fixture(snapshot); preserved = deepcopy(native)
        result = compose_ace_prepeek_result(snapshot, 'owned-mixture', native)
        p = snapshot.dealer_bj_probability
        for action in ('double','split'):
            item = result['actions'][action]
            self.assertAlmostEqual(item['net_distribution']['-1'], p)
            self.assertAlmostEqual(item['ev'], (1-p)*native['actions'][action]['ev'] - p)
            self.assertAlmostEqual(sum(item['net_distribution'].values()), 1)
            self.assertEqual(item['additional_investment'], 1)
            self.assertAlmostEqual(item['expected_additional_investment'], 1-p)
            self.assertNotIn('joint_distribution', item)
            self.assertNotIn('hand_evs', item)
        self.assertEqual(result['conditional_non_bj'], preserved)
        self.assertEqual(native, preserved)
        self.assertEqual(result['branches']['dealer_bj']['net_distribution'], {'-1':1.})
        self.assertFalse(result['branches']['dealer_bj']['actions_executed'])
        self.assertEqual(result['conditional_draw']['next_target_draw'], native['next_draw'])
        self.assertEqual(result['probabilities']['hit_bust'], native['hit_bust'])
        self.assertAlmostEqual(sum(result['probabilities']['dealer_terminal_if_stand_now'].values()), 1)
        validate_ace_prepeek_result(result)
        self.assertEqual(ledger.to_list(), before)

    def test_natural_bj_terminal_push_against_bj_and_positive_non_bj(self):
        ledger, snapshot = self.snapshot(('A','T'))
        before = ledger.to_list()
        result = compose_ace_prepeek_result(snapshot, 'owned-natural', conditional_fixture(snapshot))
        p = snapshot.dealer_bj_probability; terminal = result['terminal_evaluation']
        self.assertEqual(snapshot.legal_actions, ())
        self.assertEqual(result['actions'], {})
        self.assertAlmostEqual(terminal['net_distribution']['0'], p)
        self.assertAlmostEqual(terminal['net_distribution']['1.5'], 1-p)
        self.assertAlmostEqual(terminal['ev'], 1.5*(1-p))
        self.assertAlmostEqual(terminal['net_profit_probability'], 1-p)
        self.assertEqual(terminal['loss_probability'], 0)
        self.assertEqual(result['conditional_draw']['status'], 'inapplicable')
        self.assertNotIn('hit_bust', result['probabilities'])
        validate_ace_prepeek_result(result)
        self.assertEqual(ledger.to_list(), before)

    def test_multi_initial_natural_other_hand_is_retained_in_condition(self):
        ledger, snapshot = self.snapshot(participants=['玩家1','玩家2'])
        result = compose_ace_prepeek_result(snapshot, 'owned-multi', conditional_fixture(snapshot))
        self.assertEqual(len(json.loads(snapshot.information_json)['initial_hands']), 2)
        self.assertIn('per-seat-others-no-draw-v1', snapshot.support_scope)
        self.assertEqual(snapshot.dealer_bj_fraction, Fraction(127,411))
        self.assertEqual(len(result['actions']), 3)
        self.assertFalse(ledger.replay().current.table.dealer_hole_checked_negative)

    def test_forged_probability_observation_stage_count_or_unknown_rules_rejected(self):
        _, snapshot = self.snapshot()
        for key, value in [('dealer_bj_probability',.9), ('dealer_bj_fraction','9/10'),
                           ('bj_numerator',127), ('bj_denominator',412), ('peek_negative',True)]:
            forged = deepcopy(snapshot.to_dict()); forged[key] = value
            with self.subTest(key=key), self.assertRaises((ValueError,TypeError)):
                parse_analysis_input(forged)
        for key, value in [('evaluation_stage','after_peek'),('negative_peek',True),
                           ('unrevealed_out',2),('gap',True),('full_initial_deal',False)]:
            info = json.loads(snapshot.information_json); info[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                replace(snapshot, information_json=canonical(info)).validate()
        for field, value in [('start_from_new_shoe',None),('burn_cards_known',None),
                             ('initial_burn_count',None),('version',1),('check_bj_when','before_player_actions_A_T')]:
            rules = json.loads(snapshot.rules_json); rules[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                replace(snapshot, rules_json=canonical(rules)).validate()
        info = json.loads(snapshot.information_json); info['initial_hands'][0]['actions'] = ['补牌']
        with self.assertRaises(ValueError):
            replace(snapshot, information_json=canonical(info)).validate()
        with self.assertRaises(ValueError):
            replace(snapshot, counts=(snapshot.counts[0]-1,)+snapshot.counts[1:]).validate()

    def test_storage_rejects_corrupted_branch_ev_draw_and_investment(self):
        _, snapshot = self.snapshot(('8','8'))
        result = compose_ace_prepeek_result(snapshot, 'owned-corruption', conditional_fixture(snapshot))
        with tempfile.TemporaryDirectory() as tmp:
            store = AnalysisSnapshots(tmp)
            cases = []
            for path, value in [
                (('dealer_bj_probability',), .8),
                (('actions','double','ev'), .2),
                (('actions','split','expected_additional_investment'), 1),
                (('actions','split','joint_distribution'), {'-1,0':1}),
                (('branches','dealer_bj','net_distribution'), {'-2':1}),
                (('conditional_draw','hit_bust'), .99),
                (('conditional_non_bj','actions','stand','ev'), .99)]:
                changed = deepcopy(result); parent = changed
                for part in path[:-1]: parent = parent[part]
                parent[path[-1]] = value; cases.append(changed)
            for changed in cases:
                with self.assertRaises(SnapshotFormatError): store.save(changed)
            self.assertEqual(list(Path(tmp).glob('*.json')), [])

    def test_saved_original_prefix_rebuild_survives_actual_negative_observation(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctrl = SessionController(Path(tmp)/'owned.db')
            try:
                ctrl.new_shoe(observed_rules()); ctrl.start_round(['玩家1'], simple_hole=True)
                for seat, rank in [('玩家1','9'),('庄家','A'),('玩家1','8')]: ctrl.deal_shown(seat, rank)
                snapshot = ctrl.current_decision_input('玩家1'); before = ctrl.ledger.to_list()
                result = compose_ace_prepeek_result(snapshot, 'owned-history', conditional_fixture(snapshot))
                saved = ctrl.analysis_store.save(result)
                file = ctrl.analysis_store.directory / (saved['snapshot_id']+'.json'); original = file.read_bytes()
                self.assertEqual(ctrl.ledger.to_list(), before)
                ctrl.peek_negative()
                restored = ctrl.recompute_input(ctrl.analysis_store.load(saved['snapshot_id']))
                self.assertEqual(restored.schema, ACE_PREPEEK_INPUT_SCHEMA)
                self.assertEqual(restored.input_digest, snapshot.input_digest)
                self.assertFalse(restored.peek_negative)
                self.assertEqual(file.read_bytes(), original)
                self.assertNotEqual(ctrl.current_decision_input('玩家1').schema, ACE_PREPEEK_INPUT_SCHEMA)
            finally: ctrl.close()

    def test_original_single_and_split_parser_identities_remain_unchanged(self):
        ledger, _ = self.snapshot(); ledger.peek_negative()
        split = build_input(ledger,'玩家1')
        self.assertEqual(parse_analysis_input(split.to_dict()).to_dict(), split.to_dict())
        legacy = EventLedger('owned-old-schema'); legacy.start_session(); legacy.create_shoe(research_rules())
        legacy.start_round(['玩家1']); legacy.deal('庄家','6'); legacy.deal('庄家',hidden=True)
        legacy.deal('玩家1','T'); legacy.deal('玩家1','6')
        single = build_input(legacy,'玩家1')
        self.assertEqual(parse_analysis_input(single.to_dict()).to_dict(), single.to_dict())
        with tempfile.TemporaryDirectory() as tmp:
            store = AnalysisSnapshots(tmp)
            for schema, snapshot in [('hakimi-analysis-result-v1',single), ('hakimi-analysis-result-v2',split)]:
                minimal = dict(schema=schema, input=snapshot.to_dict(), input_digest=snapshot.input_digest)
                saved = store.save(minimal)
                self.assertEqual(store.load(saved['snapshot_id'])['result'], json.loads(canonical(minimal)))

    def test_zero_and_certain_bj_contract_boundaries_never_invent_conditional_branch(self):
        _, original = self.snapshot()
        # Pure numerical contract fixtures declare self-consistent earlier
        # removals. They are not a live prefix or recording-factory acceptance.
        for certain in (False, True):
            info = json.loads(original.information_json)
            ranks = ('A','2','3','4','5','6','7','8','9') if certain else ('10','J','Q','K')
            for rank in ranks: info['exact_out'][rank] = 32
            counts = tuple(32-info['exact_out'][r] for r in ('A','2','3','4','5','6','7','8','9')) + (
                128-sum(info['exact_out'][r] for r in ('10','J','Q','K'))-info['t_bucket_out'],)
            snapshot = replace(original, information_json=canonical(info), counts=counts,
                physical_remaining=sum(counts)-1, bj_numerator=counts[9], bj_denominator=sum(counts),
                legal_actions=() if certain else original.legal_actions)
            snapshot.validate()
            self.assertEqual(snapshot.dealer_bj_probability, int(certain))
            if certain:
                with patch('blackjack_lab.analysis.ace_prepeek_service.solve_presplit_native') as native:
                    result = calculate(snapshot, 'owned-certain')
                    native.assert_not_called()
                self.assertEqual(result['actions'], {})
                self.assertIsNone(result['conditional_non_bj'])
                self.assertEqual(result['terminal_evaluation']['net_distribution'], {'-1':1.})
                self.assertEqual(result['terminal_evaluation']['ev'], -1)
                forged = deepcopy(result); forged['dealer_bj_probability'] = True
                with self.assertRaises(ValueError): validate_ace_prepeek_result(forged)
            else:
                numbers = conditional_fixture(snapshot)
                result = compose_ace_prepeek_result(snapshot, 'owned-zero', numbers)
                for action,item in result['actions'].items():
                    self.assertEqual(item['ev'], numbers['actions'][action]['ev'])
            validate_ace_prepeek_result(result)

    def test_new_schema_requires_full_result_and_zero_bj_in_conditional_kernel(self):
        _, snapshot = self.snapshot()
        with tempfile.TemporaryDirectory() as tmp:
            store = AnalysisSnapshots(tmp)
            minimal = dict(schema=ACE_PREPEEK_RESULT_SCHEMA, input=snapshot.to_dict(),
                           input_digest=snapshot.input_digest)
            with self.assertRaises(SnapshotFormatError): store.save(minimal)
            self.assertEqual(list(Path(tmp).glob('*.json')), [])
        native = conditional_fixture(snapshot)
        native['dealer_distribution']['blackjack'] = 1e-12
        native['dealer_distribution']['bust'] -= 1e-12
        with self.assertRaises(ArithmeticError): compose_ace_prepeek_result(snapshot, 'owned-invalid-condition', native)

    def test_real_bounded_worker_returns_mixed_numbers_and_never_changes_facts(self):
        ledger, snapshot = self.snapshot(); before = ledger.to_list()
        service = AnalysisService()
        try:
            service.start(snapshot)
            end = perf_counter()+8
            result = None
            while perf_counter()<end and result is None:
                result = service.poll(); sleep(.01)
            self.assertIsNotNone(result, 'before-peek worker did not return')
            self.assertEqual(result['status'], 'available', result.get('reason'))
            self.assertEqual(result['schema'], ACE_PREPEEK_RESULT_SCHEMA)
            self.assertEqual(set(result['actions']), {'stand','hit','double'})
            self.assertFalse(result['input']['peek_negative'])
            self.assertEqual(result['input_digest'], snapshot.input_digest)
            validate_ace_prepeek_result(result)
            for action,item in result['actions'].items():
                native = result['conditional_non_bj']['actions'][action]
                self.assertAlmostEqual(item['ev'], (1-snapshot.dealer_bj_probability)*native['ev']
                                       -snapshot.dealer_bj_probability)
            self.assertEqual(ledger.to_list(), before)
        finally:
            service.close()
            end = perf_counter()+3
            while not service.stopped and perf_counter()<end: sleep(.01)
            self.assertTrue(service.stopped, 'before-peek worker resources not drained')
