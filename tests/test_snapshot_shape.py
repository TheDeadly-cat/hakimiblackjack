"""N1: malformed sidecars are isolated without altering history or event data."""
import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from blackjack_lab.analysis.contracts import RESULT_SCHEMA, canonical, digest, research_rules
from blackjack_lab.analysis.information import build_input
from blackjack_lab.analysis.service import calculate
from blackjack_lab.storage.analysis_snapshots import AnalysisSnapshots, SnapshotFormatError
from blackjack_lab.ui.app import BlackjackLabApp
from tests.test_analysis_integration import example


class TestSnapshotShapes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.store = AnalysisSnapshots(self.directory)
        self.saved = self.store.save(calculate(build_input(example(), "玩家1")))
        self.original_path = self.directory / (self.saved["snapshot_id"] + ".json")
        self.original = self.original_path.read_bytes()

    def envelope(self):
        body = json.loads(self.original)
        body["snapshot_id"] = "a" * 32
        return body

    def check_bad(self, data, expected_field):
        if isinstance(data, dict):
            body = {key: value for key, value in data.items() if key != "content_digest"}
            try:
                data["content_digest"] = digest(body)
            except ValueError:
                data["content_digest"] = "0" * 64
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        path = self.directory / ("a" * 32 + ".json")
        path.write_bytes(raw)
        with self.assertRaises(SnapshotFormatError) as raised:
            self.store.load(path.stem)
        self.assertIn(expected_field, str(raised.exception))
        entries, damaged = self.store.list()
        self.assertEqual([e["snapshot_id"] for e in entries], [self.saved["snapshot_id"]])
        self.assertEqual(len(damaged), 1)
        self.assertEqual(damaged[0]["file"], path.name)
        self.assertIn(expected_field, damaged[0]["error"])
        self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(self.original_path.read_bytes(), self.original)

    def test_scalar_array_and_null_roots_have_controlled_errors(self):
        for data in (None, [], "history", 12, 1.5, True):
            with self.subTest(root=data):
                self.check_bad(data, "根结构")

    def test_result_and_input_wrong_types_are_isolated(self):
        for field in ("result", "input"):
            for value in (None, [], "text", 12, True):
                with self.subTest(field=field, value=value):
                    data = self.envelope()
                    target = data if field == "result" else data["result"]
                    target[field] = value
                    self.check_bad(data, "result" + (".input" if field == "input" else ""))

    def test_timestamp_types_nonfinite_and_out_of_range_are_isolated_before_sort(self):
        for value in (None, "today", [], {}, True, float("nan"), float("inf"), 10 ** 100):
            with self.subTest(saved_at=value):
                data = self.envelope()
                data["saved_at"] = value
                self.check_bad(data, "saved_at")

    def test_missing_envelope_and_computed_identity_fields_are_isolated(self):
        for field in ("schema", "snapshot_id", "saved_at", "result"):
            with self.subTest(envelope=field):
                data = self.envelope()
                del data[field]
                self.check_bad(data, field)
        for field in ("session_id", "shoe_id", "round_id", "seat", "hand_id", "through_seq", "n_decks", "prefix_digest"):
            with self.subTest(input=field):
                data = self.envelope()
                del data["result"]["input"][field]
                data["result"]["input_digest"] = digest(data["result"]["input"])
                self.check_bad(data, "result.input." + field)
        for field in ("input_digest", "engine_version", "strategy_version", "status", "reason", "rules_digest", "partial_comparison", "elapsed_seconds"):
            with self.subTest(result=field):
                data = self.envelope()
                del data["result"][field]
                self.check_bad(data, "result." + field)

    def test_nested_display_types_and_identity_are_checked(self):
        changes = (("input", "seat", []), ("input", "through_seq", True),
                   ("input", "player_ranks", [None]), ("input", "dealer_up", {}),
                   ("input", "peek_negative", "yes"), ("result", "schema", []),
                   ("result", "engine_version", {}), ("result", "actions", []),
                   ("result", "probabilities", None), ("result", "probability_status", []))
        for section, field, value in changes:
            with self.subTest(section=section, field=field):
                data = self.envelope()
                target = data["result"]["input"] if section == "input" else data["result"]
                target[field] = value
                data["result"]["input_digest"] = digest(data["result"]["input"])
                self.check_bad(data, ("result.input." if section == "input" else "result.") + field)
        data = self.envelope()
        data["result"]["actions"]["stand"]["ev"] = []
        self.check_bad(data, "result.actions.ev")

    def test_minimal_envelopes_and_old_engine_are_preserved_and_sorted(self):
        probe = {"synthetic_storage_probe": True}
        minimal = self.store.save({"schema": RESULT_SCHEMA, "input": probe, "input_digest": digest(probe)})
        old = copy.deepcopy(self.saved["result"])
        old["engine_version"] = "old-retained-engine"
        self.store.save(old)
        before = {path.name: path.read_bytes() for path in self.directory.glob("*.json")}
        entries, damaged = self.store.list()
        self.assertEqual(damaged, [])
        self.assertEqual(len(entries), 3)
        self.assertEqual(self.store.load(minimal["snapshot_id"]), minimal)
        self.assertEqual([e["saved_at"] for e in entries], sorted(e["saved_at"] for e in entries))
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.directory.glob("*.json")})

    def test_split_schema_common_identity_compatibility_without_single_hand_fields(self):
        from tests.test_analysis_integration import example
        from blackjack_lab.analysis.split_contracts import split_research_rules
        from blackjack_lab.analysis.information import build_input
        ledger = example(cards=('8','8'),up='6',rules=split_research_rules())
        hand_id = build_input(ledger,'玩家1').hand_id
        ledger.player_action('玩家1',hand_id,'分牌')
        result = calculate(build_input(ledger,'玩家1'))
        self.assertEqual(result['status'],'available')
        self.assertNotIn('player_ranks',result['input'])
        saved = self.store.save(result)
        self.assertEqual(canonical(self.store.load(saved["snapshot_id"])), canonical(saved))
        self.assertEqual(self.store.list()[1], [])

    def test_invalid_encoding_and_filename_do_not_change_healthy_history(self):
        cases = {"b" * 32 + ".json": b"\xff", "not-a-snapshot.json": b"{}"}
        for name, content in cases.items():
            (self.directory / name).write_bytes(content)
        entries, damaged = self.store.list()
        self.assertEqual(len(entries), 1)
        self.assertEqual({item["file"] for item in damaged}, set(cases))
        for name, content in cases.items():
            self.assertEqual((self.directory / name).read_bytes(), content)
        self.assertEqual(self.original_path.read_bytes(), self.original)


class TestSnapshotHistoryUI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.errors = []
        error = patch("blackjack_lab.ui.app.messagebox.showerror", side_effect=lambda *args, **kw: self.errors.append(args))
        error.start()
        self.addCleanup(error.stop)
        self.app = BlackjackLabApp(Path(self.tmp.name) / "n1-ui.db")
        self.addCleanup(self.app.on_close)
        ctrl = self.app.ctrl
        ctrl.new_shoe(research_rules(6))
        ctrl.start_round(["玩家1"])
        ctrl.deal_shown("庄家", "10")
        ctrl.deal_hidden("庄家")
        ctrl.deal_shown("玩家1", "10")
        ctrl.deal_shown("玩家1", "6")
        ctrl.peek_negative()
        self.app.var_target.set("玩家1")
        self.app.refresh_all()
        self.saved = ctrl.analysis_store.save(calculate(ctrl.analysis_input("玩家1")))
        self.directory = ctrl.analysis_store.directory
        self.original = (self.directory / (self.saved["snapshot_id"] + ".json")).read_bytes()
        self.events_before = canonical(ctrl.ledger.to_list())

    def window(self):
        self.app.analysis_panel.show_history()
        self.app.update()
        return next(w for w in self.app.winfo_children() if w.winfo_class() == "Toplevel")

    def test_real_tk_history_shows_filenames_reasons_and_recomputes_healthy_result(self):
        bad = self.directory / ("a" * 32 + ".json")
        bad.write_bytes(b"[]")
        win = self.window()
        texts = [w.get("1.0", "end") for w in win.winfo_children() if w.winfo_class() == "Text"]
        self.assertTrue(any("历史分析" in text and "EV" in text for text in texts))
        self.assertTrue(any(bad.name in text and "根结构" in text for text in texts))
        listing = next(w for w in win.winfo_children() if w.winfo_class() == "Listbox")
        self.assertEqual(listing.size(), 1)
        button = next(w for w in win.winfo_children() if w.winfo_class() == "TButton")
        button.invoke()
        deadline = time.perf_counter() + 7
        while not self.app.analysis_panel.saved and time.perf_counter() < deadline:
            self.app.update()
            time.sleep(0.01)
        saved = self.app.analysis_panel.saved
        self.assertIsNotNone(saved)
        self.assertEqual(saved["recomputed_from"], self.saved["snapshot_id"])
        self.assertNotEqual(saved["snapshot_id"], self.saved["snapshot_id"])
        self.assertEqual(bad.read_bytes(), b"[]")
        self.assertEqual((self.directory / (self.saved["snapshot_id"] + ".json")).read_bytes(), self.original)
        self.assertEqual(canonical(self.app.ctrl.ledger.to_list()), self.events_before)
        self.assertEqual(self.errors, [])

    def test_real_tk_minimal_envelope_is_metadata_and_cannot_recompute(self):
        probe = {"synthetic_storage_probe": True}
        self.app.ctrl.analysis_store.save({"schema": RESULT_SCHEMA, "input": probe, "input_digest": digest(probe)})
        win = self.window()
        listing = next(w for w in win.winfo_children() if w.winfo_class() == "Listbox")
        self.assertEqual(listing.size(), 2)
        self.assertIn("仅存储信封", listing.get(1))
        texts = [w.get("1.0", "end") for w in win.winfo_children() if w.winfo_class() == "Text"]
        self.assertTrue(any("不能复算" in text for text in texts))
        button = next(w for w in win.winfo_children() if w.winfo_class() == "TButton")
        self.assertTrue(button.instate(["disabled"]))
        button.invoke()
        self.app.update()
        self.assertIsNone(self.app.analysis_panel.request_id)
        self.assertEqual(canonical(self.app.ctrl.ledger.to_list()), self.events_before)
        self.assertEqual(self.errors, [])

    def test_real_tk_newer_result_uses_split_display_adapter_and_retains_legacy(self):
        # B3 replaces N1's temporary 'adapter absent' assertion with actual v2.
        from tests.test_analysis_integration import example
        from blackjack_lab.analysis.split_contracts import split_research_rules
        from blackjack_lab.analysis.information import build_input
        ledger = example(cards=('8','8'),up='6',rules=split_research_rules())
        self.app.ctrl.store.save_ledger(ledger)
        result = calculate(build_input(ledger,'玩家1'))
        self.assertEqual(result['status'],'available')
        self.app.ctrl.analysis_store.save(result)
        win = self.window()
        texts = [w.get("1.0", "end") for w in win.winfo_children() if w.winfo_class() == "Text"]
        self.assertTrue(any("两手顺序分牌" in text and '合计净收益分布' in text for text in texts))
        button = next(w for w in win.winfo_children() if w.winfo_class() == "TButton")
        self.assertFalse(button.instate(["disabled"]))
        self.assertEqual((self.directory / (self.saved['snapshot_id']+'.json')).read_bytes(),self.original)
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
