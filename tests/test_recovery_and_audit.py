"""旧库迁移、异常退出、事务导入和历史信息隔离的独立验证。"""
import copy
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from blackjack_lab.core.rules import RuleProfile
from blackjack_lab.core.table import TableState, TableError
from blackjack_lab.ledger.events import CANDIDATE
from blackjack_lab.ledger.ledger import EventLedger, LedgerError
from blackjack_lab.storage.database import LocalStore
from blackjack_lab.storage.export import export_csv, import_csv
from blackjack_lab.ui.controller import SessionController


class TestRecoveryAndAudit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)

    def store(self, path=None):
        store = LocalStore(path or self.path / "test.db")
        self.addCleanup(store.close)
        return store

    def ledger(self, session="audit"):
        ledger = EventLedger(session)
        ledger.start_session("自建测试数据")
        ledger.create_shoe(RuleProfile())
        ledger.start_round(["玩家1"])
        return ledger

    def test_v1_migration_preserves_raw_events_and_makes_backup(self):
        db = self.path / "legacy.db"
        ledger = self.ledger()
        with sqlite3.connect(db) as conn:
            conn.executescript('''
                CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT INTO meta VALUES('schema_version', '1');
                CREATE TABLE sessions(session_id TEXT PRIMARY KEY, name TEXT, created_at REAL, note TEXT);
                CREATE TABLE events(event_id TEXT PRIMARY KEY, seq INTEGER NOT NULL UNIQUE,
                    etype TEXT NOT NULL, payload_json TEXT NOT NULL, event_time REAL,
                    observed_at REAL, session_id TEXT, shoe_id TEXT, round_id TEXT,
                    source TEXT, confirm_status TEXT, evidence TEXT, rule_version TEXT);
            ''')
            conn.execute("INSERT INTO sessions VALUES(?,?,?,?)", (ledger.session_id, "旧版测试", 1, "synthetic"))
            for e in ledger.events:
                conn.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                    e.event_id, e.seq, e.etype, json.dumps(e.payload), e.event_time,
                    e.observed_at, e.session_id, e.shoe_id, e.round_id,
                    e.source, e.confirm_status, e.evidence, e.rule_version))
        conn.close()
        store = self.store(db)
        self.assertEqual(store.get_meta("schema_version"), "2")
        self.assertEqual(store.load_ledger(ledger.session_id).to_list(), ledger.to_list())
        backup = Path(store.migration_backup)
        self.assertTrue(backup.exists())
        with sqlite3.connect(backup) as original:
            self.assertEqual(original.execute("SELECT value FROM meta").fetchone()[0], "1")
            self.assertEqual(original.execute("SELECT COUNT(*) FROM events").fetchone()[0], len(ledger.events))
        original.close()
        second = self.ledger("second")
        store.save_ledger(second)
        self.assertEqual(store.load_ledger("second").to_list(), second.to_list())

    def test_real_process_exit_recovers_all_committed_events(self):
        db = self.path / "crash.db"
        code = '''import os,sys
from blackjack_lab.ui.controller import SessionController
from blackjack_lab.core.rules import RuleProfile
c=SessionController(sys.argv[1])
c.new_shoe(RuleProfile(n_decks=7))
c.start_round(["玩家1"])
c.deal_shown("玩家1", "A")
os._exit(23)
'''
        result = subprocess.run([sys.executable, "-c", code, str(db)], cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 23, result.stderr.decode(errors="replace"))
        store = self.store(db)
        sid = store.list_sessions()[0]["session_id"]
        ledger = store.load_ledger(sid)
        self.assertEqual(len(ledger.events), 4)
        self.assertEqual(ledger.replay().current.shoe.physical_remaining(), 363)

    def test_import_failure_rolls_back_entire_session(self):
        store = self.store()
        store.conn.execute("CREATE TRIGGER fail_card BEFORE INSERT ON events WHEN NEW.etype='CARD_DEALT' BEGIN SELECT RAISE(ABORT, 'injected IO failure'); END")
        ledger = self.ledger()
        ledger.deal("玩家1", "A")
        with self.assertRaises(sqlite3.IntegrityError):
            store.save_ledger(ledger)
        self.assertEqual(store.event_count(), 0)
        self.assertEqual(store.list_sessions(), [])

    def test_modified_event_id_is_not_silently_ignored(self):
        store = self.store()
        ledger = self.ledger()
        store.save_ledger(ledger)
        event = copy.deepcopy(ledger.events[0])
        event.payload["note"] = "冲突内容"
        with self.assertRaises(ValueError):
            store.save_event(event)

    def test_ledger_conflicting_event_id_is_rejected(self):
        ledger = self.ledger()
        card = ledger.deal("玩家1", "A", event_id="stable-card")
        self.assertIs(ledger.deal("玩家1", "A", event_id=card.event_id), card)
        with self.assertRaises(LedgerError):
            ledger.deal("玩家1", "K", event_id=card.event_id)

    def test_import_cannot_smuggle_rule_change_as_card_correction(self):
        ledger = self.ledger()
        data = ledger.to_list()
        event = copy.deepcopy(data[-1])
        event.update(event_id="malformed-correction", seq=4, etype="CORRECTION",
                     payload={"target_event_id": data[1]["event_id"], "payload_fix": {"n_decks": 8}})
        data.append(event)
        with self.assertRaises(LedgerError):
            EventLedger.from_list(ledger.session_id, data)

    def test_import_older_prefix_cannot_remove_existing_history(self):
        store = self.store()
        ledger = self.ledger()
        prefix = copy.deepcopy(ledger)
        ledger.deal("玩家1", "A")
        store.save_ledger(ledger)
        with self.assertRaises(ValueError):
            store.save_ledger(prefix)
        self.assertEqual(store.load_ledger(ledger.session_id).to_list(), ledger.to_list())

    def test_csv_keeps_complete_evidence_identity(self):
        ledger = self.ledger()
        ledger.deal("玩家1", "A", evidence="synthetic/card-01.png", source="自建模拟器")
        ledger.correct(ledger.events[-1].event_id, {"suit": "S"}, "测试素材标注")
        path = export_csv(ledger, self.path / "events.csv")
        self.assertEqual(import_csv(path).to_list(), ledger.to_list())

    def test_future_schema_is_rejected_without_rewriting_version(self):
        db = self.path / "future.db"
        store = self.store(db)
        store.conn.execute("UPDATE meta SET value='999' WHERE key='schema_version'")
        store.conn.commit()
        store.close()
        with self.assertRaises(ValueError):
            LocalStore(db)
        with sqlite3.connect(db) as conn:
            self.assertEqual(conn.execute("SELECT value FROM meta").fetchone()[0], "999")
        conn.close()

    def test_historical_prefix_excludes_future_reveal_and_correction(self):
        ledger = self.ledger()
        card = ledger.deal("庄家", hidden=True)
        player = ledger.deal("玩家1", "8")
        boundary = player.seq
        before = ledger.replay(through_seq=boundary).current.shoe.remaining
        ledger.reveal(card.event_id, "Q")
        ledger.correct(player.event_id, {"rank": "9"}, "后续人工核对")
        self.assertEqual(ledger.replay(through_seq=boundary).current.shoe.remaining, before)
        self.assertNotEqual(ledger.replay().current.shoe.remaining, before)

    def test_undo_correction_restores_previous_payload(self):
        ledger = self.ledger()
        e = ledger.deal("玩家1", "K")
        ledger.correct(e.event_id, {"rank": "Q"}, "核对")
        ledger.undo_last()
        self.assertEqual(ledger.replay().current.table.players["玩家1"].hands[0].ranks, ["K"])

    def test_historical_unknown_correction_keeps_original_candidate(self):
        ledger = self.ledger()
        e = ledger.deal("玩家1", unknown=True, confirm_status=CANDIDATE)
        ledger.end_round(settle=False)
        ledger.start_round(["玩家1"])
        ledger.correct(e.event_id, {"rank": "A", "face_state": "shown"}, "已核对上一轮素材")
        self.assertEqual(ledger.replay().current.shoe.pending_candidates, 0)
        self.assertEqual(ledger.replay().current.shoe.exact_out["A"], 1)
        self.assertIsNone(ledger._find(e.event_id).payload["rank"])

    def test_shown_candidate_is_not_automatically_accepted(self):
        ledger = self.ledger()
        before = ledger.to_list()
        with self.assertRaises(LedgerError):
            ledger.deal("玩家1", "A", confirm_status=CANDIDATE)
        self.assertEqual(ledger.to_list(), before)

    def test_middle_shoe_start_and_unknown_burn_mark_gap(self):
        for fields in ({"start_from_new_shoe": False}, {"burn_cards_known": False}):
            ledger = EventLedger("gaps")
            ledger.create_shoe(RuleProfile(**fields))
            self.assertEqual(ledger.replay().current.shoe.integrity_state(), "信息不完整")

    def test_resplit_aces_allowed_on_either_hand_when_configured(self):
        t = TableState(RuleProfile(resplit_aces=True))
        t.start_round(["玩家1"])
        h = t.add_card("玩家1", "A")
        t.add_card("玩家1", "A")
        t.apply_action("玩家1", h.hand_id, "分牌")
        t.add_card("玩家1", "A", hand_id=h.hand_id)
        t.apply_action("玩家1", h.hand_id, "分牌")
        self.assertEqual(len(t.players["玩家1"].hands), 3)

    def test_t_bucket_split_depends_on_declared_pair_rule(self):
        t = TableState(RuleProfile(split_match="same_value"))
        t.start_round(["玩家1"])
        h = t.add_card("玩家1", "T")
        t.add_card("玩家1", "J")
        t.apply_action("玩家1", h.hand_id, "分牌")
        self.assertEqual(len(t.players["玩家1"].hands), 2)

    def test_six_to_five_pays_net_units_without_returned_principal(self):
        t = TableState(RuleProfile(blackjack_payout=(6, 5)))
        t.start_round(["玩家1"])
        for seat, cards in [("庄家", ["10", "8"]), ("玩家1", ["A", "Q"])]:
            for card in cards:
                t.add_card(seat, card)
        self.assertEqual(t.settle()[0]["net_units"], 1.2)

    def test_dealer_bj_all_bets_lost_includes_split_and_double(self):
        t = TableState(RuleProfile(dealer_bj_extra_bet_rule="all_bets_lost", double_after_split=True))
        t.start_round(["玩家1"])
        t.add_card("庄家", "A")
        t.add_card("庄家", "?", hidden=True)
        h = t.add_card("玩家1", "8")
        t.add_card("玩家1", "8")
        t.apply_action("玩家1", h.hand_id, "分牌")
        t.add_card("玩家1", "3", hand_id=h.hand_id)
        t.apply_action("玩家1", h.hand_id, "加倍")
        t.add_card("玩家1", "9", hand_id=h.hand_id)
        t.add_card("玩家1", "K", hand_id=t.players["玩家1"].hands[1].hand_id)
        t.reveal_card("庄家", t.dealer.hands[0].hand_id, None, "K")
        self.assertEqual([r["net_units"] for r in t.settle()], [-2.0, -1.0])

    def test_confirmed_dealer_bj_disables_player_actions(self):
        t = TableState(RuleProfile())
        t.start_round(["玩家1"])
        t.add_card("庄家", "A")
        t.add_card("庄家", "K")
        h = t.add_card("玩家1", "8")
        t.add_card("玩家1", "8")
        for action in ("补牌", "停牌", "加倍", "分牌", "投降"):
            with self.subTest(action=action), self.assertRaises(TableError):
                t.apply_action("玩家1", h.hand_id, action)

    def test_end_round_returns_only_current_round_results(self):
        c = SessionController(self.path / "controller.db")
        self.addCleanup(c.close)
        c.new_shoe(RuleProfile())
        for _ in range(2):
            c.start_round(["玩家1"])
            for seat, ranks in [("庄家", ["10", "8"]), ("玩家1", ["10", "9"])]:
                for rank in ranks:
                    c.deal_shown(seat, rank)
            _, results = c.end_round()
            self.assertEqual(len(results), 1)
        self.assertEqual(len(c.state().current.settlements), 2)


if __name__ == "__main__":
    unittest.main()
