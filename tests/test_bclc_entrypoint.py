"""Run the real command-line entry and Tk loop against a new owned database."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from blackjack_lab import main
from blackjack_lab.ui.app import BlackjackLabApp


class BclcEntrypointTests(unittest.TestCase):
    def test_main_selects_bclc_and_background_owner_before_clean_exit(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'entry.db'
            observed={}
            def factory(*args, **kwargs):
                app=BlackjackLabApp(*args, **kwargs)
                app.withdraw()
                def check_and_close():
                    observed.update(mode=app.var_table_mode.get(), background=app.background_recording,
                                    process=app.recording_process, auto=app.auto_analysis, db=app.ctrl.store.db_path,
                                    buttons=set(app.compact_panel.mode_buttons))
                    app.on_close()
                app.after(30, check_and_close)
                return app
            with patch('blackjack_lab.ui.app.BlackjackLabApp', side_effect=factory), \
                 patch('sys.argv', ['blackjack_lab.main', '--db', str(path), '--table-mode', 'bclc']):
                self.assertEqual(main.main(), 0)
            self.assertEqual(observed['mode'], 'bclc')
            self.assertTrue(observed['background']); self.assertTrue(observed['process'])
            self.assertTrue(observed['auto'])
            self.assertEqual(Path(observed['db']), path)
            self.assertEqual(observed['buttons'], {'pragmatic','bclc'})
            self.assertTrue(Path(str(path)+'.table-modes.json').is_file())


if __name__ == '__main__':
    unittest.main()
