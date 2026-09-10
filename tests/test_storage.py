# -*- coding: utf-8 -*-
"""测试组 7：SQLite 保存/恢复、JSON 与 CSV 导入导出一致性。"""
import tempfile
import unittest
from pathlib import Path

from blackjack_lab.core.rules import RuleProfile
from blackjack_lab.ledger.ledger import EventLedger
from blackjack_lab.storage.database import LocalStore
from blackjack_lab.storage.export import export_csv, export_json, import_json


def build_ledger(session_id="s-storage", n=7):
    led = EventLedger(session_id)
    led.start_session()
    led.create_shoe(RuleProfile(n_decks=n))
    led.start_round(["玩家1"])
    led.deal("庄家", "Q")
    led.deal("庄家", None, hidden=True)
    led.deal("玩家1", "A")
    led.deal("玩家1", "K")
    hidden = [e for e in led.events if e.payload.get("face_state") == "hidden"][0]
    led.reveal(hidden.event_id, "9")
    hid = led.replay().current.table.players["玩家1"].hands[0].hand_id
    led.player_action("玩家1", hid, "停牌")
    led.end_round()
    return led


class TestSQLite(unittest.TestCase):
    def test_save_and_recover_identical(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "lab.db"
            led = build_ledger()
            store = LocalStore(db)
            store.save_ledger(led)
            store.close()

            # 模拟崩溃后重开：重新连接并恢复
            store2 = LocalStore(db)
            recovered = store2.load_ledger(led.session_id)
            store2.close()

            self.assertEqual([e.to_dict() for e in recovered.events],
                             [e.to_dict() for e in led.events])
            a = led.replay().current
            b = recovered.replay().current
            self.assertEqual(a.shoe.remaining, b.shoe.remaining)
            self.assertEqual(a.settlements, b.settlements)

    def test_save_event_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            store = LocalStore(Path(d) / "x.db")
            led = build_ledger()
            n1 = store.save_ledger(led)
            n2 = store.save_ledger(led)
            self.assertEqual(n2, 0)
            self.assertEqual(store.event_count(), n1)
            store.close()


class TestExport(unittest.TestCase):
    def test_json_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            led = build_ledger()
            p = export_json(led, Path(d) / "session.json")
            self.assertTrue(p.exists())
            led2 = import_json(p)
            self.assertEqual(led2.to_list(), led.to_list())
            seg = led2.replay().current
            ok, note = seg.shoe.conservation_check()
            self.assertTrue(ok, note)

    def test_csv_export(self):
        with tempfile.TemporaryDirectory() as d:
            led = build_ledger()
            p = export_csv(led, Path(d) / "events.csv")
            text = p.read_text(encoding="utf-8-sig")
            self.assertIn("CARD_DEALT", text)
            self.assertIn("ROUND_ENDED", text)


if __name__ == "__main__":
    unittest.main()
