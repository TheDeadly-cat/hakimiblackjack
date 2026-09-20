"""K-F5: complete Windows/Tk target transitions, based on the original review cases.
Only temporary databases; navigation and analysis identity remain independent.
"""
import json
import os
import unittest
from tests import test_ui_workflow as ui_fixture
from blackjack_lab.core.table import ACTION_SPLIT, ACTION_DOUBLE


@unittest.skipUnless(os.name == 'nt', 'Requires complete Windows application environment')
class TestPR14Followup(unittest.TestCase):
    setUp = ui_fixture.TestUIWorkflow.setUp
    close_app = ui_fixture.TestUIWorkflow.close_app

    def press(self, key):
        self.app.focus_force()
        self.app.update()
        self.app.event_generate('<KeyPress-' + key + '>')
        self.app.update()
        self.app.event_generate('<KeyRelease-' + key + '>')
        self.app.update()

    def test_real_eight_key_sequence_asserts_each_durable_step(self):
        self.app.act_research_template()
        self.app.act_new_shoe()
        for name, variable in self.app.var_participants.items():
            variable.set(name in ('玩家1', '玩家2', '玩家3'))
        self.app.act_new_round()
        trace = []
        root = self.app
        tag = 'PR14ReviewTrace' + str(id(self))
        original_tags = root.bindtags()

        def on_key(event):
            plan = root.ctrl.entry_plan
            trace.append({
                'type': str(event.type), 'keysym': event.keysym,
                'keycode': event.keycode, 'char': event.char, 'state': event.state,
                'focus': str(root.focus_get()), 'widget': str(event.widget),
                'bindtags': list(event.widget.bindtags()),
                'guard_pressed': sorted(root._key_binder.guard.pressed),
                'seq': root.ctrl.ledger.events[-1].seq,
                'cursor': plan.cursor_slot_id, 'mode': plan.mode,
                'input_paused': plan.input_paused,
                'target': root.var_target.get(),
            })
            return None

        registered = []
        for sequence in ('<KeyPress>', '<KeyRelease>'):
            registered.append((sequence, root.bind_class(tag, sequence, on_key)))
        root.bindtags((tag, *original_tags))
        try:
            for index, key in enumerate(('0', '7', '1', '6', '0', '0', '9', 'period'), 1):
                self.press(key)
                plan = root.ctrl.entry_plan
                trace.append({'after_key': key, 'physical_remaining': root.ctrl.state().current.shoe.physical_remaining(),
                              'plan': plan.to_dict(), 'errors': list(self.errors)})
                detail = json.dumps(trace, ensure_ascii=False, indent=2, default=str)
                self.assertEqual(self.errors, [], detail)
                self.assertEqual(root.ctrl.state().current.shoe.physical_remaining(), 312 - index, detail)
                self.assertEqual(len(plan.filled_slots), index, detail)
            table = root.ctrl.state().current.table
            self.assertEqual(table.players['玩家1'].hands[0].ranks, ['T', 'T'])
            self.assertEqual(table.players['玩家2'].hands[0].ranks, ['7', 'T'])
            self.assertEqual(table.players['玩家3'].hands[0].ranks, ['A', '9'])
            self.assertEqual(root.ctrl.state().current.shoe.unrevealed_out, 1)
            self.assertFalse(table.dealer_hole_checked_negative)
        finally:
            root.bindtags(original_tags)
            for sequence, command in registered:
                root.unbind_class(tag, sequence)
                root.deletecommand(command)

    def start_same_value(self, das=False, cards=('T', 'T')):
        self.app.act_research_template(split=not das, das=das, same_value=True)
        self.app.act_new_shoe()
        self.app.act_new_round()
        for rank in (cards[0], '6', cards[1]):
            self.app._key_rank(rank)
        self.app._key_hole()
        self.assertEqual(self.errors, [])

    def test_last_player_bust_switches_actual_recording_target_to_dealer(self):
        self.start_same_value(cards=('T', '6'))
        self.app._key_rank('8')
        self.assertEqual(self.errors, [])
        self.assertTrue(self.app.ctrl.state().current.table.players['玩家1'].hands[0].is_closed)
        self.assertEqual(self.app.ctrl.entry_plan.mode, 'dealer_phase')
        self.assertEqual(self.app.var_target.get(), '庄家')
        self.assertIn('下一张给：庄家', self.app.var_entry_prompt.get())
        self.assert_dealer_projection()
        self.assertIn('录入 玩家1 <- 8', self.app.var_status.get())

    def test_last_split_das_card_switches_target_and_reveal_reuses_hole(self):
        self.start_same_value(das=True, cards=('8', '8'))
        self.app.act_action(ACTION_SPLIT)
        self.app._key_rank('9')    # First hand is 17.
        self.app._key_stand()
        self.app._key_rank('3')    # Second hand is 11.
        self.app.act_action(ACTION_DOUBLE)
        self.app._key_rank('T')    # The unique DAS card closes the last hand.
        self.assertEqual(self.errors, [])
        self.assertEqual(self.app.ctrl.entry_plan.mode, 'dealer_phase')
        self.assertEqual(self.app.var_target.get(), '庄家')
        self.assert_dealer_projection()
        self.assertIn('录入 玩家1 <- T', self.app.var_status.get())
        before = self.app.ctrl.state().current.shoe.physical_remaining()
        self.app.var_mode.set('揭示')
        self.app._key_rank('9')
        self.assertEqual(self.errors, [])
        self.assertEqual(self.app.ctrl.state().current.shoe.physical_remaining(), before)
        self.assertEqual(self.app.ctrl.state().current.shoe.unrevealed_out, 0)


    def assert_dealer_projection(self):
        seg = self.app._current_seg()
        dealer_hand = seg.table.dealer.hands[0]
        self.assertEqual(self.app.var_target.get(), '庄家')
        self.assertEqual(self.app._selected_hand_id(seg), dealer_hand.hand_id)
        self.assertIn('下一张给：庄家', self.app.var_entry_prompt.get())
        self.assertIn('录入目标：庄家', self.app.var_entry_prompt.get())
        self.assertEqual(self.app.var_analysis_target.get(), '玩家1')
        self.assertFalse(seg.table.dealer_hole_checked_negative)
        self.assertEqual(self.errors, [])

    def test_last_split_twenty_one_switches_target_and_survives_restart(self):
        from blackjack_lab.ui.app import BlackjackLabApp
        self.start_same_value(cards=('T', 'T'))
        self.press('slash')
        self.press('8')
        self.press('minus')
        self.press('1')  # Split-hand 21 closes the last player hand.
        self.assert_dealer_projection()
        self.assertIn('录入 玩家1 <- A', self.app.var_status.get())
        before = self.app.ctrl.ledger.to_list()
        self.close_app()
        self.app = BlackjackLabApp(self.db)
        self.app.update()
        self.assert_dealer_projection()
        self.assertEqual(self.app.ctrl.ledger.to_list(), before)

    def test_last_normal_stand_projects_dealer_without_extra_card_event(self):
        self.start_same_value(cards=('T', '7'))
        before = self.app.ctrl.ledger.to_list()
        self.press('minus')
        self.assert_dealer_projection()
        added = self.app.ctrl.ledger.to_list()[len(before):]
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0]['etype'], 'PLAYER_ACTION')
        self.assertEqual(added[0]['payload']['action'], '停牌')

    def test_bust_undo_restores_player_then_repeated_bust_returns_to_dealer(self):
        self.start_same_value(cards=('T', '6'))
        self.press('8')
        self.assert_dealer_projection()
        self.press('BackSpace')
        self.assertEqual(self.app.var_target.get(), '玩家1')
        self.assertEqual(self.app.ctrl.entry_plan.mode, 'player_continuation')
        self.assertEqual(self.app.ctrl.state().current.table.players['玩家1'].hands[0].ranks, ['T', '6'])
        self.assertIsNone(self.app.ctrl.entry_plan.last_saved)
        self.assertIn('刚刚记入：—', self.app.var_entry_prompt.get())
        from blackjack_lab.ui.app import BlackjackLabApp
        before = self.app.ctrl.ledger.to_list()
        self.close_app()
        self.app = BlackjackLabApp(self.db)
        self.app.update()
        self.assertEqual(self.app.ctrl.ledger.to_list(), before)
        self.assertIn('刚刚记入：—', self.app.var_entry_prompt.get())
        self.press('9')
        self.assert_dealer_projection()

    def test_first_player_bust_projects_next_player_before_dealer(self):
        self.app.act_research_template()
        self.app.act_new_shoe()
        self.app.var_participants['玩家2'].set(True)
        self.app.act_new_round()
        for key in ('0', '0', '6', '6', '7', 'period'):
            self.press(key)
        self.press('8')
        self.assertEqual(self.app.var_target.get(), '玩家2')
        self.assertEqual(self.app.ctrl.entry_plan.mode, 'player_continuation')
        self.assertIn('下一张给：玩家2／第1手', self.app.var_entry_prompt.get())
        self.press('minus')
        self.assert_dealer_projection()


    def test_recording_prompt_is_fully_visible_at_default_and_minimum_size(self):
        self.app.act_research_template()
        self.app.act_new_shoe()
        self.app.var_participants['玩家3'].set(True)
        self.app.var_participants['玩家5'].set(True)
        self.app.act_new_round()
        for key in ('0', '7', '1', '6', '0', '0', '9'):
            self.press(key)
        for width, height in ((1360, 900), (1180, 800)):
            with self.subTest(width=width, height=height):
                self.app.geometry(f'{width}x{height}')
                self.app.update()
                label = self.app.entry_prompt_label
                self.assertTrue(label.winfo_ismapped())
                self.assertGreaterEqual(label.winfo_height(), label.winfo_reqheight())
                ancestor = label.master
                left, top = label.winfo_rootx(), label.winfo_rooty()
                right, bottom = left + label.winfo_width(), top + label.winfo_height()
                while ancestor is not None:
                    self.assertGreaterEqual(left, ancestor.winfo_rootx())
                    self.assertGreaterEqual(top, ancestor.winfo_rooty())
                    self.assertLessEqual(right, ancestor.winfo_rootx() + ancestor.winfo_width())
                    self.assertLessEqual(bottom, ancestor.winfo_rooty() + ancestor.winfo_height())
                    ancestor = ancestor.master
                prompt = self.app.var_entry_prompt.get()
                for text in ('下一张给：庄家暗牌', '刚刚记入：玩家5', '录入目标：庄家'):
                    self.assertIn(text, prompt)
