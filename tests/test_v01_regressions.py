"""V0.1 复核发现的回归：持久化、状态机、信息隔离与真实 UI 入口。"""
import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from blackjack_lab.core.cards import hand_total
from blackjack_lab.core.rules import RuleProfile
from blackjack_lab.core.shoe import ShoeState, ConsistencyError
from blackjack_lab.core.table import TableState, TableError
from blackjack_lab.ledger.ledger import EventLedger, LedgerError
from blackjack_lab.ledger.events import Event, CORRECTION
from blackjack_lab.storage.database import LocalStore
from blackjack_lab.storage.export import export_json, import_json
from blackjack_lab.ui.controller import SessionController


def table(**rules):
    t = TableState(RuleProfile(**rules))
    t.start_round(["玩家1"])
    return t


def ledger():
    l = EventLedger("regression")
    l.start_session()
    l.create_shoe(RuleProfile())
    l.start_round(["玩家1"])
    return l


class TestPersistenceRegressions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "test.db"

    def controller(self):
        c = SessionController(self.db)
        self.addCleanup(c.close)
        return c

    def test_two_sessions_keep_every_event_after_reopen(self):
        a, b = self.controller(), self.controller()
        for c, n in [(a, 6), (b, 8)]:
            c.new_shoe(RuleProfile(n_decks=n))
            c.start_round(["玩家1"])
            c.deal_shown("玩家1", "A")
        for c in (a, b):
            self.assertEqual(c.store.load_ledger(c.session_id).to_list(), c.ledger.to_list())

    def test_invalid_record_never_enters_memory_or_database(self):
        c = self.controller()
        c.new_shoe(RuleProfile())
        before, count = c.ledger.to_list(), c.store.event_count()
        with self.assertRaises((LedgerError, TableError)):
            c.deal_shown("玩家1", "A")  # 尚未开轮
        self.assertEqual(c.ledger.to_list(), before)
        self.assertEqual(c.store.event_count(), count)

    def test_disk_failure_rolls_back_memory(self):
        c = self.controller()
        before = c.ledger.to_list()
        with patch.object(c.store, "save_event", side_effect=sqlite3.OperationalError("disk full")):
            with self.assertRaises(sqlite3.OperationalError):
                c.new_shoe(RuleProfile())
        self.assertEqual(c.ledger.to_list(), before)
        self.assertEqual(c.store.load_ledger(c.session_id).to_list(), before)

    def test_sequence_collision_raises_instead_of_silent_drop(self):
        c = self.controller()
        conflicting = copy.deepcopy(c.ledger.events[0])
        conflicting.event_id = "conflicting-event"
        with self.assertRaises((ValueError, sqlite3.IntegrityError)):
            c.store.save_event(conflicting)

    def test_unknown_export_version_rejected(self):
        c = self.controller()
        out = export_json(c.ledger, Path(self.tmp.name) / "export.json")
        data = json.loads(out.read_text(encoding="utf-8"))
        data["format_version"] = 999
        out.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(ValueError):
            import_json(out)


class TestShoeRegressions(unittest.TestCase):
    def test_known_card_after_full_burn_rejected_for_each_deck_count(self):
        for n in (6, 7, 8):
            with self.subTest(decks=n):
                s = ShoeState(n)
                s.burn_known_count(s.total_cards)
                with self.assertRaises(ConsistencyError):
                    s.remove_known("A")
                self.assertEqual(s.physical_remaining(), 0)

    def test_exact_ten_cannot_reuse_capacity_already_in_t_bucket(self):
        s = ShoeState(6)
        for _ in range(96):
            s.remove_known("T")
        with self.assertRaises(ConsistencyError):
            s.remove_known("K")
        self.assertTrue(s.conservation_check()[0])

    def test_gap_does_not_disable_maximum_capacity_guard(self):
        s = ShoeState(6)
        s.mark_gap("未知移除")
        with self.assertRaises(ConsistencyError):
            s.burn_known_count(313)

    def test_fractional_burn_is_rejected(self):
        with self.assertRaises(ValueError):
            ShoeState(6).burn_known_count(1.5)

    def test_partial_unknown_hand_total_is_unknown(self):
        self.assertEqual(hand_total(["A", "?"]), (None, False))


class TestLedgerRegressions(unittest.TestCase):
    def test_invalid_append_is_atomic(self):
        l = ledger()
        before = l.to_list()
        with self.assertRaises((TableError, LedgerError)):
            l.deal("玩家8", "A")
        self.assertEqual(l.to_list(), before)

    def test_closed_shoe_rejects_new_round(self):
        l = ledger()
        l.end_round()
        l.end_shoe()
        with self.assertRaises((TableError, LedgerError)):
            l.start_round()

    def test_undo_new_shoe_restores_context(self):
        l = ledger()
        old_shoe = l.replay().current.shoe_id
        l.end_round()
        l.create_shoe(RuleProfile(n_decks=8))
        l.undo_last()
        ev = l.start_round()
        self.assertEqual(ev.shoe_id, old_shoe)

    def test_reveal_exact_target_when_multiple_unknowns_have_no_track(self):
        l = ledger()
        a = l.deal("玩家1", unknown=True)
        b = l.deal("玩家1", unknown=True)
        l.reveal(b.event_id, "K")
        h = l.replay().current.table.players["玩家1"].hands[0]
        self.assertEqual([c.rank for c in h.cards], ["?", "K"])
        l.reveal(a.event_id, "A")
        self.assertEqual(l.replay().current.shoe.unrevealed_out, 0)

    def test_revealing_hidden_does_not_clear_unrelated_pending(self):
        l = ledger()
        l.deal("玩家1", unknown=True)
        h = l.deal("庄家", hidden=True)
        l.reveal(h.event_id, "9")
        self.assertEqual(l.replay().current.shoe.pending_candidates, 1)

    def test_second_reveal_of_same_card_rejected(self):
        l = ledger()
        a = l.deal("玩家1", unknown=True)
        l.deal("玩家1", unknown=True)
        l.reveal(a.event_id, "8")
        before = l.to_list()
        with self.assertRaises((LedgerError, TableError)):
            l.reveal(a.event_id, "9")
        self.assertEqual(l.to_list(), before)

    def test_corrections_compose_without_losing_previous_fix(self):
        l = ledger()
        e = l.deal("玩家1", "K")
        l.correct(e.event_id, {"rank": "Q"}, "牌面纠错")
        l.correct(e.event_id, {"suit": "H"}, "补记花色")
        c = l.replay().current.table.players["玩家1"].hands[0].cards[0]
        self.assertEqual((c.rank, c.suit), ("Q", "H"))

    def test_invalid_history_correction_is_rejected_without_poisoning(self):
        l = ledger()
        e = l.deal("玩家1", "8")
        l.deal("玩家1", "8")
        hid = l.replay().current.table.players["玩家1"].hands[0].hand_id
        l.player_action("玩家1", hid, "分牌")
        before = l.to_list()
        with self.assertRaises((LedgerError, TableError)):
            l.correct(e.event_id, {"rank": "9"}, "错误修正破坏后续分牌")
        self.assertEqual(l.to_list(), before)

    def test_hidden_face_must_not_contain_future_rank(self):
        l = ledger()
        with self.assertRaises((ValueError, LedgerError)):
            l.deal("庄家", "K", hidden=True)

    def test_ambiguous_import_sequence_rejected(self):
        data = ledger().to_list()
        data[-1]["seq"] = data[-2]["seq"]
        with self.assertRaises((ValueError, LedgerError)):
            EventLedger.from_list("regression", data)

    def test_inactive_player_cannot_receive_cards(self):
        l = ledger()
        with self.assertRaises((ValueError, TableError, LedgerError)):
            l.deal("玩家2", "K")


class TestTableRegressions(unittest.TestCase):
    def test_stand_disables_double_and_split(self):
        for action in ("加倍", "分牌"):
            with self.subTest(action=action):
                t = table()
                h = t.add_card("玩家1", "8")
                t.add_card("玩家1", "8")
                t.enter_play_phase()
                t.apply_action("玩家1", h.hand_id, "停牌")
                with self.assertRaises(TableError):
                    t.apply_action("玩家1", h.hand_id, action)

    def test_double_cannot_be_repeated_before_card_arrives(self):
        t = table()
        h = t.add_card("玩家1", "5")
        t.add_card("玩家1", "6")
        t.enter_play_phase()
        t.apply_action("玩家1", h.hand_id, "加倍")
        with self.assertRaises(TableError):
            t.apply_action("玩家1", h.hand_id, "加倍")

    def test_both_split_ace_hands_pay_normal_twenty_one(self):
        t = table()
        t.add_card("庄家", "10")
        t.add_card("庄家", "9")
        h = t.add_card("玩家1", "A")
        t.add_card("玩家1", "A")
        t.enter_play_phase()
        t.apply_action("玩家1", h.hand_id, "分牌")
        for hand in t.players["玩家1"].hands:
            t.add_card("玩家1", "K", hand_id=hand.hand_id)
        self.assertEqual([r["net_units"] for r in t.settle()], [1.0, 1.0])

    def test_original_split_hand_also_obeys_no_das(self):
        t = table(double_after_split=False)
        h = t.add_card("玩家1", "8")
        t.add_card("玩家1", "8")
        t.enter_play_phase()
        t.apply_action("玩家1", h.hand_id, "分牌")
        t.add_card("玩家1", "3", hand_id=h.hand_id)
        with self.assertRaises(TableError):
            t.apply_action("玩家1", h.hand_id, "加倍")

    def test_unknown_player_hand_cannot_be_settled(self):
        t = table()
        t.add_card("庄家", "10")
        t.add_card("庄家", "8")
        t.add_card("玩家1", "A")
        t.add_card("玩家1", "?", hidden=True)
        with self.assertRaises(TableError):
            t.settle()

    def test_unknown_bj_payout_never_defaults_to_three_to_two(self):
        t = table(blackjack_payout=None)
        t.add_card("庄家", "10")
        t.add_card("庄家", "8")
        t.add_card("玩家1", "A")
        t.add_card("玩家1", "K")
        with self.assertRaises(TableError):
            t.settle()

    def test_dealer_cannot_split(self):
        t = table()
        h = t.add_card("庄家", "8")
        t.add_card("庄家", "8")
        t.enter_play_phase()
        with self.assertRaises(TableError):
            t.apply_action("庄家", h.hand_id, "分牌")

    def test_negative_peek_rejects_contradictory_reveal(self):
        l = ledger()
        l.deal("庄家", "A")
        h = l.deal("庄家", hidden=True)
        l.peek_negative()
        before = l.to_list()
        with self.assertRaises((LedgerError, TableError)):
            l.reveal(h.event_id, "K")
        self.assertEqual(l.to_list(), before)

    def test_unknown_dealer_extra_bet_policy_cannot_settle_split(self):
        t = table()
        t.add_card("庄家", "A")
        t.add_card("庄家", "?", hidden=True)
        h = t.add_card("玩家1", "8")
        t.add_card("玩家1", "8")
        t.enter_play_phase()
        t.apply_action("玩家1", h.hand_id, "分牌")
        for hand in t.players["玩家1"].hands:
            t.add_card("玩家1", "9", hand_id=hand.hand_id)
        t.reveal_card("庄家", t.dealer.hands[0].hand_id, None, "K")
        with self.assertRaises(TableError):
            t.settle()

    def test_invalid_rules_rejected(self):
        for fields in ({"n_decks": 6.0}, {"n_seats": True},
                       {"blackjack_payout": (3, 0)}, {"surrender": "anything"},
                       {"max_split_hands": 0}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                RuleProfile(**fields)


if __name__ == "__main__":
    unittest.main()
