"""Statistical and identity gates for the DAS 336-request matrix. Does not run the matrix."""
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from blackjack_lab.analysis.split_contracts import DAS_ENGINE, DAS_PROFILE, DAS_STRATEGY
from scripts import das_benchmarks as bench


class TestDasBenchmarkSpec(unittest.TestCase):
    def test_frozen_spec_has_336_unique_das_identities(self):
        spec = bench.load_spec()
        self.assertEqual(spec["_load_errors"], [])
        self.assertEqual(spec["count"], 336)
        self.assertEqual(spec["expected_counts"], {"presplit": 300, "dynamic": 36})
        self.assertEqual(spec["profile"], DAS_PROFILE)
        self.assertEqual(spec["required_engine"], DAS_ENGINE)
        self.assertEqual(spec["required_strategy"], DAS_STRATEGY)
        self.assertEqual(spec["budget_seconds"], 5.0)
        self.assertEqual(spec["target_p95_seconds"], 2.0)
        ids = [case["id"] for case in spec["cases"]]
        self.assertEqual(len(ids), 336)
        self.assertEqual(len(set(ids)), 336)
        self.assertEqual(sum(case["group"] == "presplit" for case in spec["cases"]), 300)
        self.assertEqual(sum(case["group"] == "dynamic" for case in spec["cases"]), 36)
        self.assertTrue((ROOT / "scripts" / "split_benchmarks.py").is_file())


class TestDasBenchmarkConstruction(unittest.TestCase):
    def test_dynamic_prefixes_match_declared_states(self):
        spec = {case["id"]: case for case in bench.load_spec()["cases"]}
        d01, _ = bench.construct_case(spec["dynamic-6-D01"])
        self.assertEqual(d01.engine_version, DAS_ENGINE)
        self.assertEqual(d01.legal_actions, ("deal",))
        self.assertEqual(len(d01.hands[0].ranks), 1)
        d02, _ = bench.construct_case(spec["dynamic-6-D02"])
        self.assertEqual(d02.legal_actions, ("stand", "hit", "double"))
        d03, _ = bench.construct_case(spec["dynamic-6-D03"])
        self.assertEqual(d03.legal_actions, ("deal",))
        self.assertTrue(d03.hands[0].forced_draw)
        self.assertEqual(d03.hands[0].bet_units, 1)
        d04, _ = bench.construct_case(spec["dynamic-6-D04"])
        self.assertEqual(d04.legal_actions, ("stand", "hit"))
        d05, _ = bench.construct_case(spec["dynamic-6-D05"])
        self.assertEqual(d05.legal_actions, ("deal",))
        self.assertEqual(d05.hands[0].bet_units, 2)
        d08, _ = bench.construct_case(spec["dynamic-6-D08"])
        self.assertEqual(d08.legal_actions, ("deal",))
        self.assertEqual(d08.active_index, 1)
        d10, _ = bench.construct_case(spec["dynamic-6-D10"])
        self.assertEqual(d10.legal_actions, ("complete",))
        self.assertEqual(sum(hand.bet_units for hand in d10.hands), 4)
        d11, _ = bench.construct_case(spec["dynamic-6-D11"])
        self.assertTrue(d11.hands[0].closed)
        self.assertEqual(d11.hands[0].bet_units, 2)
        self.assertEqual(d11.legal_actions, ("deal",))
        d12, _ = bench.construct_case(spec["dynamic-6-D12"])
        self.assertEqual(d12.legal_actions, ("complete",))
        self.assertEqual(sum(hand.bet_units for hand in d12.hands), 2)
        self.assertTrue(all(hand.split_ace for hand in d12.hands))


class TestDasBenchmarkReceiptGates(unittest.TestCase):
    def _row(self, name, decks=6, status="available", engine=DAS_ENGINE, strategy=DAS_STRATEGY,
             profile=DAS_PROFILE, wall=0.1):
        return dict(name=name, group="presplit", n_decks=decks, status=status, wall_seconds=wall,
                    engine_version=engine, strategy_version=strategy, profile_id=profile)

    def _spec(self, cases):
        return dict(count=len(cases), cases=cases, target_p95_seconds=2.0, budget_seconds=5.0,
                    _load_errors=[])

    def test_empty_matrix_fails(self):
        receipt = bench.build_receipt(self._spec([]), [], {"commit": "x"}, {}, {}, 0.0, "bin")
        self.assertFalse(receipt["passed"])
        self.assertIn("empty_matrix", receipt["gate_errors"])

    def test_missing_or_duplicate_ids_fail(self):
        cases = [{"id": "a"}, {"id": "b"}]
        rows = [self._row("a"), self._row("a")]
        receipt = bench.build_receipt(self._spec(cases), rows, {"commit": "x"}, {"f": "1"}, {"f": "1"}, 0.0, "bin")
        self.assertFalse(receipt["passed"])
        self.assertTrue({"duplicate_id", "case_mismatch"} & set(receipt["gate_errors"]))

    def test_b1_engine_fails(self):
        cases = [{"id": "a"}]
        rows = [self._row("a", engine="v0.2b1-finite-two-hand-1",
                          strategy="sequential-two-hand-total-net-hit-stand-v1",
                          profile="research-s17-us-peek-two-sequential-v1")]
        receipt = bench.build_receipt(self._spec(cases), rows, {"commit": "x"}, {"f": "1"}, {"f": "1"}, 0.0, "bin")
        self.assertFalse(receipt["passed"])
        self.assertIn("wrong_engine", receipt["gate_errors"])

    def test_source_change_fails(self):
        cases = [{"id": "a"}]
        rows = [self._row("a")]
        receipt = bench.build_receipt(self._spec(cases), rows, {"commit": "x"}, {"f": "1"}, {"f": "2"}, 0.0, "bin")
        self.assertFalse(receipt["passed"])
        self.assertIn("source_changed", receipt["gate_errors"])

    def test_timeout_keeps_denominator_and_fails(self):
        cases = [{"id": "a"}, {"id": "b"}]
        rows = [self._row("a"), self._row("b", status="timeout", wall=5.0)]
        receipt = bench.build_receipt(self._spec(cases), rows, {"commit": "x"}, {"f": "1"}, {"f": "1"}, 0.0, "bin")
        self.assertEqual(receipt["count"], 2)
        self.assertEqual(receipt["timed_out"], 1)
        self.assertFalse(receipt["all_completed"])
        self.assertFalse(receipt["passed"])

    def test_existing_output_directory_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            existing = Path(folder) / "run"
            existing.mkdir()
            with self.assertRaises(SystemExit) as error:
                bench.main(["--output", str(existing)])
            self.assertIn("拒绝覆盖", str(error.exception))

    def test_load_spec_rejects_empty_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "empty.json"
            path.write_text(json.dumps({"schema": "x", "cases": [], "count": 0}), encoding="utf-8")
            spec = bench.load_spec(path)
            self.assertIn("empty_matrix", spec["_load_errors"])


if __name__ == "__main__":
    unittest.main()
