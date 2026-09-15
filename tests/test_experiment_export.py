"""Full shoe-window archives must keep every checkpoint, not a stripped stdout dump."""
import json
import tempfile
import unittest
from pathlib import Path

from blackjack_lab.analysis.experiment_export import (
    summary_from_checkpoint_rows, write_window_study,
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
