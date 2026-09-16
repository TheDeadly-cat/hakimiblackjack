"""Drive existing F11 / operator wizards and manual counting on a temp ledger."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from blackjack_lab.ui.app import BlackjackLabApp, DEFAULT_DB, usage_session_paths


def _fingerprint(path):
    if not Path(path).is_file():
        return None
    stat = Path(path).stat()
    return (stat.st_mtime_ns, stat.st_size)


class UsageSessionPathsTest(unittest.TestCase):
    def test_usage_session_is_not_the_user_ledger(self):
        with tempfile.TemporaryDirectory() as folder:
            session = usage_session_paths(root=folder, stamp="fixture")
            self.assertNotEqual(session["db"], DEFAULT_DB.resolve())
            self.assertFalse(session["manifest"]["accepted"])
            self.assertIn("lab.db", str(session["db"]))
            self.assertTrue((session["folder"] / "session.json").is_file())


class WizardUsageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.user_db_before = _fingerprint(DEFAULT_DB)
        session = usage_session_paths(root=self.folder, stamp="usage")
        self.session_paths = session
        self.db = session["db"]
        self.still = self.folder / "still.png"
        self.still.write_bytes(b"not-a-real-frame")
        self.f11_path = self.folder / "fullscreen-wizard.json"
        self.pair_path = self.folder / "operator-pair-wizard.json"
        self.export_path = self.folder / "session-export.json"
        self.saves = [str(self.f11_path), str(self.pair_path)]

        def take_save(*_args, **_kwargs):
            return self.saves.pop(0) if self.saves else str(self.export_path)

        patches = (
            patch("blackjack_lab.ui.app.messagebox.showerror", lambda *a, **kw: None),
            patch("blackjack_lab.ui.app.messagebox.showinfo", lambda *a, **kw: None),
            patch("blackjack_lab.ui.app.messagebox.askyesno", lambda *a, **kw: True),
            patch("blackjack_lab.ui.wizard_dialogs.messagebox.showerror", lambda *a, **kw: None),
            patch("blackjack_lab.ui.wizard_dialogs.messagebox.showinfo", lambda *a, **kw: None),
            patch("blackjack_lab.ui.wizard_dialogs.messagebox.askyesno", lambda *a, **kw: True),
            patch("blackjack_lab.ui.wizard_dialogs.filedialog.asksaveasfilename", take_save),
            patch("blackjack_lab.ui.wizard_dialogs._drop_m4_inbox", lambda *a, **kw: a[-1]),
            patch("blackjack_lab.ui.wizard_dialogs.probe_environment",
                  lambda app: {"not_acceptance": True, "monitor_count": 1,
                               "screen_width": 1920, "screen_height": 1080}),
            patch("blackjack_lab.ui.wizard_dialogs.observe_foreground",
                  lambda **kw: {"foreground": {
                      "hwnd": 1, "x": 0, "y": 0, "width": 100, "height": 80},
                      "not_acceptance": True}),
            patch("blackjack_lab.ui.wizard_dialogs.grab_foreground_still",
                  lambda *a, **kw: {
                      "capture_ok": True, "path": str(self.still), "sha256": "abc",
                      "bytes": self.still.stat().st_size, "error": None,
                      "not_acceptance": True, "accepted": False,
                  }),
        )
        for context in patches:
            context.start()
            self.addCleanup(context.stop)
        self.app = BlackjackLabApp(self.db)
        self.app.usage_session = session
        self.addCleanup(self.close)
        self.app.update()

    def close(self):
        if self.app:
            self.app.on_close()
            self.app = None

    def test_manual_fullscreen_and_operator_wizards_on_temp_db(self):
        panel = self.app.act_quick_record()
        self.app.update()
        self.assertTrue(panel.winfo_exists())

        self.app.act_fullscreen_wizard()
        wizard = self.app._fullscreen_wizard
        self.app.update()
        for _ in range(6):
            wizard._done()
            self.app.update()
        wizard._skip()
        wizard._skip()
        wizard._save()
        evidence_dir = self.session_paths["folder"]
        body = json.loads((evidence_dir / "fullscreen-wizard.json").read_text(encoding="utf-8"))
        live = json.loads((evidence_dir / "fullscreen-wizard-live.json").read_text(encoding="utf-8"))
        log = (evidence_dir / "wizard-log.jsonl").read_text(encoding="utf-8")
        self.assertFalse(body["accepted"])
        self.assertFalse(live["accepted"])
        self.assertIn("fullscreen_step", log)
        self.assertIn("invoke_panel", log)
        self.assertFalse(wizard.session["accepted"])
        self.assertTrue(wizard.session["required_complete"])
        self.assertEqual(
            ["enter_f11", "invoke_panel", "enter_two_cards", "correct_one_card",
             "return_to_browser", "interrupt_source"],
            wizard.session["recorded_steps"])

        self.app.act_new_shoe()
        self.app.act_new_round()
        self.app.var_target.set("玩家1")
        self.app.refresh_all()
        self.app.act_card("K")
        self.app.act_card("7")
        self.app.lst_timeline.selection_set(len(self.app.ctrl.ledger.events) - 1)
        with patch("blackjack_lab.ui.app.simpledialog.askstring",
                   side_effect=["Q", "实测纠错，不是真实桌"]):
            self.app.act_correct()
        ranks = self.app.ctrl.state().current.table.players["玩家1"].hands[0].ranks
        self.assertEqual(["K", "Q"], ranks)
        self.assertGreater(self.app.ctrl.commit_revision, 0)
        session_id = self.app.ctrl.session_id
        with patch("blackjack_lab.ui.app.filedialog.asksaveasfilename",
                   return_value=str(self.export_path)):
            self.app.act_export_json()

        self.app.act_operator_wizard()
        operator = self.app._operator_wizard
        self.app.update()
        operator._complete_leg()
        operator._complete_leg()
        operator._export()
        pair = json.loads(
            (self.session_paths["folder"] / "operator-pair-wizard.json").read_text(encoding="utf-8"))
        self.assertFalse(pair["accepted"])
        self.assertFalse(pair["paired"])
        self.assertTrue(pair["declared_pair_ids"])
        self.assertEqual(["manual", "assisted"], [row["condition"] for row in pair["trials"]])

        self.close()
        self.app = BlackjackLabApp(self.db)
        self.app.update()
        self.assertEqual(session_id, self.app.ctrl.session_id)
        restored = self.app.ctrl.state().current.table.players["玩家1"].hands[0].ranks
        self.assertEqual(["K", "Q"], restored)
        self.assertEqual(_fingerprint(DEFAULT_DB), self.user_db_before)
        self.assertNotEqual(Path(self.db).resolve(), DEFAULT_DB.resolve())
        self.assertEqual(Path(self.app.ctrl.store.db_path).resolve(), Path(self.db).resolve())
        self.assertTrue(self.export_path.is_file())
        closed = json.loads(
            (self.session_paths["folder"] / "usage-final.json").read_text(encoding="utf-8"))
        self.assertFalse(closed["accepted"])
        self.assertEqual(session_id, closed["session_id"])
        self.assertGreaterEqual(closed["event_count"], 2)
