"""Real Tk dispatch: released equal keys versus a held-key repeat burst."""
import unittest

from tests import test_analysis_ui as fixture


class RecordingKeyQueueTests(unittest.TestCase):
    setUp = fixture.TestAnalysisUI.setUp
    close = fixture.TestAnalysisUI.close

    def test_queued_equal_cards_keep_seat_order_and_held_repeat_records_once(self):
        app = self.app
        app.act_common_settings()
        app.act_new_shoe()
        for i, variable in enumerate(app.var_participants.values()):
            variable.set(i < 3)
        app.act_new_round()
        app.focus_force()
        app.update()
        before = len(app.ctrl.ledger.events)
        # Queue native Tk events without waiting for each recording callback.
        # Four presses before one release represent one held key.
        for _ in range(4):
            app.event_generate('<KeyPress-2>', when='tail')
        app.event_generate('<KeyRelease-2>', when='tail')
        for key in ('2', '2', '6', '9', '9', '9'):
            app.event_generate('<KeyPress-' + key + '>', when='tail')
            app.event_generate('<KeyRelease-' + key + '>', when='tail')
        app.update()
        self.assertEqual(self.errors, [])
        events = app.ctrl.ledger.events[before:]
        self.assertEqual([(e.payload['seat'], e.payload['rank']) for e in events], [
            ('玩家1', '2'), ('玩家2', '2'), ('玩家3', '2'), ('庄家', '6'),
            ('玩家1', '9'), ('玩家2', '9'), ('玩家3', '9'), ('庄家', None)])
        self.assertEqual(app.ctrl.store.load_ledger(app.ctrl.session_id).to_list(), app.ctrl.ledger.to_list())
        self.assertEqual(app.var_target.get(), '玩家1')
        self.assertEqual(app.ctrl.state().current.shoe.physical_remaining(), 408)
