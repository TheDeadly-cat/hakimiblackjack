"""r1 F01-F09 prerequisites. Failed findings are retained in unique run output."""
import copy
import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from blackjack_lab.core.rules import RuleProfile
from blackjack_lab.core.table import TableState, TableError
from blackjack_lab.ledger.events import Event
from blackjack_lab.ledger.ledger import EventLedger, LedgerError
from blackjack_lab.storage.export import export_json, export_csv, import_json
from blackjack_lab.ui.controller import SessionController


class TestRecordingPrerequisites(unittest.TestCase):
    def test_split_ace_resplit_option_does_not_allow_third_card(self):
        t = TableState(RuleProfile(resplit_aces=True))
        t.start_round(["玩家1"])
        h = t.add_card("玩家1", "A")
        t.add_card("玩家1", "A")
        t.apply_action("玩家1", h.hand_id, "分牌")
        t.add_card("玩家1", "A", hand_id=h.hand_id)
        with self.assertRaises(TableError):
            t.add_card("玩家1", "9", hand_id=h.hand_id)
        self.assertEqual(h.ranks, ["A", "A"])
        t.apply_action("玩家1", h.hand_id, "分牌")
        self.assertEqual(len(t.players["玩家1"].hands), 3)

    def test_declared_participant_without_cards_blocks_settlement(self):
        t = TableState(RuleProfile(dealer_soft17="S17"))
        t.start_round(["玩家1", "玩家2"])
        for seat, cards in [("庄家", ["10", "8"]), ("玩家1", ["10", "9"])]:
            for card in cards:
                t.add_card(seat, card)
        with self.assertRaises(TableError):
            t.settle()

    def test_dealer_cannot_draw_past_s17_terminal(self):
        t = TableState(RuleProfile(dealer_soft17="S17"))
        t.start_round(["玩家1"])
        t.add_card("庄家", "A")
        t.add_card("庄家", "6")
        with self.assertRaises(TableError):
            t.add_card("庄家", "10")
        self.assertEqual(t.dealer.hands[0].ranks, ["A", "6"])

    def test_dealer_h17_soft_hit_is_legal_but_hard17_stops(self):
        t = TableState(RuleProfile(dealer_soft17="H17"))
        t.start_round(["玩家1"])
        t.add_card("庄家", "A")
        t.add_card("庄家", "6")
        t.add_card("庄家", "10")
        with self.assertRaises(TableError):
            t.add_card("庄家", "2")

    def test_dealer_reveal_checks_prior_stopping_point(self):
        t = TableState(RuleProfile(dealer_soft17="S17"))
        t.start_round(["玩家1"])
        h = t.add_card("庄家", "A")
        t.add_card("庄家", "?", hidden=True)
        t.add_card("庄家", "10")
        with self.assertRaises(TableError):
            t.reveal_card("庄家", h.hand_id, None, "6")
        self.assertEqual([c.rank for c in h.cards], ["A", "?", "10"])

    def test_event_required_fields_not_defaulted_on_import(self):
        ledger = EventLedger("strict")
        ledger.start_session()
        for field in ("event_time", "source", "confirm_status", "observed_at"):
            data = ledger.to_list()
            del data[0][field]
            with self.subTest(field=field), self.assertRaises((ValueError, LedgerError)):
                EventLedger.from_list("strict", data)

    def test_invalid_event_values_rejected(self):
        for kwargs in ({"event_id": ""}, {"event_time": float("nan")}, {"seq": True},
                       {"confirm_status": "guess"}, {"payload": []}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                Event("SESSION_STARTED", **kwargs)

    def test_non_last_undo_cannot_rewrite_state_from_import(self):
        ledger = EventLedger("strict")
        ledger.start_session()
        ledger.create_shoe(RuleProfile())
        ledger.start_round(["玩家1"])
        first = ledger.deal("玩家1", "8")
        ledger.deal("玩家1", "9")
        data = ledger.to_list()
        event = copy.deepcopy(data[-1])
        event.update(event_id="bad-undo", seq=6, etype="UNDO",
                     payload={"target_event_id": first.event_id, "reason": "非逆序撤销"})
        with self.assertRaises((ValueError, LedgerError)):
            EventLedger.from_list("strict", data + [event])


class TestDataProtectionPrerequisites(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.ctrl = SessionController(self.directory / "records.json")  # A DB need not end in .db.
        self.addCleanup(self.ctrl.close)

    def test_export_rejects_active_database_even_with_json_name(self):
        db = self.directory / "records.json"
        before = db.read_bytes()
        for export in (export_json, export_csv):
            with self.subTest(export=export.__name__), self.assertRaises(ValueError):
                export(self.ctrl.ledger, db)
        self.assertEqual(db.read_bytes(), before)
        self.assertEqual(self.ctrl.store.event_count(), 1)

    def test_csv_formula_cells_are_neutralized_but_raw_audit_survives(self):
        ledger = EventLedger("csv-test")
        event = ledger.start_session()
        # Field copied into human-friendly spreadsheet columns.
        event.source = "=HYPERLINK(\"https://invalid.example/\",\"x\")"
        path = export_csv(ledger, self.directory / "export.csv")
        with path.open(encoding="utf-8-sig", newline="") as stream:
            row = next(csv.DictReader(stream))
        self.assertTrue(row["source"].startswith("'="))
        self.assertEqual(json.loads(row["event_json"])["source"], event.source)


if __name__ == "__main__":
    unittest.main()
