"""Actual das-1 snapshot files: display warning, refuse old engine, recompute to das-2."""
import json
import tempfile
import unittest
from pathlib import Path

from blackjack_lab.analysis.contracts import digest
from blackjack_lab.analysis.information import build_input
from blackjack_lab.analysis.service import calculate
from blackjack_lab.analysis.split_contracts import (
    DAS_ENGINE, DAS_ENGINE_LEGACY, DAS_STRATEGY, DAS_STRATEGY_LEGACY, SplitAnalysisInput)
from blackjack_lab.ledger.events import SOURCE_SIMULATOR
from blackjack_lab.storage.analysis_snapshots import AnalysisSnapshots
from blackjack_lab.ui.controller import SessionController
from blackjack_lab.ui.split_display import format_split_result
from tests.test_analysis_integration import example
from blackjack_lab.analysis.split_contracts import das_research_rules, split_research_rules


def _legacy_copy(result):
    data = json.loads(json.dumps(result))
    data["engine_version"] = DAS_ENGINE_LEGACY
    data["strategy_version"] = DAS_STRATEGY_LEGACY
    data["input"]["engine_version"] = DAS_ENGINE_LEGACY
    data["input"]["strategy_version"] = DAS_STRATEGY_LEGACY
    data["input_digest"] = digest(data["input"])
    return data


class TestDasHistoryRoundtrip(unittest.TestCase):
    def test_legacy_das1_file_displays_warning_and_keeps_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            ctrl = SessionController(Path(folder) / "lab.db", recording_source=SOURCE_SIMULATOR)
            try:
                ctrl.ledger = example(cards=("8", "8"), up="6", rules=das_research_rules())
                ctrl.session_id = ctrl.ledger.session_id
                ctrl.store.save_ledger(ctrl.ledger)
                current = calculate(build_input(ctrl.ledger, "玩家1"))
                self.assertEqual(current["status"], "available", current["reason"])
                saved = ctrl.analysis_store.save(current)
                path = ctrl.analysis_store.directory / (saved["snapshot_id"] + ".json")
                original = path.read_bytes()
                legacy_result = _legacy_copy(current)
                legacy = ctrl.analysis_store.save(legacy_result)
                legacy_path = ctrl.analysis_store.directory / (legacy["snapshot_id"] + ".json")
                before = legacy_path.read_bytes()
                loaded = ctrl.analysis_store.load(legacy["snapshot_id"])
                text = format_split_result(loaded["result"], historical=True)
                self.assertIn("旧算法历史结果", text)
                self.assertIn(DAS_ENGINE_LEGACY, text)
                self.assertEqual(legacy_path.read_bytes(), before)
                snapshot = SplitAnalysisInput.from_dict(loaded["result"]["input"])
                snapshot.validate()
                refused = calculate(snapshot)
                self.assertEqual(refused["status"], "failed")
                self.assertIn("已升级", refused["reason"])
                rebuilt = ctrl.recompute_input(loaded)
                self.assertEqual(rebuilt.engine_version, DAS_ENGINE)
                self.assertEqual(rebuilt.strategy_version, DAS_STRATEGY)
                recomputed = calculate(rebuilt)
                self.assertEqual(recomputed["status"], "available", recomputed["reason"])
                self.assertEqual(recomputed["engine_version"], DAS_ENGINE)
                newer = ctrl.analysis_store.save(recomputed, legacy["snapshot_id"])
                self.assertEqual(newer["recomputed_from"], legacy["snapshot_id"])
                self.assertEqual(legacy_path.read_bytes(), before)
                self.assertEqual(path.read_bytes(), original)
                self.assertNotEqual(newer["snapshot_id"], legacy["snapshot_id"])
            finally:
                ctrl.close()

    def test_mixed_history_isolates_bad_files(self):
        with tempfile.TemporaryDirectory() as folder:
            store = AnalysisSnapshots(Path(folder) / "mix")
            store.directory.mkdir()
            das = calculate(build_input(example(cards=("8", "8"), up="6", rules=das_research_rules()), "玩家1"))
            b1 = calculate(build_input(example(cards=("8", "8"), up="6", rules=split_research_rules()), "玩家1"))
            store.save(das)
            store.save(_legacy_copy(das))
            store.save(b1)
            (store.directory / "deadbeefdeadbeefdeadbeefdeadbeef.json").write_text("{", encoding="utf-8")
            entries, damaged = store.list()
            engines = {item["result"]["engine_version"] for item in entries}
            self.assertIn(DAS_ENGINE, engines)
            self.assertIn(DAS_ENGINE_LEGACY, engines)
            self.assertIn("v0.2b1-finite-two-hand-1", engines)
            self.assertEqual(len(damaged), 1)
            self.assertIn("deadbeefdeadbeefdeadbeefdeadbeef.json", damaged[0]["file"])

    def test_missing_session_does_not_look_like_successful_recompute(self):
        with tempfile.TemporaryDirectory() as folder:
            ctrl = SessionController(Path(folder) / "lab.db", recording_source=SOURCE_SIMULATOR)
            try:
                other = example(cards=("8", "8"), up="6", rules=das_research_rules())
                result = calculate(build_input(other, "玩家1"))
                saved = ctrl.analysis_store.save(result)
                with self.assertRaises(Exception):
                    ctrl.recompute_input(saved)
            finally:
                ctrl.close()
