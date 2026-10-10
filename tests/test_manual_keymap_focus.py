"""Raw Tcl focus targets must not break the physical-key repeat guard."""
import tkinter as tk
import unittest

from blackjack_lab.ui.manual_keymap import ManualKeyBinder


class TestManualKeymapFocus(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk display unavailable: {error}")
        self.callback_errors = []
        self.root.report_callback_exception = lambda kind, error, trace: self.callback_errors.append(str(error))
        self.root.geometry('240x80')
        self.binder = ManualKeyBinder(self.root, lambda command: None,
                                      is_recording_surface=lambda event: False)
        self.root.update()

    def tearDown(self):
        if hasattr(self, 'binder'):
            self.binder.close()
        if hasattr(self, 'root'):
            self.root.destroy()
        if hasattr(self, 'callback_errors'):
            self.assertEqual(self.callback_errors, [])

    def test_raw_child_focus_keeps_held_key_latched(self):
        # Tk-created widgets, including ttk internals, have no Python wrapper.
        self.root.tk.call('entry', '.rawfield')
        self.root.tk.call('pack', '.rawfield')
        self.root.update()
        self.root.tk.call('focus', '-force', '.rawfield')
        self.root.update()
        self.assertEqual(str(self.root.tk.call('focus')), '.rawfield')
        with self.assertRaises(KeyError):
            self.root.nametowidget('.rawfield')
        self.assertTrue(self.binder.guard.accept_press('0'))

        self.binder._check_recording_focus()

        self.assertFalse(self.binder.guard.accept_press('0'))
        self.binder.guard.release('0')
        self.assertTrue(self.binder.guard.accept_press('0'))

    def test_raw_popup_focus_clears_latched_key_without_callback_error(self):
        self.root.tk.call('toplevel', '.rawpopdown')
        self.root.tk.call('entry', '.rawpopdown.field')
        self.root.tk.call('pack', '.rawpopdown.field')
        self.root.update()
        self.root.tk.call('focus', '-force', '.rawpopdown.field')
        self.root.update()
        self.assertEqual(str(self.root.tk.call('focus')), '.rawpopdown.field')
        with self.assertRaises(KeyError):
            self.root.nametowidget('.rawpopdown.field')
        self.assertTrue(self.binder.guard.accept_press('0'))

        self.binder._check_recording_focus()

        self.assertTrue(self.binder.guard.accept_press('0'))


if __name__ == '__main__':
    unittest.main()
