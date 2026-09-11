import copy
import json
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from blackjack_lab.analysis.contracts import research_rules, InputUnavailable, digest
from blackjack_lab.analysis.information import build_input
from blackjack_lab.analysis.service import calculate, AnalysisService
from blackjack_lab.ledger.ledger import EventLedger
from blackjack_lab.storage.analysis_snapshots import AnalysisSnapshots
from blackjack_lab.storage.export import export_json
from blackjack_lab.ui.controller import SessionController
from blackjack_lab.ui.analysis_panel import format_result


def example(n=6, cards=("10", "6"), up="10", rules=None, participants=None, peek=True):
    ledger = EventLedger("synthetic-analysis-session")
    ledger.start_session("自建数学验收数据；非真实牌桌")
    ledger.create_shoe(rules or research_rules(n))
    ledger.start_round(participants or ["玩家1"])
    ledger.deal("庄家", up, source="自建模拟器")
    ledger.deal("庄家", hidden=True, source="自建模拟器")
    peeked = False
    ten_up = up in ("A", "10", "J", "Q", "K", "T")
    for index, card in enumerate(cards):
        if index >= 2 and peek and ten_up and not peeked:
            ledger.peek_negative()
            peeked = True
        ledger.deal("玩家1", card, source="自建模拟器")
    if peek and ten_up and not peeked:
        ledger.peek_negative()
    return ledger


class TestAnalysisInput(unittest.TestCase):
    def test_ten_bucket_only_makes_pair_status_uncertain_when_pair_is_possible(self):
        nonpair = calculate(build_input(example(cards=("T", "3"), up="6"), "玩家1"))
        pair = calculate(build_input(example(cards=("T", "J"), up="6"), "玩家1"))
        self.assertFalse(nonpair["partial_comparison"])
        self.assertTrue(pair["partial_comparison"])
        self.assertEqual(pair["actions"]["split"]["status"], "pending")
        self.assertIsNone(pair["highest_ev_action"])

    def test_service_rejects_unsupported_snapshot_even_without_ui_builder(self):
        snapshot = build_input(example(), "玩家1")
        rules = json.loads(snapshot.rules_json)
        rules["dealer_soft17"] = "H17"
        forged = replace(snapshot, rules_json=json.dumps(rules))
        result = calculate(forged)
        self.assertNotEqual(result["status"], "available")
        self.assertIsNone(result["probabilities"])
        self.assertEqual(result["actions"], {})

    def test_normal_hole_works_and_partial_comparison_is_explicit(self):
        for n in (6, 7, 8):
            ledger = example(n, cards=("8", "8"), up="6")
            snapshot = build_input(ledger, "玩家1")
            self.assertEqual(sum(snapshot.counts), n * 52 - 3)
            self.assertEqual(snapshot.physical_remaining, n * 52 - 4)
            result = calculate(snapshot)
            self.assertEqual(result["status"], "available")
            self.assertTrue(result["partial_comparison"])
            self.assertIsNone(result["highest_ev_action"])
            self.assertEqual(result["actions"]["split"]["status"], "unsupported")
            self.assertNotIn("EV最高", format_result(result))

    def test_unknown_conditions_and_unsupported_rules_are_not_defaulted(self):
        cases = [("start_from_new_shoe", None, "START_UNKNOWN"),
                 ("burn_cards_known", None, "BURN_COUNT_UNKNOWN"),
                 ("initial_burn_count", None, "INITIAL_BURN_UNKNOWN"),
                 ("dealer_soft17", "H17", "RULE_COMBINATION_UNSUPPORTED"),
                 ("blackjack_payout", (6, 5), "RULE_COMBINATION_UNSUPPORTED"),
                 ("initial_burn_count", 2, "NONZERO_BURN")]
        for field, value, code in cases:
            rules = research_rules()
            setattr(rules, field, value)
            if field == "burn_cards_known":
                rules.initial_burn_count = None
            with self.subTest(field=field):
                ledger = example(rules=rules)
                with self.assertRaises(InputUnavailable) as error:
                    build_input(ledger, "玩家1")
                self.assertEqual(error.exception.code, code)

    def test_negative_peek_required_but_hole_itself_is_valid(self):
        ledger = example(peek=False)
        with self.assertRaises(InputUnavailable) as error:
            build_input(ledger, "玩家1")
        self.assertEqual(error.exception.code, "PEEK_REQUIRED")
        ledger.peek_negative()
        self.assertEqual(calculate(build_input(ledger, "玩家1"))["status"], "available")

    def test_gap_or_multiple_players_or_split_blocks_exact_input(self):
        ledger = example()
        ledger.gap("测试漏牌")
        with self.assertRaises(InputUnavailable) as error:
            build_input(ledger, "玩家1")
        self.assertEqual(error.exception.code, "RECORD_GAP")
        multi = example(participants=["玩家1", "玩家2"])
        with self.assertRaises(InputUnavailable) as error:
            build_input(multi, "玩家1")
        self.assertEqual(error.exception.code, "SINGLE_PLAYER_ONLY")
        split = example(cards=("8", "8"), up="6")
        hand = split.replay().current.table.players["玩家1"].hands[0]
        split.player_action("玩家1", hand.hand_id, "分牌")
        with self.assertRaises(InputUnavailable) as error:
            build_input(split, "玩家1")
        self.assertEqual(error.exception.code, "SPLIT_HAND_UNSUPPORTED")

    def test_future_reveals_and_corrections_cannot_change_historical_input(self):
        original = example()
        seq = original.events[-1].seq
        before = build_input(original, "玩家1", through_seq=seq)
        a, b = copy.deepcopy(original), copy.deepcopy(original)
        hidden = next(e.event_id for e in original.events if e.payload.get("face_state") == "hidden")
        a.reveal(hidden, "8")
        b.reveal(hidden, "9")
        card = next(e.event_id for e in b.events if e.payload.get("seat") == "玩家1" and e.payload.get("rank") == "6")
        b.correct(card, {"rank": "5"}, "之后核对的纠错")
        self.assertEqual(build_input(a, "玩家1", through_seq=seq), before)
        self.assertEqual(build_input(b, "玩家1", through_seq=seq), before)
        self.assertEqual(calculate(before)["actions"], calculate(build_input(b, "玩家1", through_seq=seq))["actions"])

    def test_all_negative_ev_is_not_an_opening_advantage(self):
        result = calculate(build_input(example(), "玩家1"))
        self.assertTrue(result["all_computed_ev_negative"])
        self.assertIn("均为负", format_result(result))
        self.assertIn("不等于下一轮开局优势", format_result(result))
        self.assertNotIn("opening_advantage", result)


class TestAnalysisPersistence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.c = SessionController(self.directory / "lab.db")
        self.addCleanup(self.c.close)
        ledger = example()
        self.c.store.save_ledger(ledger)
        self.c.load_session(ledger.session_id)

    def test_restart_and_historical_recompute_preserve_original(self):
        snapshot = self.c.analysis_input("玩家1")
        result = calculate(snapshot)
        saved = self.c.analysis_store.save(result)
        original_path = self.c.analysis_store.directory / (saved["snapshot_id"] + ".json")
        original_bytes = original_path.read_bytes()
        hole = next(e.event_id for e in self.c.ledger.events if e.payload.get("face_state") == "hidden")
        self.c.reveal(hole, "8")
        restarted = SessionController.recover(self.directory / "lab.db", self.c.session_id)
        self.addCleanup(restarted.close)
        recovered = restarted.analysis_store.load(saved["snapshot_id"])
        again = restarted.recompute_input(recovered)
        self.assertEqual(again, snapshot)
        new = restarted.analysis_store.save(calculate(again), saved["snapshot_id"])
        self.assertNotEqual(new["snapshot_id"], saved["snapshot_id"])
        self.assertEqual(original_path.read_bytes(), original_bytes)
        self.assertEqual(len(restarted.analysis_store.list()[0]), 2)

    def test_result_write_failure_never_reverts_events(self):
        result = calculate(self.c.analysis_input("玩家1"))
        before = self.c.ledger.to_list()
        with patch("blackjack_lab.storage.safe_files.os.fsync", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.c.analysis_store.save(result)
        self.assertEqual(self.c.ledger.to_list(), before)
        self.assertEqual(self.c.store.load_ledger(self.c.session_id).to_list(), before)
        self.assertEqual(self.c.analysis_store.list(), ([], []))

    def test_new_engine_recompute_does_not_overwrite_synthetic_old_version(self):
        old = calculate(self.c.analysis_input("玩家1"))
        old["engine_version"] = old["input"]["engine_version"] = "synthetic-old-engine-fixture"
        old["input_digest"] = digest(old["input"])
        old["fixture_note"] = "Synthetic version-compatibility test, not a historical release claim"
        saved = self.c.analysis_store.save(old)
        path = self.c.analysis_store.directory / (saved["snapshot_id"] + ".json")
        original_bytes = path.read_bytes()
        snapshot = self.c.recompute_input(saved)
        self.assertNotEqual(snapshot.engine_version, old["engine_version"])
        new = self.c.analysis_store.save(calculate(snapshot), saved["snapshot_id"])
        self.assertEqual(path.read_bytes(), original_bytes)
        self.assertNotEqual(new["snapshot_id"], saved["snapshot_id"])

    def test_failed_export_keeps_old_file_and_live_database(self):
        target = self.directory / "export.json"
        target.write_bytes(b"old export bytes")
        count = self.c.store.event_count()
        with patch("blackjack_lab.storage.safe_files.os.fsync", side_effect=OSError("full")):
            with self.assertRaises(OSError):
                export_json(self.c.ledger, target)
        self.assertEqual(target.read_bytes(), b"old export bytes")
        self.assertEqual(self.c.store.event_count(), count)

    def test_corrupted_result_is_not_silently_shown(self):
        saved = self.c.analysis_store.save(calculate(self.c.analysis_input("玩家1")))
        path = self.c.analysis_store.directory / (saved["snapshot_id"] + ".json")
        changed = json.loads(path.read_text(encoding="utf-8"))
        changed["result"]["actions"]["stand"]["ev"] = 999
        path.write_text(json.dumps(changed), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.c.analysis_store.load(saved["snapshot_id"])
        self.assertEqual(len(self.c.analysis_store.list()[1]), 1)

    def test_invalid_legacy_record_has_readonly_diagnostic_and_raw_export(self):
        self.c.store.conn.execute("UPDATE events SET confirm_status='legacy-invalid' WHERE event_id=?", (self.c.ledger.events[-1].event_id,))
        self.c.store.conn.commit()
        raw = self.c.store.diagnose_session(self.c.session_id)
        self.assertFalse(raw["valid_for_recording"])
        self.assertTrue(raw["error"])
        path = self.c.export_diagnostic(self.c.session_id, self.directory / "raw.json")
        exported = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(exported["raw_database_rows"], raw["raw_database_rows"])
        self.assertEqual(self.c.store.diagnose_session(self.c.session_id), raw)


class TestAnalysisService(unittest.TestCase):
    def wait(self, service):
        end = time.perf_counter() + 7
        while time.perf_counter() < end:
            result = service.poll()
            if result:
                return result
            time.sleep(0.02)
        self.fail("worker did not terminate within bounded deadline")

    def test_real_worker_returns_current_identity_and_drops_old_result(self):
        ledger = example()
        before = build_input(ledger, "玩家1")
        service = AnalysisService()
        self.addCleanup(service.close)
        old_id = service.start(before)
        old = calculate(before, request_id=old_id)
        ledger.deal("玩家1", "2")
        after = build_input(ledger, "玩家1")
        service.start(after)
        self.assertFalse(service.accept_result(old))
        result = self.wait(service)
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["input_digest"], after.input_digest)

    def test_cancel_and_wall_timeout_publish_no_old_values(self):
        snapshot = build_input(example(cards=("2", "3"), up="2"), "玩家1")
        service = AnalysisService()
        self.addCleanup(service.close)
        service.start(snapshot)
        service.cancel()
        self.assertIsNone(service.active)
        self.assertEqual(service.result["status"], "cancelled")
        service.start(snapshot, budget_seconds=0.001)
        result = self.wait(service)
        self.assertEqual(result["status"], "timeout")
        self.assertIsNone(result["probabilities"])
        self.assertEqual(result["actions"], {})


if __name__ == "__main__":
    unittest.main()
