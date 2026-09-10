"""实际 Tk 控件与回调的端到端检查；只使用临时数据库和自建录牌数据。"""
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from blackjack_lab.ui.app import BlackjackLabApp


class TestUIWorkflow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "ui.db"
        self.errors = []
        for name, replacement in (
            ("showerror", lambda title, message, **kw: self.errors.append(message)),
            ("showinfo", lambda *a, **kw: None),
            ("askyesno", lambda *a, **kw: True),
        ):
            context = patch("blackjack_lab.ui.app.messagebox." + name, side_effect=replacement)
            context.start()
            self.addCleanup(context.stop)
        self.app = BlackjackLabApp(self.db)
        self.addCleanup(self.close_app)
        self.app.update()

    def close_app(self):
        if self.app:
            self.app.on_close()
            self.app = None

    def start(self, n=6):
        self.app.var_decks.set(n)
        self.app.act_new_shoe()
        self.app.act_new_round()

    def deal(self, seat, *cards):
        self.app.var_target.set(seat)
        self.app.refresh_all()
        for rank in cards:
            self.app.act_card(rank)

    def test_full_ui_round_for_6_7_8_decks(self):
        for n in (6, 7, 8):
            with self.subTest(decks=n):
                self.start(n)
                self.deal("玩家1", "A", "K")
                self.deal("庄家", "10")
                self.app.act_hidden_card()
                self.app.var_mode.set("揭示")
                self.app.act_card("8")
                self.app.var_mode.set("新发牌")
                self.app.act_end_round()
                seg = self.app.ctrl.state().current
                self.assertEqual(seg.settlements[-1]["net_units"], 1.5)
                self.assertEqual(seg.shoe.physical_remaining(), n * 52 - 4)
                self.app.act_end_shoe()
        self.assertEqual(self.errors, [])

    def test_split_buttons_and_explicit_hand_selection(self):
        self.start()
        self.deal("玩家1", "8", "8")
        self.assertFalse(self.app.btn_split.instate(["disabled"]))
        self.app.btn_split.invoke()
        hands = self.app.ctrl.state().current.table.players["玩家1"].hands
        first_id = hands[0].hand_id
        label = next(label for label, hid in self.app._hand_ids.items() if hid == first_id)
        self.app.var_hand.set(label)
        self.app.act_card("3")
        self.assertEqual(self.app._selected_hand_id(self.app.ctrl.state().current), first_id)
        self.app.btn_stand.invoke()
        hands = self.app.ctrl.state().current.table.players["玩家1"].hands
        self.assertEqual(hands[0].ranks, ["8", "3"])
        self.assertEqual(hands[1].ranks, ["8"])
        self.assertTrue(hands[0].stood)
        self.assertEqual(self.errors, [])

    def test_reveal_undo_and_reveal_again(self):
        self.start()
        self.deal("庄家", "9")
        self.app.act_hidden_card()
        self.app.var_mode.set("揭示")
        self.app.act_card("8")
        self.app.act_undo()
        self.app.act_card("7")
        self.assertEqual(self.app.ctrl.state().current.table.dealer.hands[0].ranks, ["9", "7"])
        self.assertEqual(self.errors, [])

    def test_history_correction_import_export_and_resume(self):
        self.start()
        self.deal("玩家1", "K")
        self.app.lst_timeline.selection_set(len(self.app.ctrl.ledger.events) - 1)
        with patch("blackjack_lab.ui.app.simpledialog.askstring", side_effect=["Q", "自建测试牌面纠正"]):
            self.app.act_correct()
        expected = self.app.ctrl.ledger.to_list()
        self.app.act_history()
        for suffix, export_action in [("json", self.app.act_export_json), ("csv", self.app.act_export_csv)]:
            path = Path(self.tmp.name) / ("session." + suffix)
            with patch("blackjack_lab.ui.app.filedialog.asksaveasfilename", return_value=str(path)):
                export_action()
            with patch("blackjack_lab.ui.app.filedialog.askopenfilename", return_value=str(path)):
                self.app.act_import()
            self.assertEqual(self.app.ctrl.ledger.to_list(), expected)
        sid = self.app.ctrl.session_id
        self.close_app()
        self.app = BlackjackLabApp(self.db)
        self.assertEqual(self.app.ctrl.session_id, sid)
        self.assertEqual(self.app.ctrl.ledger.to_list(), expected)
        self.assertEqual(len(self.app.ctrl.list_recoverable()), 1)
        self.assertEqual(self.errors, [])

    def test_fewer_seats_and_unsettled_round(self):
        self.app.rule_details = {"n_seats": 1}
        self.start()
        self.app.act_unknown_card()
        with patch("blackjack_lab.ui.app.simpledialog.askstring", return_value="本轮玩家牌未能确认"), \
                patch.object(self.app, "ask_observation_status", return_value="unknown"):
            self.app.act_end_unsettled()
        self.app.act_new_round()
        self.assertEqual(self.app.ctrl.state().current.table.round_no, 2)
        self.assertEqual(self.app.ctrl.state().current.shoe.pending_candidates, 1)
        self.assertEqual(self.errors, [])

    def test_invalid_click_does_not_poison_next_record(self):
        self.app.act_card("A")
        self.assertEqual(len(self.errors), 1)
        self.start()
        self.deal("玩家1", "9", "8")
        self.app.btn_stand.invoke()
        count = self.app.ctrl.store.event_count()
        self.app.act_card("K")
        self.assertEqual(len(self.errors), 2)
        self.assertEqual(self.app.ctrl.store.event_count(), count)
        self.app.act_undo()
        self.app.act_card("2")
        self.assertEqual(self.app.ctrl.state().current.table.players["玩家1"].hands[0].ranks, ["9", "8", "2"])

    def test_k_button_not_overlapped_and_entry_shortcut_does_not_deal(self):
        self.start()
        def walk(widget):
            yield widget
            for child in widget.winfo_children():
                yield from walk(child)
        card_k = [w for w in walk(self.app) if w.winfo_class() == "TButton" and w.cget("text") == "K"][0]
        cell = card_k.grid_info()
        self.assertEqual(len(card_k.master.grid_slaves(row=cell["row"], column=cell["column"])), 1)
        before = len(self.app.ctrl.ledger.events)
        class KeyEvent:
            widget = self.app.cmb_hand
        self.app._shortcut(KeyEvent(), lambda: self.app.act_card("K"))
        self.assertEqual(len(self.app.ctrl.ledger.events), before)
        card_k.invoke()
        self.assertEqual(self.app.ctrl.state().current.table.players["玩家1"].hands[0].ranks, ["K"])
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
