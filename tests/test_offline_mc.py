"""Confirmed ledger remaining can feed offline MC without the 16-card exact cap."""
import tempfile
import unittest
from pathlib import Path

from blackjack_lab.analysis.contracts import research_rules
from blackjack_lab.analysis.fixed_policy_mc import POLICY_ALWAYS_STAND
from blackjack_lab.analysis.offline_mc_contracts import OFFLINE_MC_RESULT_SCHEMA
from blackjack_lab.analysis.research_windows import build_offline_mc_input, build_predeal_input
from blackjack_lab.analysis.service import calculate
from blackjack_lab.storage.analysis_snapshots import AnalysisSnapshots
from blackjack_lab.ui.controller import SessionController


class LedgerOfflineMcTest(unittest.TestCase):
    def test_full_shoe_exact_stays_capped_offline_mc_runs(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ctrl = SessionController(Path(tmp.name) / "offline.db")
        self.addCleanup(ctrl.close)
        ctrl.new_shoe(research_rules(6, surrender=None))
        with self.assertRaises(Exception) as caught:
            build_predeal_input(ctrl.ledger)
        self.assertEqual("PREDEAL_SHOE_TOO_LARGE", caught.exception.code)
        snapshot = build_offline_mc_input(
            ctrl.ledger, policy=POLICY_ALWAYS_STAND, n_samples=16, seed=7)
        self.assertEqual(312, snapshot.physical_remaining)
        self.assertEqual(312, len(snapshot.pack))
        self.assertEqual(sum(snapshot.counts), 312)
        self.assertEqual("ledger-prefix", __import__("json").loads(snapshot.information_json)["source"])
        result = calculate(snapshot, budget_seconds=15)
        self.assertEqual(OFFLINE_MC_RESULT_SCHEMA, result["schema"])
        self.assertEqual("available", result["status"])
        self.assertIsNotNone(result["ev"])
        self.assertEqual("hoeffding_fixed_n_finite_family_v1", result["ci_method"])
        self.assertFalse(result["timely"])
        self.assertTrue(result["not_exact_optimal"])
        self.assertTrue(result["not_a_reliable_window_claim"])
        self.assertEqual(snapshot.prefix_digest, result["ledger_prefix_digest"])
        self.assertEqual("ledger-prefix", result["source_mode"])
        store = AnalysisSnapshots(Path(tmp.name) / "snaps")
        saved = store.save(result)
        loaded = store.load(saved["snapshot_id"])
        self.assertEqual(result["input_digest"], loaded["result"]["input_digest"])
        again = ctrl.recompute_input(saved)
        self.assertEqual(snapshot.input_digest, again.input_digest)

    def test_unknown_cards_are_not_averaged_into_a_pack(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ctrl = SessionController(Path(tmp.name) / "unknown.db")
        self.addCleanup(ctrl.close)
        ctrl.new_shoe(research_rules(6, surrender=None))
        ctrl.start_round(["玩家1"])
        ctrl.deal_unknown("庄家")
        with self.assertRaises(Exception) as exact:
            build_predeal_input(ctrl.ledger)
        with self.assertRaises(Exception) as offline:
            build_offline_mc_input(
                ctrl.ledger, policy=POLICY_ALWAYS_STAND, n_samples=8, seed=1)
        self.assertIn(exact.exception.code, ("ROUND_ALREADY_DEALT", "COMPOSITION_UNKNOWN", "RECORD_GAP"))
        self.assertEqual(exact.exception.code, offline.exception.code)
