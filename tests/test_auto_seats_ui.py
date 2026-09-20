import time
import unittest
from unittest.mock import patch

from tests import test_analysis_ui as fixture
from blackjack_lab.ui.app import BlackjackLabApp


class TestAutoSeatsUI(unittest.TestCase):
    setUp = fixture.TestAnalysisUI.setUp
    close = fixture.TestAnalysisUI.close

    def start(self, players=3):
        app = self.app
        app.analysis_panel.auto.set(True)
        app.act_research_template()
        app.act_new_shoe()
        for _ in range(players - 1):
            app.compact_panel.add_player.invoke()
        app.act_new_round()
        for rank in ('T',) * players + ('6',) + tuple(str(6 + i % 4) for i in range(players)):
            app._key_rank(rank)
        app._key_hole()

    def wait_all(self):
        end = time.perf_counter() + 15
        while time.perf_counter() < end:
            self.app.update()
            rows = self.app.analysis_panel.overview.rows
            if rows and all(row['result'] is not None for row in rows.values()):
                return rows
            time.sleep(.01)
        self.fail({seat: row['state'] for seat, row in rows.items()})

    def test_application_default_is_automatic(self):
        self.close()
        self.app = BlackjackLabApp(self.db)
        self.assertTrue(self.app.analysis_panel.auto.get())

    def test_player_buttons_preserve_current_round_and_take_effect_next_round(self):
        self.start(players=2)
        app = self.app
        before = app.ctrl.ledger.to_list()
        app.compact_panel.add_player.invoke()
        self.assertEqual(app.compact_panel.player_count.get(), '下一轮 3 人')
        self.assertEqual(app.ctrl.state().current.table.participants, ['玩家1', '玩家2'])
        self.assertEqual(app.ctrl.ledger.to_list(), before)
        app.compact_panel.remove_player.invoke()
        app.compact_panel.remove_player.invoke()
        self.assertEqual(app.compact_panel.player_count.get(), '下一轮 1 人')
        self.assertTrue(app.compact_panel.remove_player.instate(['disabled']))
        app.ctrl.end_round_unsettled('按钮验收', 'complete')
        app.act_new_round()
        self.assertEqual(app.ctrl.state().current.table.participants, ['玩家1'])
        self.assertEqual(self.errors, [])

    def test_every_seat_automatically_updates_and_old_rows_clear_on_new_card(self):
        self.start()
        rows = self.wait_all()
        self.assertTrue(all(row['result']['status'] == 'available' for row in rows.values()))
        view = self.app.compact_panel
        self.assertEqual(len(view.seat_table.get_children()), 3)
        self.assertTrue(view.seat_table.winfo_ismapped())
        self.assertIn('其他玩家之后不再补牌', view.notes.get())
        old = {seat: row['result']['input_digest'] for seat, row in rows.items()}
        self.app._key_rank('2')
        self.assertTrue(all(row['result'] is None for row in self.app.analysis_panel.overview.rows.values()))
        rows = self.wait_all()
        self.assertTrue(all(row['result']['input_digest'] != old[seat] for seat, row in rows.items()))
        for row in rows.values():
            self.assertIn('per-seat-others-no-draw-v1', row['result']['input']['support_scope'])
        self.assertEqual(self.errors, [])

    def test_cancel_stops_both_workers_until_new_input(self):
        self.start()
        self.wait_all()
        self.app.analysis_panel.cancel()
        deadline = time.perf_counter() + .5
        while time.perf_counter() < deadline:
            self.app.update()
            time.sleep(.01)
        panel = self.app.analysis_panel
        self.assertIsNone(panel.service.active)
        self.assertIsNone(panel.overview.service.active)
        self.assertTrue(all(row['result'] is None for row in panel.overview.rows.values()))
        self.app._key_rank('2')
        self.wait_all()

    def test_seven_rows_and_player_controls_fit_compact_minimum(self):
        self.start(players=7)
        app, view = self.app, self.app.compact_panel
        app.geometry('660x460')
        app.update()
        self.assertEqual(len(view.seat_table.get_children()), 7)
        for widget in (view.add_player, view.remove_player, view.seat_table, view.details_button):
            self.assertTrue(widget.winfo_ismapped())
            self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(), app.winfo_rootx() + app.winfo_width())
            self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(), app.winfo_rooty() + app.winfo_height())

    def test_background_save_failure_is_visible_without_losing_recorded_cards(self):
        with patch.object(self.app.ctrl.analysis_store, 'save', side_effect=OSError('test disk full')):
            self.start()
            rows = self.wait_all()
        self.assertEqual(len(self.app.ctrl.ledger.events), 11)
        for seat in ('玩家2', '玩家3'):
            self.assertEqual(rows[seat]['state'], '保存待重试')
            self.assertEqual(self.app.compact_panel.seat_table.item(seat, 'values')[-1], '保存待重试')


if __name__ == '__main__':
    unittest.main()
