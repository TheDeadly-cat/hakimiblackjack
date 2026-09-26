import unittest

from blackjack_lab.ledger.events import SOURCE_SIMULATOR
from tests import test_common_settings as fixture


class TestCompositeRecordingSource(unittest.TestCase):
    setUp = fixture.TestCommonSettings.setUp
    close = fixture.TestCommonSettings.close
    start = fixture.TestCommonSettings.start

    def assert_sources(self, before, expected_types):
        ledger = self.app.ctrl.ledger.to_list()
        added = ledger[before:]
        self.assertEqual([e['etype'] for e in added],expected_types)
        self.assertEqual({e['source'] for e in added},{SOURCE_SIMULATOR})
        stored = [e.to_dict() for e in self.app.ctrl.store.load_events(self.app.ctrl.session_id)]
        self.assertEqual(stored,ledger)

    def test_auto_next_and_group_undo_keep_selected_source_on_every_event(self):
        self.app.ctrl.recording_source = SOURCE_SIMULATOR
        self.start()
        for rank in ('T','6','6'):self.app._key_rank(rank)
        self.app._key_stand()
        before = len(self.app.ctrl.ledger.events)
        self.app._key_rank('A')
        self.assert_sources(before,['CARD_REVEALED','ROUND_ENDED','ROUND_STARTED'])
        before = len(self.app.ctrl.ledger.events)
        self.app.act_undo()
        self.assert_sources(before,['UNDO','UNDO','UNDO'])
        self.assertEqual(self.app.var_target.get(),'庄家')

    def test_replace_shoe_and_group_undo_keep_selected_source(self):
        self.app.ctrl.recording_source = SOURCE_SIMULATOR
        self.start()
        self.app._key_rank('8')
        before = len(self.app.ctrl.ledger.events)
        self.app.act_new_shoe()
        self.assert_sources(before,['ROUND_ENDED','SHOE_ENDED','SHOE_CREATED'])
        before = len(self.app.ctrl.ledger.events)
        self.app.act_undo()
        self.assert_sources(before,['UNDO','UNDO','UNDO'])
        self.assertEqual(self.app.ctrl.state().current.shoe.physical_remaining(),415)


if __name__=='__main__':
    unittest.main()
