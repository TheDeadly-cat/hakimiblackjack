"""DAS 336 attachment must be recomputed. receipt.passed is not proof."""
import contextlib
import hashlib
import io
import json
import math
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from blackjack_lab.analysis.split_contracts import DAS_ENGINE, DAS_PROFILE, DAS_STRATEGY
from scripts import das_benchmarks as bench
from scripts.das_matrix_gate import NUMERIC_SCOPE, numeric_manifest, validate_attached_matrix
from scripts import verify_das_release as release


def _row(case, wall=0.1, status="available"):
    return dict(
        name=case["id"], group=case["group"], n_decks=case["decks"], status=status,
        wall_seconds=wall, result_file=case["id"] + ".json",
        engine_version=DAS_ENGINE, strategy_version=DAS_STRATEGY, profile_id=DAS_PROFILE,
    )


def _result(case, status="available", digest=None, decks=None):
    payload = {"status": status, "input": {"n_decks": decks if decks is not None else case["decks"]}}
    if digest:
        payload["backend_source_sha256"] = digest
    return payload


class TestNumericScope(unittest.TestCase):
    def test_scope_covers_frozen_solver_and_spec_not_docs(self):
        self.assertIn("blackjack_lab/analysis/native/SplitEngine.cs", NUMERIC_SCOPE)
        self.assertIn("fixtures/v02b2/das_benchmark_spec.json", NUMERIC_SCOPE)
        self.assertNotIn("scripts/verify_das_release.py", NUMERIC_SCOPE)
        self.assertNotIn("scripts/das_matrix_gate.py", NUMERIC_SCOPE)
        self.assertNotIn("blackjack_lab/analysis/external_pw.py", NUMERIC_SCOPE)
        manifest = numeric_manifest()
        for relative in NUMERIC_SCOPE:
            self.assertIn(relative, manifest)
            self.assertEqual(len(manifest[relative]), 64)


class TestAttachedMatrixGate(unittest.TestCase):
    def setUp(self):
        self.spec = bench.load_spec()
        self.numeric = numeric_manifest()
        self.engine = self.numeric["blackjack_lab/analysis/native/SplitEngine.cs"]

    def _packet(self, folder, *, rows=None, extra_receipt=None, write_results=True, digest=None):
        directory = Path(folder) / "matrix"
        directory.mkdir()
        spec = self.spec
        if rows is None:
            rows = [_row(case) for case in spec["cases"]]
        if write_results:
            for row, case in zip(rows, spec["cases"]):
                if type(row) is not dict:
                    continue
                name = row.get("result_file")
                if type(name) is str and Path(name).name == name:
                    (directory / name).write_text(
                        json.dumps(_result(case, row.get("status", "available"), digest or self.engine)),
                        encoding="utf-8")
        receipt = {
            "schema": bench.SCHEMA,
            "passed": True,
            "count": len(rows),
            "completed": len(rows),
            "failed": 0,
            "timed_out": 0,
            "p95_seconds": 0.1,
            "cases": rows,
            "source_manifest": dict(self.numeric),
            "spec_sha256": hashlib.sha256(bench.SPEC_PATH.read_bytes()).hexdigest(),
            "identity": {"commit": "synthetic", "dirty_worktree": False, "kind": "test"},
            "binary_sha256": "0" * 64,
        }
        if extra_receipt:
            receipt.update(extra_receipt)
        (directory / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
        return directory

    def test_passed_flag_without_any_cases_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder) / "matrix"
            directory.mkdir()
            (directory / "receipt.json").write_text(json.dumps({"passed": True}), encoding="utf-8")
            verdict = validate_attached_matrix(directory, spec=self.spec, current_numeric=self.numeric)
            self.assertFalse(verdict["passed"])
            self.assertTrue({"wrong_schema", "missing_cases", "missing_source_manifest", "empty_matrix"}
                            & set(verdict["errors"]))
            self.assertTrue(verdict["claimed_passed"])

    def test_stale_other_source_receipt_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = self._packet(folder, rows=[], write_results=False, extra_receipt={
                "schema": bench.SCHEMA,
                "count": 336,
                "completed": 336,
                "source_manifest": {"blackjack_lab/analysis/native/SplitEngine.cs": "different-old-source-hash"},
                "cases": [],
            })
            verdict = validate_attached_matrix(directory, spec=self.spec, current_numeric=self.numeric)
            self.assertFalse(verdict["passed"])
            self.assertIn("source_mismatch", verdict["errors"])
            self.assertIn("empty_matrix", verdict["errors"])

    def test_complete_synthetic_336_with_matching_numeric_hashes_is_accepted(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = self._packet(folder)
            verdict = validate_attached_matrix(directory, spec=self.spec, current_numeric=self.numeric)
            self.assertEqual(verdict["errors"], [], verdict["errors"])
            self.assertTrue(verdict["passed"])
            self.assertEqual(verdict["count"], 336)
            self.assertEqual(verdict["completed"], 336)
            self.assertLessEqual(verdict["p95_seconds"], 2.0)

    def test_claimed_p95_is_recomputed_from_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            rows = [_row(case, wall=0.1) for case in self.spec["cases"]]
            for row in rows[-20:]:
                row["wall_seconds"] = 9.0
            directory = self._packet(folder, rows=rows, extra_receipt={"p95_seconds": 0.2, "passed": True})
            verdict = validate_attached_matrix(directory, spec=self.spec, current_numeric=self.numeric)
            self.assertFalse(verdict["passed"])
            self.assertGreater(verdict["p95_seconds"], 2.0)
            self.assertFalse(verdict["target_met"])

    def test_missing_result_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = self._packet(folder)
            (directory / "pre-6-A-A.json").unlink()
            verdict = validate_attached_matrix(directory, spec=self.spec, current_numeric=self.numeric)
            self.assertFalse(verdict["passed"])
            self.assertIn("missing_result_file", verdict["errors"])

    def test_tampered_result_status_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = self._packet(folder)
            (directory / "pre-6-A-A.json").write_text(json.dumps({"status": "timeout", "input": {"n_decks": 6}}),
                                                      encoding="utf-8")
            verdict = validate_attached_matrix(directory, spec=self.spec, current_numeric=self.numeric)
            self.assertFalse(verdict["passed"])
            self.assertIn("result_status_mismatch", verdict["errors"])

    def test_non_finite_timing_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            rows = [_row(case) for case in self.spec["cases"]]
            rows[0]["wall_seconds"] = math.inf
            directory = self._packet(folder, rows=rows)
            verdict = validate_attached_matrix(directory, spec=self.spec, current_numeric=self.numeric)
            self.assertFalse(verdict["passed"])
            self.assertIn("invalid_timing", verdict["errors"])

    def test_result_engine_digest_must_match_current_solver(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = self._packet(folder, digest="ab" * 32)
            verdict = validate_attached_matrix(directory, spec=self.spec, current_numeric=self.numeric)
            self.assertFalse(verdict["passed"])
            self.assertIn("result_source_mismatch", verdict["errors"])


class TestVerifyDasReleaseAttachment(unittest.TestCase):
    def execute(self, matrix_receipt, extra_files=None):
        with tempfile.TemporaryDirectory(prefix="hakimi-matrix-gate-") as folder:
            root = Path(folder)
            matrix = root / "matrix"
            matrix.mkdir()
            (matrix / "receipt.json").write_text(json.dumps(matrix_receipt), encoding="utf-8")
            for name, payload in (extra_files or {}).items():
                (matrix / name).write_text(json.dumps(payload), encoding="utf-8")
            out = root / "out"
            fake = types.SimpleNamespace(returncode=0, stdout="TEST DOUBLE: Windows check not run\n")
            with patch.object(release, "ROOT", root), \
                    patch.object(release, "source_manifest", return_value={"scripts/x.py": "1"}), \
                    patch.object(release, "source_identity",
                                 return_value={"commit": "current", "dirty_worktree": False, "kind": "synthetic"}), \
                    patch.object(release.subprocess, "run", return_value=fake), \
                    patch.object(sys, "argv",
                                 ["verify_das_release.py", "--output", str(out), "--matrix-dir", str(matrix)]), \
                    contextlib.redirect_stdout(io.StringIO()):
                code = release.main()
            result = json.loads((out / "receipt.json").read_text(encoding="utf-8"))
            return code, result

    def test_failed_flag_is_rejected_control(self):
        code, result = self.execute({"passed": False})
        self.assertNotEqual(code, 0)
        self.assertFalse(result["passed"])

    def test_passed_flag_without_any_cases_is_rejected(self):
        code, result = self.execute({"passed": True})
        self.assertNotEqual(code, 0, "A passed flag without schema, counts, identity or result files was accepted")
        self.assertFalse(result["passed"])

    def test_stale_other_source_receipt_is_rejected(self):
        data = {
            "passed": True, "schema": "hakimi-das-cold-matrix-v1", "count": 336, "completed": 336,
            "failed": 0, "timed_out": 0, "p95_seconds": 1.0, "statuses": {"available": 336},
            "identity": {"commit": "wrong-old-commit"},
            "source_manifest": {"blackjack_lab/analysis/native/SplitEngine.cs": "different-old-source-hash"},
            "cases": [],
        }
        code, result = self.execute(data)
        self.assertNotEqual(code, 0, "A different source manifest with no case files was accepted")
        self.assertFalse(result["passed"])


class TestOriginal336MaterialsIfPresent(unittest.TestCase):
    def test_opt3_attachment_recomputes_against_numeric_scope(self):
        path = ROOT / ".local-evidence" / "das-cold-20260912-t9-opt3"
        if not path.is_dir() or not (path / "receipt.json").is_file():
            self.skipTest("original 336 materials are not on this machine")
        verdict = validate_attached_matrix(path)
        self.assertTrue(verdict["passed"], verdict["errors"])
        self.assertEqual(verdict["count"], 336)
        self.assertEqual(verdict["completed"], 336)
        self.assertLessEqual(verdict["p95_seconds"], 2.0)


if __name__ == "__main__":
    unittest.main()
