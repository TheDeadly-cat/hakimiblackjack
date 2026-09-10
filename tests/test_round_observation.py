"""Observation completeness survives round boundaries independently of settlement."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from blackjack_lab.analysis.contracts import InputUnavailable, research_rules
from blackjack_lab.analysis.information import build_input
from blackjack_lab.ledger.events import Event, ROUND_ENDED
from blackjack_lab.ledger.ledger import EventLedger, LedgerError
from blackjack_lab.storage.export import export_json, import_json, export_csv, import_csv
from blackjack_lab.ui.controller import SessionController
from blackjack_lab.ui.app import RoundObservationDialog
from tests import test_analysis_ui as ui_fixture


def next_round(ledger):
    ledger.start_round(["玩家1"])
    ledger.deal("庄家", "10")
    ledger.deal("庄家", hidden=True)
    ledger.deal("玩家1", "10")
    ledger.deal("玩家1", "6")
    ledger.peek_negative()


def first_round(complete=False):
    ledger = EventLedger("review-synthetic")
    ledger.start_session("自建审查回归，非平台记录")
    ledger.create_shoe(research_rules())
    ledger.start_round(["玩家1"])
    ledger.deal("庄家", "9")
    ledger.deal("庄家", "8")
    ledger.deal("玩家1", "10")
    if complete:
        ledger.deal("玩家1", "6")
    return ledger


class TestRoundObservation(unittest.TestCase):
    def blocked(self, ledger):
        with self.assertRaises(InputUnavailable) as error:
            build_input(ledger, "玩家1")
        self.assertEqual(error.exception.code, "PRIOR_ROUND_OBSERVATION")

    def test_missing_initial_card_survives_round_boundary_without_parsing_reason(self):
        for declaration in ("unknown", "incomplete", "complete"):
            with self.subTest(declaration=declaration):
                ledger = first_round()
                ledger.end_round(settle=False, reason="完整、没有漏牌（此文案不可信）", observation_status=declaration)
                record = ledger.replay().current.round_observations[-1]
                self.assertEqual(record["status"], "incomplete")
                self.assertEqual(record["detected_missing"][0]["code"], "PLAYER_INITIAL_MISSING")
                next_round(ledger)
                self.blocked(ledger)

    def test_explicit_complete_unsettled_round_keeps_analysis_eligible(self):
        ledger = first_round(complete=True)
        ledger.end_round(settle=False, reason="尚不统计收益", observation_status="complete")
        self.assertEqual(ledger.replay().current.settlements, [])
        self.assertFalse(ledger.replay().current.shoe.gap)
        next_round(ledger)
        self.assertEqual(build_input(ledger, "玩家1").player, (10, 6))

    def test_legacy_missing_field_remains_unknown_and_original_rows_unchanged(self):
        for settle in (False, True):
            ledger = first_round(complete=True)
            ledger.end_round(settle=settle)
            raw = ledger.to_list()
            del raw[-1]["payload"]["observation_status"]
            restored = EventLedger.from_list(ledger.session_id, raw)
            self.assertEqual(restored.to_list(), raw)
            record = restored.replay().current.round_observations[-1]
            self.assertEqual(record["status"], "unknown")
            self.assertTrue(record["legacy_unspecified"])
            next_round(restored)
            self.blocked(restored)

    def test_recovery_import_and_replay_preserve_both_complete_and_missing(self):
        for complete in (False, True):
            with self.subTest(complete=complete), tempfile.TemporaryDirectory() as tmp:
                ledger = first_round(complete)
                ledger.end_round(settle=False, observation_status="complete")
                next_round(ledger)
                raw = ledger.to_list()
                ctrl = SessionController(Path(tmp)/"test.db")
                try:
                    ctrl.store.save_ledger(ledger)
                    restored = SessionController.recover(Path(tmp)/"test.db", ledger.session_id)
                    try:
                        copies = [restored.ledger, EventLedger.from_list(ledger.session_id, raw)]
                        for suffix, export, load in (("json", export_json, import_json), ("csv", export_csv, import_csv)):
                            copies.append(load(export(ledger, Path(tmp)/("export."+suffix))))
                        for copy in copies:
                            self.assertEqual(copy.to_list(), raw)
                            if complete:
                                self.assertEqual(build_input(copy, "玩家1").player, (10, 6))
                            else:
                                self.blocked(copy)
                    finally:
                        restored.close()
                finally:
                    ctrl.close()

    def test_undo_reopens_round_and_recorded_missing_card_restores_completeness(self):
        ledger = first_round()
        ended = ledger.end_round(settle=False)
        original = ended.to_dict()
        ledger.undo_last()
        self.assertFalse(ledger.replay().current.shoe.gap)
        ledger.deal("玩家1", "6")
        ledger.end_round(settle=False, observation_status="complete")
        next_round(ledger)
        self.assertEqual(build_input(ledger, "玩家1").player, (10, 6))
        self.assertEqual(ledger._find(ended.event_id).to_dict(), original)

    def test_append_only_observation_correction_preserves_historical_unknown_prefix(self):
        ledger = first_round(True)
        ended = ledger.end_round(settle=False)
        raw_end = ended.to_dict()
        next_round(ledger)
        seq = ledger.events[-1].seq
        self.blocked(ledger)
        ledger.correct(ended.event_id, {"observation_status": "complete"}, "已核对全部移出牌")
        self.assertEqual(build_input(ledger, "玩家1").player, (10, 6))
        with self.assertRaises(InputUnavailable):
            build_input(ledger, "玩家1", through_seq=seq)
        self.assertEqual(ledger._find(ended.event_id).to_dict(), raw_end)
        ledger.undo_last()
        self.blocked(ledger)

    def test_declaring_or_correcting_complete_cannot_hide_pending_draws(self):
        for action in ("补牌", "加倍", "分牌", "庄家待补"):
            with self.subTest(action=action):
                ledger = first_round(True)
                if action == "分牌":
                    card = ledger.events[-1]
                    ledger.correct(card.event_id, {"rank": "10"}, "合成对子")
                if action == "庄家待补":
                    dealer = next(e for e in ledger.events if e.payload.get("seat") == "庄家" and e.payload.get("rank") == "8")
                    ledger.correct(dealer.event_id, {"rank": "2"}, "合成庄家11点")
                else:
                    hand = ledger.replay().current.table.players["玩家1"].hands[0]
                    ledger.player_action("玩家1", hand.hand_id, action)
                ended = ledger.end_round(settle=False, observation_status="complete")
                ledger.correct(ended.event_id, {"observation_status": "complete"}, "不能抹去待补牌事实")
                self.assertEqual(ledger.replay().current.round_observations[-1]["status"], "incomplete")
                next_round(ledger)
                self.blocked(ledger)

    def test_all_players_bust_does_not_require_unnecessary_dealer_draw(self):
        ledger = first_round(True)
        dealer = next(e for e in ledger.events if e.payload.get("seat") == "庄家" and e.payload.get("rank") == "8")
        ledger.correct(dealer.event_id, {"rank": "2"}, "合成11点庄家")
        ledger.deal("玩家1", "10")
        ledger.end_round(settle=False, observation_status="complete")
        self.assertFalse(ledger.replay().current.shoe.gap)
        next_round(ledger)
        build_input(ledger, "玩家1")

    def test_invalid_observation_values_rejected(self):
        for value in (None, True, 1, [], {}, "yes"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Event(ROUND_ENDED, {"observation_status": value})
        ledger = first_round(True)
        ended = ledger.end_round(settle=False)
        with self.assertRaises(LedgerError):
            ledger.correct(ended.event_id, {"observation_status": "yes"}, "无效字段")


class TestObservationWindows(unittest.TestCase):
    setUp = ui_fixture.TestAnalysisUI.setUp
    close = ui_fixture.TestAnalysisUI.close

    def choose_dialog(self, value=None):
        def accept():
            dialog = next(w for w in self.app.winfo_children() if isinstance(w, RoundObservationDialog))
            self.assertEqual(dialog.choices[dialog.selection.get()], "unknown")
            if value:
                dialog.selection.set(next(k for k,v in dialog.choices.items() if v == value))
            dialog.ok()
        self.app.after(30, accept)

    def test_actual_unsettled_dialog_cross_round_and_recovery(self):
        ledger = first_round()
        self.app.ctrl.store.save_ledger(ledger)
        self.app.ctrl.load_session(ledger.session_id)
        self.app.refresh_all()
        self.choose_dialog()  # Accept the real default-unknown selector.
        with patch("blackjack_lab.ui.app.simpledialog.askstring", return_value="自建漏录场景"):
            self.app.act_end_unsettled()
        self.app.act_new_round()
        self.app.var_target.set("庄家")
        self.app.act_card("10")
        self.app.act_hidden_card()
        self.app.var_target.set("玩家1")
        self.app.act_card("10")
        self.app.act_card("6")
        self.app.act_peek_negative()
        self.assertTrue(self.app.analysis_panel.compute_button.instate(["disabled"]))
        self.assertIn("此前第1轮", self.app.analysis_panel.status.get())
        self.assertEqual(self.errors, [])
