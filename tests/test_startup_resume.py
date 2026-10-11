"""Startup resumes saved input without asking or silently replacing bad history."""
from pathlib import Path
from contextlib import closing
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from blackjack_lab.analysis.split_contracts import both_initial_das_rules
from blackjack_lab.ui.app import BlackjackLabApp
from blackjack_lab.ui.controller import SessionController
from scripts.tk_lifecycle import close_app


class StartupResumeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'startup.db'
        self.apps = []
        self.addCleanup(self.close_apps)
        self.ask = patch('blackjack_lab.ui.app.messagebox.askyesno',
                         side_effect=AssertionError('Startup must not ask to enable history'))
        self.ask.start()
        self.addCleanup(self.ask.stop)

    def close_apps(self):
        for app in reversed(self.apps):
            close_app(app)
        self.apps.clear()

    def open_app(self):
        app = BlackjackLabApp(self.db, auto_analysis=False)
        app.withdraw()
        self.apps.append(app)
        return app

    def recorded_session(self, rank):
        controller = SessionController(self.db)
        try:
            controller.new_shoe(both_initial_das_rules(8))
            controller.start_round(['玩家1'], simple_hole=True)
            controller.deal_shown('玩家1', rank)
            return controller.session_id, controller.ledger.to_list(), controller.entry_plan.to_dict()
        finally:
            controller.close()

    def raw_rows(self):
        with closing(sqlite3.connect(self.db)) as connection:
            return (connection.execute('SELECT * FROM sessions ORDER BY session_id').fetchall(),
                    connection.execute('SELECT * FROM events ORDER BY session_id, seq').fetchall())

    def test_empty_database_opens_without_history_question(self):
        app = self.open_app()
        self.assertEqual(len(app.ctrl.ledger.events), 1)
        self.assertIsNone(app.ctrl.state().current)

    def test_repeated_startup_keeps_latest_recorded_session_and_exact_saved_input(self):
        first, first_events, _ = self.recorded_session('8')
        latest, expected_events, expected_plan = self.recorded_session('A')
        empty = SessionController(self.db)
        empty.close()  # A later empty startup must not replace actual recorded input.
        before = self.raw_rows()
        for _ in range(2):
            app = self.open_app()
            self.assertEqual(app.ctrl.session_id, latest)
            self.assertEqual(app.ctrl.ledger.to_list(), expected_events)
            self.assertEqual(app.ctrl.entry_plan.to_dict(), expected_plan)
            self.assertEqual(app.ctrl.store.load_ledger(first).to_list(), first_events)
            self.assertEqual(app.ctrl.state().current.shoe.physical_remaining(), 415)
            self.close_apps()
        self.assertEqual(self.raw_rows(), before)

    def test_corrupt_latest_record_stops_startup_and_preserves_all_database_rows(self):
        self.recorded_session('8')
        latest, _, _ = self.recorded_session('A')
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute('UPDATE events SET payload_json=? WHERE session_id=? AND seq=4',
                               ('{', latest))
        before = self.raw_rows()
        with self.assertRaises(ValueError):
            self.open_app()
        self.assertEqual(self.raw_rows(), before)


if __name__ == '__main__':
    unittest.main()
