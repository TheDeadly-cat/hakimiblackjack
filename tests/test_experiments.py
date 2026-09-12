"""T10-A: synthetic 6/7/8-deck contrast and frozen history-prefix replay."""
import json
import tempfile
import unittest
import uuid
from pathlib import Path

from blackjack_lab.experiments.contracts import KIND_HISTORY, KIND_SYNTHETIC
from blackjack_lab.experiments.runner import ExperimentRunner
from blackjack_lab.experiments.scenarios import config_from_mapping
from blackjack_lab.ledger.events import CARD_DEALT, FACE_HIDDEN
from blackjack_lab.storage.database import LocalStore
from tests.test_analysis_integration import example


class TestExperiments(unittest.TestCase):
    def run_config(self, data, folder):
        config = config_from_mapping(data, uuid.uuid4().hex)
        return ExperimentRunner().run(config, Path(folder) / "out")

    def test_synthetic_6_7_8_enter_calculation_and_keep_full_actions(self):
        with tempfile.TemporaryDirectory() as folder:
            saved = self.run_config({
                "kind": KIND_SYNTHETIC, "n_decks": (6, 7, 8), "template": "single",
                "player_ranks": ("10", "6"), "dealer_up": "10",
            }, folder)
            items = saved["record"]["items"]
            self.assertEqual([item["n_decks"] for item in items], [6, 7, 8])
            remainings = []
            for item in items:
                self.assertEqual(item["status"], "available", item.get("reason"))
                self.assertIn("stand", item["actions"])
                self.assertIn("hit", item["actions"])
                self.assertTrue(item["not_a_round_simulation"])
                remainings.append(item["physical_remaining"])
            self.assertEqual(len(set(remainings)), 3)
            self.assertTrue((Path(folder) / "out" / "experiment.json").is_file())
            self.assertTrue((Path(folder) / "out" / "experiment.csv").is_file())
            text = (Path(folder) / "out" / "experiment.csv").read_text(encoding="utf-8")
            self.assertIn("stand", text)
            self.assertIn("hit", text)

    def test_illegal_composition_is_rejected_and_kept(self):
        with tempfile.TemporaryDirectory() as folder:
            saved = self.run_config({
                "kind": KIND_SYNTHETIC, "n_decks": (6,), "template": "single",
                "player_ranks": ("10", "6"), "dealer_up": "10",
                "extra_removed": ("A",) * 25,
            }, folder)
            item = saved["record"]["items"][0]
            self.assertEqual(item["status"], "failed")
            self.assertEqual(item["reason_code"], "ILLEGAL_COMPOSITION")
            self.assertEqual(item["actions"], {})

    def test_history_prefix_does_not_absorb_later_reveal(self):
        ledger = example(cards=("10", "6"), up="10")
        prefix_seq = ledger.events[-1].seq
        hole = next(event for event in ledger.events
                    if event.etype == CARD_DEALT and event.payload.get("face_state") == FACE_HIDDEN)
        ledger.reveal(hole.event_id, "5")
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "history.db"
            store = LocalStore(db)
            try:
                store.save_ledger(ledger)
            finally:
                store.close()
            saved = self.run_config({
                "kind": KIND_HISTORY, "n_decks": (6,), "template": "single",
                "player_ranks": ("10", "6"), "dealer_up": "10",
                "db_path": str(db), "session_id": ledger.session_id,
                "through_seq": prefix_seq,
            }, folder)
            item = saved["record"]["items"][0]
            self.assertEqual(item["status"], "available", item.get("reason"))
            self.assertEqual(item["later_event_count_ignored"], 1)
            self.assertEqual(item["through_seq"], prefix_seq)
            info = json.loads(
                Path(folder, "out", "experiment.json").read_text(encoding="utf-8"))
            self.assertEqual(info["items"][0]["through_seq"], prefix_seq)

    def test_timeout_is_retained(self):
        with tempfile.TemporaryDirectory() as folder:
            config = config_from_mapping({
                "kind": KIND_SYNTHETIC, "n_decks": (6,), "template": "das",
                "player_ranks": ("8", "8"), "dealer_up": "6",
            }, uuid.uuid4().hex)
            saved = ExperimentRunner().run(config, Path(folder) / "out", budget_seconds=0.0001)
            item = saved["record"]["items"][0]
            self.assertEqual(item["status"], "timeout", item)
            self.assertEqual(item.get("actions"), {})

    def test_cli_synthetic_and_history_write_json_csv(self):
        from scripts.run_experiment import main
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "cli-synthetic"
            code = main(["--mode", "synthetic", "--decks", "6,7,8", "--player", "10,6",
                         "--up", "10", "--output", str(out)])
            self.assertEqual(code, 0)
            record = json.loads((out / "experiment.json").read_text(encoding="utf-8"))
            self.assertEqual([item["n_decks"] for item in record["items"]], [6, 7, 8])
            self.assertTrue(all(item["status"] == "available" for item in record["items"]))
            self.assertTrue((out / "experiment.csv").is_file())
            self.assertTrue(record["config"]["not_a_round_simulation"])

            ledger = example(cards=("10", "6"), up="10")
            prefix_seq = ledger.events[-1].seq
            hole = next(event for event in ledger.events
                        if event.etype == CARD_DEALT and event.payload.get("face_state") == FACE_HIDDEN)
            ledger.reveal(hole.event_id, "5")
            db = Path(folder) / "history.db"
            store = LocalStore(db)
            try:
                store.save_ledger(ledger)
            finally:
                store.close()
            hist = Path(folder) / "cli-history"
            code = main(["--mode", "history", "--db", str(db), "--session", ledger.session_id,
                         "--through-seq", str(prefix_seq), "--output", str(hist)])
            self.assertEqual(code, 0)
            replay = json.loads((hist / "experiment.json").read_text(encoding="utf-8"))
            self.assertEqual(replay["items"][0]["status"], "available")
            self.assertEqual(replay["items"][0]["later_event_count_ignored"], 1)
            self.assertEqual(replay["config"]["kind"], KIND_HISTORY)


if __name__ == "__main__":
    unittest.main()
