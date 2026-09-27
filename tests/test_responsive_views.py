from dataclasses import FrozenInstanceError
import unittest
from unittest.mock import patch

from tests import test_analysis_ui as fixture
from blackjack_lab.analysis.contracts import digest


class ResponsiveViewTests(unittest.TestCase):
    setUp=fixture.TestAnalysisUI.setUp
    close=fixture.TestAnalysisUI.close

    def initial(self):
        app=self.app;app.act_common_settings();app.act_new_shoe();app.act_new_round();app.update()

    def test_hidden_workbench_is_dirty_without_rebuilding_its_widgets(self):
        self.initial();app=self.app
        with patch.object(app,'refresh_table',side_effect=AssertionError('hidden table redraw')),\
             patch.object(app,'refresh_composition',side_effect=AssertionError('hidden composition redraw')),\
             patch.object(app,'refresh_timeline',side_effect=AssertionError('hidden timeline redraw')):
            app._key_rank('8');app.update()
        self.assertTrue(app._workbench_dirty)
        self.assertIn('8',app.compact_panel.identity.get())
        events=app.ctrl.ledger.to_list();app.show_workbench();app.update()
        self.assertFalse(app._workbench_dirty)
        self.assertEqual(app._timeline_ids,[e['event_id'] for e in events])
        self.assertEqual(app.ctrl.ledger.to_list(),events)

    def test_visible_timeline_appends_and_undo_updates_without_selection_drift(self):
        self.initial();app=self.app;app.show_workbench();app._key_rank('8')
        index=len(app._timeline_ids)-1;selected=app._timeline_ids[index]
        app.lst_timeline.selection_set(index)
        with patch.object(app.lst_timeline,'delete',wraps=app.lst_timeline.delete) as deletion:
            app._key_rank('6');app._key_rank('8');app.act_undo();app.update()
            self.assertNotIn(((0,'end'),{}),[(c.args,c.kwargs) for c in deletion.call_args_list])
        self.assertEqual(app._selected_event().event_id,selected)
        self.assertEqual(app._timeline_ids,[e.event_id for e in app.ctrl.ledger.events])
        voided=app.ctrl.ledger._voided_ids()
        for event_id,line in zip(app._timeline_ids,app._timeline_text):
            self.assertEqual('已撤销' in line,event_id in voided)

    def test_frame_prefix_is_immutable_and_commit_invalidates_same_frame(self):
        self.initial();ctrl=self.app.ctrl
        before=ctrl.ledger.to_list()
        with ctrl.read_frame():
            first=ctrl.read_prefix();self.assertIs(first,ctrl.read_prefix())
            mutated=first.to_list();mutated[0]['payload']['note']='must not change shared prefix'
            self.assertEqual(first.to_list(),before)
            with self.assertRaises(FrozenInstanceError):first.content=b'[]'
            self.app._key_rank('8')
            second=ctrl.read_prefix()
            self.assertIsNot(first,second);self.assertNotEqual(first.prefix_digest,second.prefix_digest)
            self.assertEqual(second.prefix_digest,digest(ctrl.ledger.to_list()))
        self.assertIsNone(ctrl._read_prefix);self.assertEqual(first.to_list(),before)

    def test_new_frame_rechecks_full_content_even_when_revision_did_not_change(self):
        self.initial();ctrl=self.app.ctrl
        with ctrl.read_frame():first=ctrl.read_prefix()
        old=ctrl.ledger.events[0].payload['note']
        try:
            ctrl.ledger.events[0].payload['note']='controlled out-of-band mutation'
            with ctrl.read_frame():second=ctrl.read_prefix()
            self.assertNotEqual(first.prefix_digest,second.prefix_digest)
        finally:ctrl.ledger.events[0].payload['note']=old
