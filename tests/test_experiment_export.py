"""Full shoe-window archives must keep every checkpoint, not a stripped stdout dump."""
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path

from blackjack_lab.analysis.experiment_export import (
    checkpoint_row, checkpoint_row_from_csv, checkpoint_row_to_csv,
    summary_from_checkpoint_rows, write_window_study, _scope_record,
)
from blackjack_lab.analysis.shoe_windows import (
    EVALUATION_FIXED_POLICY_MC, KIND_FULL_DEPLETE, KIND_FULL_RESHUFFLE,
    CONSUMPTION_STAND, run_window_study,
)
from blackjack_lab.experiments.contracts import ExperimentError


class WindowStudyExportTest(unittest.TestCase):
    def test_export_keeps_rounds_and_recomputes_summary(self):
        report = run_window_study(
            kind=KIND_FULL_RESHUFFLE, n_decks=6, seed=3, max_rounds=2, surrender=None,
            evaluation_method=EVALUATION_FIXED_POLICY_MC,
            evaluation_policy_id=CONSUMPTION_STAND, mc_n_samples=8)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        written = write_window_study(report, Path(tmp.name) / "run-1")
        study = json.loads(Path(written["study"]).read_text(encoding="utf-8"))
        self.assertIn("rounds", study)
        self.assertEqual(2, len(study["rounds"]))
        self.assertEqual(2, written["round_count"])
        rows = [
            json.loads(line) for line in Path(written["checkpoints"]).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(2, len(rows))
        for row in rows:
            self.assertEqual(312, row["physical_remaining"])
            self.assertEqual("fixed_composition", row["input_scope"])
            self.assertEqual(CONSUMPTION_STAND, row["evaluation_policy_id"])
            self.assertIsNotNone(row["status"])
        recomputed = json.loads(Path(written["summary"]).read_text(encoding="utf-8"))
        self.assertTrue(recomputed["recomputed_from_checkpoint_rows"])
        self.assertEqual(2, recomputed["round_count"])
        self.assertEqual(summary_from_checkpoint_rows(rows, report=study)["positive_ev"],
                         recomputed["positive_ev"])
        identity = json.loads(Path(written["run_identity"]).read_text(encoding="utf-8"))
        self.assertEqual(KIND_FULL_RESHUFFLE, identity["kind"])
        self.assertEqual(CONSUMPTION_STAND, identity["evaluation_policy_id"])
        self.assertTrue(identity["not_a_reliable_window_claim"])
        self.assertTrue((Path(written["checkpoints_csv"])).is_file())
        with self.assertRaises(ExperimentError):
            write_window_study(report, Path(tmp.name) / "run-1")

    def test_deplete_export_records_cut_and_unevaluated_reason(self):
        report = run_window_study(
            kind=KIND_FULL_DEPLETE, n_decks=6, seed=1, max_rounds=2, surrender=None,
            evaluation_method=EVALUATION_FIXED_POLICY_MC,
            evaluation_policy_id=CONSUMPTION_STAND, mc_n_samples=4)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        written = write_window_study(report, Path(tmp.name) / "deplete")
        study = json.loads(Path(written["study"]).read_text(encoding="utf-8"))
        self.assertEqual(52, study["cut_remaining"])
        self.assertNotEqual("moved-to-tail", study.get("cut_source"))
        summary = json.loads(Path(written["summary"]).read_text(encoding="utf-8"))
        self.assertEqual(len(study["rounds"]), summary["round_count"])
        self.assertEqual(study["stop_reason"], summary["stop_reason"])

    def test_summary_does_not_upgrade_uncertain_or_current_hand_to_opening_positive(self):
        from blackjack_lab.analysis.experiment_export import (
            checkpoint_row, summary_from_checkpoint_rows,
        )
        from blackjack_lab.analysis.research_windows import WINDOW_CURRENT_HAND, WINDOW_PRE_DEAL
        tiny = checkpoint_row({
            "predeal": {
                "status": "available", "ev": 1e-16, "numerical_tolerance": 1e-10,
                "window_kind": WINDOW_PRE_DEAL, "method": "fixed_policy_monte_carlo",
                "window_state": "indeterminate",
            }})
        flagged = checkpoint_row({
            "predeal": {
                "status": "available", "ev": 1.0, "indeterminate": True,
                "window_kind": WINDOW_PRE_DEAL, "method": "fixed_policy_monte_carlo",
            }})
        current_hand = checkpoint_row({
            "predeal": {
                "status": "available", "ev": 0.7,
                "window": WINDOW_CURRENT_HAND, "window_kind": WINDOW_CURRENT_HAND,
                "method": "exact-enumeration",
            }})
        missing_kind = checkpoint_row({
            "predeal": {"status": "available", "ev": 0.4}})
        summary = summary_from_checkpoint_rows([tiny, flagged, current_hand, missing_kind])
        self.assertEqual(0, summary["positive_ev"])
        self.assertGreaterEqual(summary["indeterminate_ev"] + summary["unavailable_ev"], 4)


class CheckpointCsvRoundtripTest(unittest.TestCase):
    def roundtrip(self, record):
        row = checkpoint_row({"round_index": 2, "predeal": record})
        stream = io.StringIO()
        writer = csv.DictWriter(stream, fieldnames=list(row.keys()))
        writer.writeheader()
        writer.writerow(checkpoint_row_to_csv(row))
        raw = next(csv.DictReader(io.StringIO(stream.getvalue())))
        loaded = checkpoint_row_from_csv(raw)
        self.assertEqual(row, loaded)
        self.assertEqual(_scope_record(row), _scope_record(loaded))
        self.assertEqual(_scope_record(row), _scope_record(raw))
        return row, raw, loaded

    def test_nested_json_and_types(self):
        self.roundtrip({
            "window_kind": "pre_deal", "method": "exact_small", "status": "available",
            "ev": 0.02, "statistical_positive": True, "window_claim_allowed": True,
            "physical_remaining": 64, "counts": [4] * 9 + [28],
            "n_ok": 10, "n_failed": 0, "n_not_run": 0, "family_size": 4,
        })

    def test_near_zero_both_sides(self):
        for ev in (1e-16, -1e-16):
            self.roundtrip({
                "window_kind": "pre_deal", "method": "exact_small",
                "status": "available", "ev": ev, "numerical_tolerance": 1e-10,
            })

    def test_explicit_indeterminate(self):
        self.roundtrip({
            "window_kind": "pre_deal", "method": "exact_small",
            "status": "available", "ev": 0.1, "indeterminate": True,
        })

    def test_false_claim_flag_preserved(self):
        self.roundtrip({
            "window_kind": "pre_deal", "method": "fixed_policy_monte_carlo",
            "status": "available", "ev": 0.1, "window_claim_allowed": False,
        })

    def test_current_hand_not_retyped(self):
        row, _, _ = self.roundtrip({
            "window_kind": "current_hand", "method": "solver",
            "status": "available", "ev": 0.5,
        })
        self.assertEqual("current_hand", _scope_record(row)["window_kind"])

    def test_missing_window_not_invented(self):
        row, _, _ = self.roundtrip({"method": "exact_small", "status": "available", "ev": 0.5})
        self.assertTrue(_scope_record(row)["legacy_missing_window_kind"])

    def test_unicode_commas_newlines_quotes(self):
        self.roundtrip({
            "window_kind": "pre_deal", "method": "exact_small", "status": "failed",
            "reason_code": '错误, "说明"\n下一行', "ev": None,
        })

    def test_legacy_python_repr_rejected(self):
        with self.assertRaises(ValueError):
            _scope_record({"classification": "{'ev': 1, 'indeterminate': True}"})

    def test_nonobject_and_nonfinite_json_rejected(self):
        for value in ("[]", "null", '{"ev":NaN}'):
            with self.assertRaises(ValueError):
                _scope_record({"classification": value})

    def test_nonfinite_numeric_rejected(self):
        for value in ("nan", "inf", "-inf"):
            with self.assertRaises(ValueError):
                checkpoint_row_from_csv({"ev": value})

    def test_invalid_boolean_rejected(self):
        with self.assertRaises(ValueError):
            checkpoint_row_from_csv({"window_claim_allowed": "yes"})

    def test_exported_csv_roundtrips_with_study_json(self):
        from blackjack_lab.analysis.shoe_windows import (
            EVALUATION_FIXED_POLICY_MC, KIND_FULL_RESHUFFLE, CONSUMPTION_STAND,
            run_window_study,
        )
        report = run_window_study(
            kind=KIND_FULL_RESHUFFLE, n_decks=6, seed=3, max_rounds=2, surrender=None,
            evaluation_method=EVALUATION_FIXED_POLICY_MC,
            evaluation_policy_id=CONSUMPTION_STAND, mc_n_samples=8)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        written = write_window_study(report, Path(tmp.name) / "csv-roundtrip")
        json_rows = [
            json.loads(line)
            for line in Path(written["checkpoints"]).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        with Path(written["checkpoints_csv"]).open(encoding="utf-8", newline="") as handle:
            csv_rows = [checkpoint_row_from_csv(row) for row in csv.DictReader(handle)]
        self.assertEqual(json_rows, csv_rows)
        self.assertEqual(
            summary_from_checkpoint_rows(json_rows, report=report)["indeterminate_ev"],
            summary_from_checkpoint_rows(csv_rows, report=report)["indeterminate_ev"])
        for row in csv_rows:
            self.assertIsInstance(row["classification"], dict)
            self.assertIsInstance(row["composition_counts"], list)
