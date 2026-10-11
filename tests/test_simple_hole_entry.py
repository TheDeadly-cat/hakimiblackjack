import copy
import json
import unittest
from dataclasses import replace
from unittest.mock import patch

from tests import test_analysis_ui as fixture
from blackjack_lab.analysis.contracts import InputUnavailable
from blackjack_lab.analysis.contracts import research_rules
from blackjack_lab.core.rules import CONFIRM_UNKNOWN
from blackjack_lab.core.table import TableError
from blackjack_lab.analysis.service import calculate
from blackjack_lab.analysis.probability import FiniteModel
from blackjack_lab.ui.controller import SessionController
from blackjack_lab.ui.app import BlackjackLabApp
from blackjack_lab.ui.deal_entry import MODE_UNALIGNED, MODE_PEEK_WAIT, SIMPLE_HOLE_CONTRACT
from blackjack_lab.ui.recent_entry import undo_label


class TestSimpleHoleEntry(unittest.TestCase):
    setUp = fixture.TestAnalysisUI.setUp
    close = fixture.TestAnalysisUI.close

    def prepare(self, simple=True, players=1):
        self.app.var_simple_hole.set(simple)
        self.app.act_research_template()
        self.app.act_new_shoe()
        for index, variable in enumerate(self.app.var_participants.values(), 1):
            variable.set(index <= players)
        self.app.act_new_round()
        self.assertEqual(self.errors, [])

    def initial(self, up='6', players=1, simple=True):
        self.prepare(simple, players)
        for rank in ('T',) * players + (up,) + ('6',) * players:
            self.app._key_rank(rank)
        if not simple:
            self.app._key_hole()
        self.assertEqual(self.errors, [])

    def hidden(self):
        return [e for e in self.app.ctrl.ledger.events if e.payload.get('face_state') == 'hidden'
                and not self.app.ctrl.ledger.is_voided(e.event_id)]

    def recover(self):
        recovered = SessionController.recover(self.db, self.app.ctrl.session_id)
        self.addCleanup(recovered.close)
        return recovered

    def test_normal_initial_has_exactly_one_auditable_unknown_hole_without_dot(self):
        self.initial()
        ctrl = self.app.ctrl
        self.assertEqual(len(self.hidden()), 1)
        self.assertEqual(ctrl.state().current.shoe.physical_remaining(), 412)
        self.assertEqual(ctrl.state().current.shoe.unrevealed_out, 1)
        receipt = json.loads(self.hidden()[0].evidence)
        self.assertEqual(receipt['recording_contract'], SIMPLE_HOLE_CONTRACT)
        self.assertEqual(receipt['trigger_event_id'], ctrl.ledger.events[-2].event_id)
        self.assertIsNone(self.hidden()[0].payload['rank'])
        self.assertEqual(self.app.var_target.get(), '玩家1')
        ctrl.analysis_input('玩家1').validate()

    def test_visible_card_buttons_follow_the_same_confirmed_initial_command(self):
        self.prepare()
        for rank in ('T', '6', '6'):
            self.app.act_card(rank)
        self.assertEqual(len(self.hidden()), 1)
        self.assertEqual(self.app.ctrl.state().current.shoe.physical_remaining(), 412)
        self.assertEqual(self.app.var_target.get(), '玩家1')
        self.assertEqual(self.errors, [])

    def test_dealer_direct_rank_reveals_then_new_rank_deals_and_settles(self):
        self.initial()
        hole = self.hidden()[0].event_id
        self.app._key_stand()
        self.assertIn('庄家开牌：请输入底牌点数', self.app.var_entry_prompt.get())
        self.assertEqual(self.app.var_mode.get(), '新发牌')
        self.app._key_rank('8')
        ctrl = self.app.ctrl
        self.assertEqual(ctrl.ledger.events[-1].etype, 'CARD_REVEALED')
        self.assertEqual(ctrl.ledger.events[-1].payload['target_event_id'], hole)
        self.assertEqual(ctrl.state().current.shoe.physical_remaining(), 412)
        self.assertEqual(ctrl.state().current.shoe.unrevealed_out, 0)
        self.app._key_rank('3')
        self.assertEqual(ctrl.ledger.events[-1].etype, 'CARD_DEALT')
        self.assertEqual(ctrl.state().current.shoe.physical_remaining(), 411)
        self.assertIn('庄家 6 8 3 · 17点', self.app.compact_panel.identity.get())
        self.app.act_end_round()
        self.assertIn('本轮已结算', self.app.var_entry_prompt.get())
        self.assertEqual(self.errors, [])

    def test_refresh_repeated_button_and_recovery_do_not_create_extra_hole(self):
        self.initial()
        original = self.app.ctrl.ledger.to_list()
        for _ in range(5):
            self.app.refresh_all()
        self.app._key_hole()  # A repeated old button cannot fill the completed slot.
        self.assertEqual(self.app.ctrl.ledger.to_list(), original)
        recovered = self.recover()
        self.assertEqual(recovered.ledger.to_list(), original)
        self.assertTrue(recovered.entry_plan.simple_hole)
        self.assertEqual(len(self.hidden()), 1)

    def test_undo_last_initial_input_removes_visible_and_auto_hole_and_allows_rerecord(self):
        self.prepare()
        for rank in ('T', '6'):
            self.app._key_rank(rank)
        ctrl = self.app.ctrl
        prior_plan = ctrl.entry_plan.to_dict()
        prior_ledger = ctrl.ledger.to_list()
        self.app._key_rank('6')
        trigger, hole = ctrl.ledger.events[-2:]
        self.app.act_undo()
        before = ctrl.ledger.to_list()
        self.assertEqual(before[:-2], prior_ledger + [trigger.to_dict(), hole.to_dict()])
        self.assertEqual([event['payload']['target_event_id'] for event in before[-2:]],
                         [hole.event_id, trigger.event_id])
        restored_plan = ctrl.entry_plan.to_dict()
        restored_plan['ledger_seq'] = prior_plan['ledger_seq']
        restored_plan['ledger_digest'] = prior_plan['ledger_digest']
        self.assertEqual(restored_plan, prior_plan)
        self.assertEqual(ctrl.state().current.table.players['玩家1'].hands[0].ranks, ['T'])
        self.assertEqual(ctrl.state().current.shoe.physical_remaining(), 414)
        for _ in range(5):
            self.app.refresh_all()
        recovered = self.recover()
        self.assertEqual(recovered.ledger.to_list(), before)
        self.assertTrue(recovered.simple_hole_active())
        self.assertEqual(recovered.state().current.shoe.unrevealed_out, 0)
        self.assertEqual(recovered.entry_plan.to_dict(), ctrl.entry_plan.to_dict())
        self.app._key_rank('7')
        self.assertEqual(ctrl.state().current.table.players['玩家1'].hands[0].ranks, ['T', '7'])
        self.assertEqual(len(self.hidden()), 1)
        self.assertNotEqual(self.hidden()[0].event_id, hole.event_id)
        self.assertEqual(ctrl.ledger.to_list()[:-2], before)
        self.assertEqual(ctrl.state().current.shoe.physical_remaining(), 412)
        self.assertEqual(self.errors, [])

    def test_legacy_auto_hole_receipt_still_rewinds_verified_input_after_restart(self):
        self.prepare()
        self.app._key_rank('T'); self.app._key_rank('6')
        ctrl = self.app.ctrl
        original_dump = json.dumps
        def legacy_dump(data, *args, **kwargs):
            if isinstance(data, dict) and 'automatic_hole_undo_v1' in data:
                data = {key: value for key, value in data.items() if key != 'automatic_hole_undo_v1'}
            return original_dump(data, *args, **kwargs)
        with patch('blackjack_lab.ui.controller.json.dumps', side_effect=legacy_dump):
            self.app._key_rank('6')
        before = ctrl.ledger.to_list()
        trigger, hole = ctrl.ledger.events[-2:]
        self.assertNotIn('automatic_hole_undo_v1', json.loads(hole.evidence))
        recovered = self.recover()
        self.assertEqual(undo_label(recovered), '撤销本次录牌及自动暗牌')
        recovered.undo_last()
        self.assertEqual(recovered.ledger.to_list()[:-2], before)
        self.assertEqual([event.payload['target_event_id'] for event in recovered.ledger.events[-2:]],
                         [hole.event_id, trigger.event_id])
        self.assertEqual(recovered.entry_plan.next_card_text(), '下一张给：玩家1，第2张')
        self.assertFalse(recovered.entry_plan.paused)
        self.assertEqual(recovered.state().current.table.players['玩家1'].hands[0].ranks, ['T'])
        self.assertEqual(recovered.state().current.shoe.unrevealed_out, 0)
        recovered.deal_shown('玩家1', '7', initial_slot_id=recovered.entry_plan.slot().slot_id)
        self.assertEqual(recovered.state().current.table.players['玩家1'].hands[0].ranks, ['T', '7'])
        self.assertEqual(recovered.state().current.shoe.unrevealed_out, 1)

    def test_seven_seat_group_undo_changes_only_last_players_second_card(self):
        self.prepare(players=7)
        for rank in ['T'] * 7 + ['6'] + ['6'] * 6:
            self.app._key_rank(rank)
        ctrl = self.app.ctrl
        prior_plan = ctrl.entry_plan.to_dict()
        last_seat = ctrl.entry_plan.slot().seat
        before_cards = {name: seat.hands[0].ranks for name, seat in ctrl.state().current.table.players.items()}
        before_ledger = ctrl.ledger.to_list()
        remaining = ctrl.state().current.shoe.physical_remaining()
        self.app._key_rank('7')
        trigger, hole = ctrl.ledger.events[-2:]
        self.assertEqual(undo_label(ctrl), '撤销本次录牌及自动暗牌')
        self.app.act_undo()
        self.assertEqual(ctrl.ledger.to_list()[:-2], before_ledger + [trigger.to_dict(), hole.to_dict()])
        self.assertEqual({name: seat.hands[0].ranks for name, seat in ctrl.state().current.table.players.items()}, before_cards)
        restored = ctrl.entry_plan.to_dict()
        restored['ledger_seq'], restored['ledger_digest'] = prior_plan['ledger_seq'], prior_plan['ledger_digest']
        self.assertEqual(restored, prior_plan)
        self.assertEqual(ctrl.state().current.shoe.physical_remaining(), remaining)
        self.app._key_rank('8')
        current_cards = {name: seat.hands[0].ranks for name, seat in ctrl.state().current.table.players.items()}
        self.assertEqual(current_cards, dict(before_cards, **{last_seat: ['T', '8']}))
        self.assertEqual(ctrl.state().current.shoe.unrevealed_out, 1)
        self.assertEqual(self.errors, [])

    def test_manual_hole_is_never_grouped_with_previous_visible_input(self):
        self.initial(simple=False)
        ctrl = self.app.ctrl
        before = ctrl.ledger.to_list()
        hole = ctrl.ledger.events[-1]
        self.assertIsNone(ctrl._automatic_hole_undo_group())
        self.assertEqual(undo_label(ctrl), '撤销暗牌登记')
        self.app.act_undo()
        self.assertEqual(ctrl.ledger.to_list()[:-1], before)
        self.assertEqual(ctrl.ledger.events[-1].payload['target_event_id'], hole.event_id)
        self.assertEqual(ctrl.state().current.table.players['玩家1'].hands[0].ranks, ['T', '6'])
        self.assertEqual(ctrl.state().current.shoe.unrevealed_out, 0)

    def test_unbound_auto_hole_receipt_only_undoes_hidden_event(self):
        self.prepare()
        self.app._key_rank('T'); self.app._key_rank('6')
        original_dump = json.dumps
        def unbound_dump(data, *args, **kwargs):
            if isinstance(data, dict) and 'automatic_hole_undo_v1' in data:
                data = copy.deepcopy(data)
                data['automatic_hole_undo_v1']['previous_plan']['ledger_digest'] = 'wrong-prefix'
            return original_dump(data, *args, **kwargs)
        with patch('blackjack_lab.ui.controller.json.dumps', side_effect=unbound_dump):
            self.app._key_rank('6')
        ctrl = self.app.ctrl
        before = ctrl.ledger.to_list()
        hole = ctrl.ledger.events[-1]
        self.assertIsNone(ctrl._automatic_hole_undo_group())
        self.assertEqual(undo_label(ctrl), '撤销自动暗牌（关联待核对）')
        self.app.act_undo()
        self.assertEqual(ctrl.ledger.to_list()[:-1], before)
        self.assertEqual(ctrl.ledger.events[-1].payload['target_event_id'], hole.event_id)
        self.assertEqual(ctrl.state().current.table.players['玩家1'].hands[0].ranks, ['T', '6'])
        self.assertEqual(ctrl.state().current.shoe.unrevealed_out, 0)

    def test_group_undo_failure_saves_neither_undo_and_preserves_plan(self):
        self.initial()
        ctrl = self.app.ctrl
        before, plan = ctrl.ledger.to_list(), ctrl.entry_plan.to_dict()
        with patch.object(ctrl.store, '_insert', side_effect=OSError('injected undo write failure')):
            self.app.act_undo()
        self.assertEqual(ctrl.ledger.to_list(), before)
        self.assertEqual(ctrl.entry_plan.to_dict(), plan)
        self.assertEqual(ctrl.store.load_ledger(ctrl.session_id).to_list(), before)
        self.assertEqual(len(self.errors), 1)
        self.errors.clear()
        self.app.act_undo()
        self.assertEqual([event.etype for event in ctrl.ledger.events[-2:]], ['UNDO', 'UNDO'])
        self.assertEqual(ctrl.state().current.table.players['玩家1'].hands[0].ranks, ['T'])
        self.assertEqual(self.errors, [])

    def test_group_undo_retains_explicit_pause_until_user_resumes(self):
        self.initial()
        self.app._key_pause()
        self.app.act_undo()
        ctrl = self.app.ctrl
        self.assertTrue(ctrl.entry_plan.input_paused)
        self.assertEqual(ctrl.entry_plan.next_card_text(), '下一张给：玩家1，第2张')
        before = ctrl.ledger.to_list()
        self.app._key_rank('7')
        self.assertEqual(ctrl.ledger.to_list(), before)
        self.errors.clear()
        self.app._key_pause()
        self.app._key_rank('7')
        self.assertEqual(ctrl.state().current.table.players['玩家1'].hands[0].ranks, ['T', '7'])
        self.assertEqual(ctrl.state().current.shoe.unrevealed_out, 1)
        self.assertEqual(self.errors, [])

    def test_undo_reveal_restores_same_unknown_physical_card(self):
        self.initial()
        self.app._key_stand()
        hole = self.hidden()[0].event_id
        self.app._key_rank('8')
        self.app.act_undo()
        ctrl = self.recover()
        self.assertEqual(ctrl.state().current.shoe.physical_remaining(), 412)
        self.assertEqual(ctrl.state().current.shoe.unrevealed_out, 1)
        self.assertIn(hole, ctrl.state().current.unresolved)
        self.assertEqual(ctrl.simple_dealer_route('庄家'), hole)
        self.app._key_rank('8')
        self.assertEqual(self.app.ctrl.ledger.events[-1].payload['target_event_id'], hole)
        self.assertEqual(self.errors, [])

    def test_repeated_dealer_ranks_are_one_reveal_then_one_new_card(self):
        self.initial()
        self.app._key_stand()
        self.app._key_rank('8')
        self.app._key_rank('8')
        self.assertEqual([e.etype for e in self.app.ctrl.ledger.events[-2:]], ['CARD_REVEALED', 'CARD_DEALT'])
        self.assertEqual(self.app.ctrl.state().current.shoe.physical_remaining(), 411)
        self.assertEqual(self.errors, [])

    def test_restart_after_reveal_next_key_is_a_new_dealer_card(self):
        # This scenario expects automatic settlement/continuation after restart.
        # The shared manual fixture disables it, and mode settings now persist it.
        self.app.var_auto_next.set(True)
        self.initial()
        self.app._key_stand()
        self.app._key_rank('8')
        before = self.app.ctrl.ledger.to_list()
        self.close()
        self.app = BlackjackLabApp(self.db, auto_analysis=False)
        self.app.update()
        self.assertEqual(self.app.ctrl.ledger.to_list(), before)
        self.assertTrue(self.app.var_simple_hole.get())
        self.app._key_rank('3')
        self.assertEqual([e.etype for e in self.app.ctrl.ledger.events[-3:]],
                         ['CARD_DEALT', 'ROUND_ENDED', 'ROUND_STARTED'])
        self.assertEqual(self.app.ctrl.ledger.events[-3].payload['rank'], '3')
        self.assertEqual(self.app.ctrl.state().current.table.round_no, 2)
        self.assertEqual(sum(e.etype == 'CARD_REVEALED' for e in self.app.ctrl.ledger.events), 1)
        self.assertEqual(self.app.ctrl.state().current.shoe.physical_remaining(), 411)
        self.assertEqual(self.errors, [])

    def test_physical_key_repeat_does_not_repeat_the_initial_batch(self):
        self.prepare()
        self.app._key_rank('T')
        self.app._key_rank('6')
        self.app.focus_force()
        self.app.update()
        for _ in range(2):
            self.app.event_generate('<KeyPress-6>')
            self.app.update()
        self.app.event_generate('<KeyRelease-6>')
        self.app.update()
        self.assertEqual(len(self.app.ctrl.ledger.events), 7)
        self.assertEqual(len(self.hidden()), 1)
        self.assertEqual(self.errors, [])

    def test_restart_preserves_explicitly_disabled_automatic_next(self):
        self.app.var_auto_next.set(False)
        self.initial(); self.app._key_stand(); self.app._key_rank('8')
        self.close()
        self.app = BlackjackLabApp(self.db, auto_analysis=False)
        self.app.update()
        self.assertFalse(self.app.var_auto_next.get())
        self.app._key_rank('3')
        self.assertEqual(self.app.ctrl.ledger.events[-1].etype, 'CARD_DEALT')
        self.assertEqual(self.app.ctrl.state().current.table.round_no, 1)
        self.assertEqual(self.errors, [])

    def test_ace_and_ten_do_not_fabricate_peek_or_infer_it_from_player_action(self):
        for up in ('A', 'T'):
            with self.subTest(up=up):
                if up == 'T':
                    self.app.ctrl.end_round_unsettled('case boundary', 'unknown')
                    self.app.ctrl.end_shoe()
                self.initial(up)
                ctrl = self.app.ctrl
                before = ctrl.ledger.to_list()
                self.assertEqual(ctrl.entry_plan.mode, MODE_PEEK_WAIT)
                with self.assertRaises(InputUnavailable) as gate:
                    ctrl.analysis_input('玩家1')
                self.assertEqual(gate.exception.code, 'PEEK_REQUIRED')
                self.app._key_stand()
                self.assertEqual(ctrl.ledger.to_list(), before)
                self.errors.clear()
                self.assertFalse(any(e.etype == 'PEEK_NEGATIVE' and e.round_id == ctrl.state().current.round_id for e in ctrl.ledger.events))
                self.app.act_peek_negative()
                ctrl.analysis_input('玩家1').validate()
                self.assertEqual(ctrl.ledger.events[-1].etype, 'PEEK_NEGATIVE')

    def test_actual_blackjack_can_be_revealed_in_peek_wait_and_undo_restores_gate(self):
        self.initial('A')
        self.app._key_rank('T')
        self.assertEqual(self.app.ctrl.ledger.events[-1].etype, 'CARD_REVEALED')
        self.assertFalse(any(e.etype == 'PEEK_NEGATIVE' for e in self.app.ctrl.ledger.events))
        self.app.act_undo()
        self.assertEqual(self.app.ctrl.entry_plan.mode, MODE_PEEK_WAIT)
        self.app._key_rank('T')
        self.app.act_end_round()
        self.assertEqual(self.app.ctrl.state().current.settlements[-1]['net_units'], -1)
        self.assertEqual(self.errors, [])

    def test_unshown_hole_stays_removed_and_history_does_not_learn_later_rank(self):
        self.initial()
        ctrl = self.app.ctrl
        snapshot = ctrl.analysis_input('玩家1')
        before = snapshot.to_dict()
        self.app._key_stand()
        self.app._key_rank('8')
        historical = ctrl.analysis_input('玩家1', through_seq=snapshot.through_seq)
        self.assertEqual(historical.to_dict(), before)
        self.app.act_undo()
        ctrl.end_round_unsettled('底牌实际没有展示', 'complete')
        self.assertEqual(ctrl.state().current.shoe.unrevealed_out, 1)
        self.assertEqual(ctrl.state().current.shoe.physical_remaining(), 412)
        self.assertEqual(self.recover().state().current.shoe.unrevealed_out, 1)

    def test_atomic_pair_rolls_back_on_second_insert_failure_and_retry_is_once(self):
        self.prepare()
        self.app._key_rank('T')
        self.app._key_rank('6')
        ctrl = self.app.ctrl
        before, plan = ctrl.ledger.to_list(), ctrl.entry_plan.to_dict()
        insert = ctrl.store._insert
        def fail_hole(event):
            if event.payload.get('face_state') == 'hidden':
                raise OSError('injected second insert failure')
            return insert(event)
        with patch.object(ctrl.store, '_insert', side_effect=fail_hole):
            self.app._key_rank('6')
        self.assertEqual(ctrl.ledger.to_list(), before)
        self.assertEqual(ctrl.store.load_ledger(ctrl.session_id).to_list(), before)
        self.assertEqual(ctrl.entry_plan.to_dict(), plan)
        self.app._key_rank('6')
        self.assertEqual(len(ctrl.ledger.events), len(before) + 2)
        self.assertEqual(len(self.hidden()), 1)

    def test_sidecar_failure_keeps_both_committed_events_and_recovers_paused(self):
        self.prepare()
        self.app._key_rank('T')
        self.app._key_rank('6')
        ctrl = self.app.ctrl
        with patch.object(ctrl, '_write_entry_plan', side_effect=OSError('injected sidecar failure')):
            self.app._key_rank('6')
        self.assertEqual(len(ctrl.ledger.events), 7)
        self.assertEqual(ctrl.entry_plan.mode, MODE_UNALIGNED)
        recovered = self.recover()
        self.assertEqual(recovered.entry_plan.mode, MODE_UNALIGNED)
        self.assertEqual(len(recovered.ledger.events), 7)
        self.assertEqual(recovered.state().current.shoe.physical_remaining(), 412)

    def test_gap_and_missing_plan_never_auto_fill(self):
        self.prepare()
        self.app._key_rank('T')
        self.app._key_rank('6')
        self.app.ctrl.mark_gap('实际漏录')
        self.app._key_rank('6')
        self.assertEqual(self.hidden(), [])
        self.assertFalse(self.app.ctrl.simple_hole_active())
        # Recovery with no matching plan cannot invent the missing hole.
        self.app.ctrl._entry_plan_path().rename(self.app.ctrl._entry_plan_path().with_suffix('.kept.json'))
        recovered = self.recover()
        self.assertEqual(recovered.entry_plan.mode, MODE_UNALIGNED)
        self.assertFalse(recovered.entry_plan.simple_hole)

    def test_unknown_non_us_or_midshoe_rules_cannot_enable_simple_mode(self):
        for change in ({'start_from_new_shoe': False}, {'american_hole_card': False},
                       {'confirm_status': CONFIRM_UNKNOWN}, {'check_bj_when': None}):
            with self.subTest(change=change):
                self.app.ctrl.new_shoe(replace(research_rules(8), **change))
                before = self.app.ctrl.ledger.to_list()
                with self.assertRaises(TableError):
                    self.app.ctrl.start_round(['玩家1'], simple_hole=True)
                self.assertEqual(self.app.ctrl.ledger.to_list(), before)
                self.app.ctrl.end_shoe()

    def test_manual_navigation_does_not_confirm_skipped_initial_deal(self):
        self.prepare()
        self.app._key_rank('T')
        self.app._key_navigate(1)
        self.app._key_rank('6')
        self.app._key_navigate(-1)
        self.app._key_rank('6')
        self.assertEqual(self.hidden(), [])
        self.assertFalse(self.app.ctrl.simple_hole_active())

    def test_partial_recovery_waits_for_an_explicit_last_visible_card(self):
        self.prepare()
        self.app._key_rank('T')
        self.app._key_rank('6')
        ctrl = self.recover()
        self.assertEqual(len(ctrl.ledger.events), 5)
        self.assertEqual(ctrl.state().current.shoe.unrevealed_out, 0)
        slot = ctrl.entry_plan.slot()
        ctrl.deal_shown(slot.seat, '6', initial_slot_id=slot.slot_id)
        self.assertEqual(len(ctrl.ledger.events), 7)
        self.assertEqual(ctrl.state().current.shoe.unrevealed_out, 1)

    def test_notification_failure_does_not_split_commit_or_repeat_hole(self):
        self.prepare()
        self.app._key_rank('T')
        self.app._key_rank('6')
        def failing_view():
            raise RuntimeError('injected view failure')
        self.app.ctrl.add_context_listener(failing_view)
        self.app._key_rank('6')
        self.app.ctrl.remove_context_listener(failing_view)
        self.assertIn('记录已保存', self.app.ctrl.context_warning)
        self.assertTrue(self.app.ctrl.entry_plan.initial_complete())
        self.app.refresh_all()
        self.assertEqual(len(self.hidden()), 1)
        self.assertEqual(len(self.recover().ledger.events), 7)

    def test_early_dealer_selection_and_multiple_unknowns_require_manual_choice(self):
        self.initial()
        self.app._key_jump(0)
        before = self.app.ctrl.ledger.to_list()
        self.app._key_rank('8')
        self.assertEqual(self.app.ctrl.ledger.to_list(), before)
        self.assertIn('提前开牌', self.errors[-1])
        self.errors.clear()
        self.app._key_jump(1)
        self.app._key_stand()
        self.app.ctrl.deal_hidden('庄家')
        before = self.app.ctrl.ledger.to_list()
        self.app._key_rank('8')
        self.assertEqual(self.app.ctrl.ledger.to_list(), before)
        self.assertIn('歧义', self.errors[-1])

    def test_multi_player_flow_inserts_one_hole_only_after_all_visible_cards(self):
        self.prepare(players=3)
        for rank in ('T', 'T', 'T', '6', '6', '6'):
            self.app._key_rank(rank)
            self.assertEqual(self.hidden(), [])
        self.app._key_rank('6')
        self.assertEqual(len(self.hidden()), 1)
        self.assertEqual(self.app.ctrl.state().current.shoe.physical_remaining(), 408)
        for _ in range(3):
            self.app._key_stand()
        self.app._key_rank('8')
        self.assertEqual(self.app.ctrl.ledger.events[-1].etype, 'CARD_REVEALED')
        self.assertEqual(self.errors, [])

    def test_v2_plan_migrates_with_simple_mode_off_and_unattested_v3_is_rejected(self):
        self.initial(simple=False)
        path = self.app.ctrl._entry_plan_path()
        data = self.app.ctrl.entry_plan.to_dict()
        data.pop('simple_hole')
        data.update(schema='hakimi-round-entry-plan-v2', version=2)
        path.write_text(json.dumps(data), encoding='utf-8')
        recovered = self.recover()
        self.assertFalse(recovered.entry_plan.simple_hole)
        path.write_text(json.dumps({**data, 'version': 2.0}), encoding='utf-8')
        self.assertEqual(self.recover().entry_plan.mode, MODE_UNALIGNED)
        upgraded = recovered.entry_plan.to_dict()
        upgraded['simple_hole'] = True
        path.write_text(json.dumps(upgraded), encoding='utf-8')
        self.assertEqual(self.recover().entry_plan.mode, MODE_UNALIGNED)

    def test_manual_mode_can_confirm_simple_mode_and_only_hide_buttons_in_normal_flow(self):
        self.assertFalse(self.app.var_simple_hole.get())
        self.app.var_simple_hole.set(True)
        with patch('blackjack_lab.ui.app.messagebox.askyesno', return_value=False):
            self.app.change_simple_hole()
        self.assertFalse(self.app.var_simple_hole.get())
        self.initial()
        view = self.app.compact_panel
        view.toggle_recording()
        self.app.update()
        self.assertFalse(view.hole_button.winfo_ismapped())
        self.assertFalse(any(button.winfo_ismapped() for button in view.manual_modes))
        self.assertTrue(view.simple_toggle.winfo_ismapped())
        self.app.act_undo()
        self.app.update()
        self.assertFalse(view.hole_button.winfo_ismapped())
        self.assertEqual(self.app.ctrl.entry_plan.next_card_text(), '下一张给：玩家1，第2张')
        self.assertTrue(self.app.ctrl.simple_hole_active())

    def test_manual_and_simple_probabilities_and_state_are_identical(self):
        self.initial()
        first = self.app.ctrl.analysis_input('玩家1')
        simple = calculate(first)
        self.app.ctrl.end_round_unsettled('comparison case', 'complete')
        self.app.ctrl.end_shoe()
        self.initial(simple=False)
        second = self.app.ctrl.analysis_input('玩家1')
        manual = calculate(second)
        self.assertEqual(first.counts, second.counts)
        self.assertEqual(first.physical_remaining, second.physical_remaining)
        self.assertEqual(simple['status'], 'available')
        self.assertEqual(simple['actions'], manual['actions'])
        self.assertEqual(simple['probabilities'], manual['probabilities'])
        counts = (2,) + (0,) * 8 + (2,)
        self.assertAlmostEqual(FiniteModel(10, False).target_draw(counts)[0], .5)
        self.assertAlmostEqual(FiniteModel(10, True).target_draw(counts)[0], 2/3)

    def test_manual_and_simple_match_under_actual_negative_peek_conditions(self):
        for up in ('A', 'T'):
            results = []
            for simple in (True, False):
                self.initial(up, simple=simple)
                self.app.act_peek_negative()
                results.append(calculate(self.app.ctrl.analysis_input('玩家1')))
                self.app.ctrl.end_round_unsettled('peek comparison', 'complete')
                self.app.ctrl.end_shoe()
            self.assertEqual(results[0]['status'], 'available')
            self.assertEqual(results[0]['actions'], results[1]['actions'])
            self.assertEqual(results[0]['probabilities'], results[1]['probabilities'])


if __name__ == '__main__':
    unittest.main()
