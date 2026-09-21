import copy
import unittest
from unittest.mock import patch

from tests import test_simple_hole_entry as fixture
from blackjack_lab.ui.controller import SessionController
from blackjack_lab.ui.daily_flow import current_flow
from blackjack_lab.ui.deal_entry import MODE_UNALIGNED
from blackjack_lab.core.table import TableError


class TestDailyFlow(unittest.TestCase):
    setUp = fixture.TestSimpleHoleEntry.setUp
    close = fixture.TestSimpleHoleEntry.close
    prepare = fixture.TestSimpleHoleEntry.prepare
    initial = fixture.TestSimpleHoleEntry.initial

    def ready(self):
        self.initial()
        self.app._key_stand()
        self.app._key_rank('8')
        self.app._key_rank('3')
        self.assertEqual(self.errors, [])
        self.assertEqual(current_flow(self.app.ctrl).stage, 'ready')

    def test_single_click_settles_and_starts_same_shoe_without_dialog(self):
        self.ready()
        ctrl = self.app.ctrl
        before = ctrl.state().current
        count = len(ctrl.ledger.events)
        with patch('blackjack_lab.ui.app.messagebox.showinfo') as info:
            self.app.compact_panel.flow_primary.invoke()
        after = ctrl.state().current
        self.assertEqual(len(ctrl.ledger.events), count + 2)
        self.assertEqual([e.etype for e in ctrl.ledger.events[-2:]], ['ROUND_ENDED', 'ROUND_STARTED'])
        self.assertEqual(after.shoe_id, before.shoe_id)
        self.assertEqual(after.shoe.physical_remaining(), before.shoe.physical_remaining())
        self.assertEqual(after.table.round_no, before.table.round_no + 1)
        self.assertFalse(after.table.dealer_hole_checked_negative)
        self.assertEqual(ctrl.entry_plan.slot().seat, '玩家1')
        self.assertTrue(ctrl.entry_plan.simple_hole)
        self.assertIn('已结算', self.app.compact_panel.settlement_link.cget('text'))
        info.assert_not_called()
        self.assertEqual(self.errors, [])

    def test_stale_click_and_fresh_empty_round_cannot_repeat_transition(self):
        self.ready()
        ctrl = self.app.ctrl
        old = ctrl.state().current.round_id
        ctrl.complete_and_next_round(old, ['玩家1'])
        before = ctrl.ledger.to_list()
        for rid in (old, ctrl.state().current.round_id):
            with self.assertRaises(TableError):
                ctrl.complete_and_next_round(rid, ['玩家1'])
        self.assertEqual(ctrl.ledger.to_list(), before)

    def test_second_insert_failure_rolls_back_both_and_retry_once(self):
        self.ready()
        ctrl = self.app.ctrl
        before, plan = ctrl.ledger.to_list(), copy.deepcopy(ctrl.entry_plan.to_dict())
        insert = ctrl.store._insert
        def failing(event):
            if event.etype == 'ROUND_STARTED' and event.seq > len(before):
                raise OSError('injected second event failure')
            return insert(event)
        with patch.object(ctrl.store, '_insert', side_effect=failing), self.assertRaises(OSError):
            ctrl.complete_and_next_round(ctrl.state().current.round_id, ['玩家1'])
        self.assertEqual(ctrl.ledger.to_list(), before)
        self.assertEqual(ctrl.store.load_ledger(ctrl.session_id).to_list(), before)
        self.assertEqual(ctrl.entry_plan.to_dict(), plan)
        ctrl.complete_and_next_round(ctrl.state().current.round_id, ['玩家1'])
        self.assertEqual(len(ctrl.ledger.events), len(before) + 2)

    def test_invalid_next_round_settings_do_not_end_current_round(self):
        self.ready()
        ctrl = self.app.ctrl
        before = ctrl.ledger.to_list()
        for options in ({'participants': []}, {'participants': ['玩家2'], 'my_seat': '玩家1'},
                        {'deal_direction': 'invalid'}):
            with self.assertRaises((ValueError, TableError)):
                ctrl.complete_and_next_round(ctrl.state().current.round_id, **options)
        self.assertEqual(ctrl.ledger.to_list(), before)

    def test_sidecar_failure_retains_whole_transition_and_recovers_paused(self):
        self.ready()
        ctrl = self.app.ctrl
        count = len(ctrl.ledger.events)
        with patch.object(ctrl, '_write_entry_plan', side_effect=OSError('sidecar failure')):
            ctrl.complete_and_next_round(ctrl.state().current.round_id, ['玩家1'])
        recovered = SessionController.recover(self.db, ctrl.session_id)
        self.addCleanup(recovered.close)
        self.assertEqual(len(recovered.ledger.events), count + 2)
        self.assertEqual(recovered.state().current.table.round_no, 2)
        self.assertEqual(recovered.entry_plan.mode, MODE_UNALIGNED)
        self.assertEqual(len(recovered.state().current.settlements), 1)

    def test_combined_and_existing_two_commands_have_identical_state(self):
        self.ready()
        ctrl = self.app.ctrl
        old = copy.deepcopy(ctrl.ledger)
        old.end_round(settle=True, observation_status='complete')
        old.start_round(['玩家1'])
        ctrl.complete_and_next_round(ctrl.state().current.round_id, ['玩家1'])
        actual, expected = ctrl.state().current, old.replay().current
        self.assertEqual(actual.shoe, expected.shoe)
        self.assertEqual(actual.settlements, expected.settlements)
        self.assertEqual(actual.table.phase, expected.table.phase)
        self.assertEqual(actual.table.participants, expected.table.participants)

    def test_stages_peek_missing_cards_and_gap_never_write_on_render(self):
        self.assertEqual(current_flow(self.app.ctrl).stage, 'setup')
        self.prepare()
        self.assertEqual(current_flow(self.app.ctrl).stage, 'initial')
        for rank in ('T', 'A', '6'):
            self.app._key_rank(rank)
        self.assertEqual(current_flow(self.app.ctrl).stage, 'peek')
        ctrl = self.app.ctrl
        before = ctrl.ledger.to_list()
        for _ in range(3):
            self.app.compact_panel.render()
        self.assertEqual(ctrl.ledger.to_list(), before)
        self.assertFalse(ctrl.state().current.table.dealer_hole_checked_negative)
        with self.assertRaises(TableError):
            ctrl.complete_and_next_round(ctrl.state().current.round_id, ['玩家1'])
        ctrl.mark_gap('漏牌')
        self.assertEqual(current_flow(ctrl).stage, 'review')
        with self.assertRaisesRegex(TableError, '缺口'):
            ctrl.complete_and_next_round(ctrl.state().current.round_id, ['玩家1'])

    def test_only_settle_remains_secondary_and_does_not_start(self):
        self.ready()
        self.app.compact_panel.only_settle.invoke()
        self.assertEqual(self.app.ctrl.ledger.events[-1].etype, 'ROUND_ENDED')
        self.assertEqual(current_flow(self.app.ctrl).stage, 'start')
        self.assertEqual(self.errors, [])
