import tkinter as tk
import sys
import unittest

from tests import test_simple_hole_entry as fixture


class TestDealerKeyRepeat(unittest.TestCase):
    setUp = fixture.TestSimpleHoleEntry.setUp
    close = fixture.TestSimpleHoleEntry.close
    prepare = fixture.TestSimpleHoleEntry.prepare
    initial = fixture.TestSimpleHoleEntry.initial

    def press(self, widget, key):
        options = {'keycode': 96 + int(key[-1])} if sys.platform == 'win32' and key.startswith('KP_') else {'keysym': key}
        widget.event_generate('<KeyPress>', **options)
        self.app.update()

    def release(self, widget, key):
        options = {'keycode': 96 + int(key[-1])} if sys.platform == 'win32' and key.startswith('KP_') else {'keysym': key}
        widget.event_generate('<KeyRelease>', **options)
        self.app.update()

    def dealer(self, up='6'):
        self.initial(up=up)
        self.app._key_stand()
        button = self.app.compact_panel.card_buttons[8]
        button.focus_force()
        self.app.update()
        return button

    def test_held_bottom_card_across_internal_focus_is_only_revealed_once(self):
        button = self.dealer()
        app, ctrl = self.app, self.app.ctrl
        before = len(ctrl.ledger.events)
        hole = ctrl.ledger.events[-2].event_id
        self.press(button, '9')
        self.assertEqual(len(ctrl.ledger.events), before + 1)
        app.focus_force()
        app.update()  # Real Tk FocusOut/FocusIn, while the key is still down.
        for _ in range(4):
            self.press(app, '9')
        self.release(app, '9')
        self.assertEqual([e.etype for e in ctrl.ledger.events[before:]], ['CARD_REVEALED'])
        self.assertEqual(ctrl.ledger.events[-1].payload['target_event_id'], hole)
        self.assertEqual(ctrl.state().current.shoe.physical_remaining(), 412)
        self.assertEqual(ctrl.state().current.table.dealer.hands[0].ranks, ['6', '9'])
        self.assertEqual(self.errors, [])
        # A separate physical press must still allow a real second 9.
        self.press(app, '9')
        self.release(app, '9')
        self.assertEqual(ctrl.ledger.events[-1].etype, 'CARD_DEALT')
        self.assertEqual(ctrl.state().current.shoe.physical_remaining(), 411)

    def test_a_uppercase_one_and_numpad_one_are_keyboard_aces(self):
        for key in ('a', 'A', '1', 'KP_1'):
            with self.subTest(key=key):
                if self.app.ctrl.state().current:
                    self.app.ctrl.end_round_unsettled('separate synthetic keyboard case', 'unknown')
                    self.app.ctrl.end_shoe()
                button = self.dealer(up='2')
                before = len(self.app.ctrl.ledger.events)
                self.press(button, key)
                self.app.focus_force()
                self.app.update()
                self.press(self.app, key)
                self.release(self.app, key)
                events = self.app.ctrl.ledger.events[before:]
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0].etype, 'CARD_REVEALED')
                self.assertEqual(events[0].payload['rank'], 'A')
                self.assertEqual(self.errors, [])

    def test_numpad_hold_has_same_one_press_semantics(self):
        button = self.dealer()
        before = len(self.app.ctrl.ledger.events)
        self.press(button, 'KP_9')
        self.app.focus_force()
        self.app.update()
        self.press(self.app, 'KP_9')
        self.release(self.app, 'KP_9')
        self.assertEqual(len(self.app.ctrl.ledger.events), before + 1)
        self.assertEqual(self.app.ctrl.ledger.events[-1].etype, 'CARD_REVEALED')
        self.assertEqual(self.errors, [])

    def test_focus_leaving_recording_window_clears_stale_pressed_keys(self):
        button = self.dealer()
        self.press(button, '9')
        modal = tk.Toplevel(self.app)
        self.addCleanup(lambda: modal.destroy() if modal.winfo_exists() else None)
        entry = tk.Entry(modal)
        entry.pack()
        entry.focus_force()
        self.app.update()
        self.assertEqual(self.app._key_binder.guard.pressed, set())
        self.app.focus_force()
        self.app.update()
        self.press(self.app, '2')
        self.release(self.app, '2')
        self.assertEqual(self.app.ctrl.state().current.table.dealer.hands[0].ranks, ['6', '9', '2'])

    def test_closing_with_pending_focus_check_cancels_callback(self):
        self.app._key_binder.on_focus_out(None)
        self.assertIsNotNone(self.app._key_binder._focus_check)
        self.close()
