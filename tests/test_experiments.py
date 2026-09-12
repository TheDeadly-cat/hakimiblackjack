"""T10-A: synthetic 6/7/8-deck contrast and frozen history-prefix replay."""
import json
import tempfile
import unittest
import uuid
from pathlib import Path

from blackjack_lab.experiments.contracts import KIND_HISTORY, KIND_SYNTHETIC, ExperimentError
from blackjack_lab.experiments.runner import ExperimentRunner
from blackjack_lab.experiments.scenarios import apply_fixed_removals, config_from_mapping, snapshot_for_deck
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

    def test_twenty_five_kings_on_six_decks_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            saved = self.run_config({
                "kind": KIND_SYNTHETIC, "n_decks": (6,), "template": "single",
                "player_ranks": ("10", "6"), "dealer_up": "10",
                "extra_removed": ("K",) * 25,
            }, folder)
            item = saved["record"]["items"][0]
            self.assertEqual(item["status"], "failed")
            self.assertEqual(item["reason_code"], "ILLEGAL_COMPOSITION")

    def test_player_king_plus_twenty_four_kings_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            saved = self.run_config({
                "kind": KIND_SYNTHETIC, "n_decks": (6,), "template": "single",
                "player_ranks": ("K", "6"), "dealer_up": "10",
                "extra_removed": ("K",) * 24,
            }, folder)
            item = saved["record"]["items"][0]
            self.assertEqual(item["status"], "failed")
            self.assertEqual(item["reason_code"], "ILLEGAL_COMPOSITION")

    def test_twenty_five_unspecified_tens_on_six_decks_remain_legal(self):
        with tempfile.TemporaryDirectory() as folder:
            saved = self.run_config({
                "kind": KIND_SYNTHETIC, "n_decks": (6,), "template": "single",
                "player_ranks": ("10", "6"), "dealer_up": "10",
                "extra_removed": ("T",) * 25,
            }, folder)
            item = saved["record"]["items"][0]
            self.assertEqual(item["status"], "available", item.get("reason"))
            self.assertEqual(item["n_decks"], 6)

    def test_twenty_five_kings_on_seven_and_eight_decks_remain_legal(self):
        for decks in (7, 8):
            with tempfile.TemporaryDirectory() as folder:
                saved = self.run_config({
                    "kind": KIND_SYNTHETIC, "n_decks": (decks,), "template": "single",
                    "player_ranks": ("10", "6"), "dealer_up": "10",
                    "extra_removed": ("K",) * 25,
                }, folder)
                item = saved["record"]["items"][0]
                self.assertEqual(item["status"], "available", item.get("reason"))
                self.assertEqual(item["n_decks"], decks)

    def test_twenty_nine_kings_on_seven_decks_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            saved = self.run_config({
                "kind": KIND_SYNTHETIC, "n_decks": (7,), "template": "single",
                "player_ranks": ("10", "6"), "dealer_up": "10",
                "extra_removed": ("K",) * 29,
            }, folder)
            item = saved["record"]["items"][0]
            self.assertEqual(item["status"], "failed")
            self.assertEqual(item["reason_code"], "ILLEGAL_COMPOSITION")

    def test_dealer_king_plus_twenty_four_kings_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            saved = self.run_config({
                "kind": KIND_SYNTHETIC, "n_decks": (6,), "template": "single",
                "player_ranks": ("10", "6"), "dealer_up": "K",
                "extra_removed": ("K",) * 24,
            }, folder)
            item = saved["record"]["items"][0]
            self.assertEqual(item["status"], "failed")
            self.assertEqual(item["reason_code"], "ILLEGAL_COMPOSITION")

    def test_twenty_four_kings_without_other_kings_remain_legal(self):
        with tempfile.TemporaryDirectory() as folder:
            saved = self.run_config({
                "kind": KIND_SYNTHETIC, "n_decks": (6,), "template": "single",
                "player_ranks": ("10", "6"), "dealer_up": "10",
                "extra_removed": ("K",) * 24,
            }, folder)
            item = saved["record"]["items"][0]
            self.assertEqual(item["status"], "available", item.get("reason"))

    def test_apply_fixed_removals_checks_original_kings_without_extra_context(self):
        config = config_from_mapping({
            "kind": KIND_SYNTHETIC, "n_decks": (6,), "template": "single",
            "player_ranks": ("10", "6"), "dealer_up": "10",
        }, "removal-only")
        snapshot, _ledger = snapshot_for_deck(config, 6)
        with self.assertRaises(ExperimentError) as error:
            apply_fixed_removals(snapshot, ("K",) * 25)
        self.assertEqual(error.exception.code, "ILLEGAL_COMPOSITION")
        kept = apply_fixed_removals(snapshot, ("K",) * 24)
        kept.validate()
        self.assertEqual(kept.counts[9], snapshot.counts[9] - 24)

    def test_false_peek_text_is_not_coerced_true(self):
        config = config_from_mapping({
            "kind": KIND_SYNTHETIC, "n_decks": (6,), "template": "single",
            "player_ranks": ("10", "6"), "dealer_up": "9",
            "peek_negative": "false",
        }, "peek-false")
        self.assertIs(config.peek_negative, False)

    def test_fractional_decks_are_rejected(self):
        with self.assertRaises(ExperimentError) as error:
            config_from_mapping({
                "kind": KIND_SYNTHETIC, "n_decks": [6.9], "template": "single",
                "player_ranks": ("10", "6"), "dealer_up": "10",
            }, "bad-decks")
        self.assertEqual(error.exception.code, "ILLEGAL_TYPE")

    def test_repeat_output_keeps_the_first_experiment(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "shared"
            first = ExperimentRunner().run(config_from_mapping({
                "kind": KIND_SYNTHETIC, "n_decks": (6,), "template": "single",
                "player_ranks": ("10", "6"), "dealer_up": "10",
                "extra_removed": ("A",) * 25,
            }, "first-run"), out)
            first_id = first["record"]["config"]["experiment_id"]
            with self.assertRaises(ExperimentError) as error:
                ExperimentRunner().run(config_from_mapping({
                    "kind": KIND_SYNTHETIC, "n_decks": (6,), "template": "single",
                    "player_ranks": ("9", "7"), "dealer_up": "5",
                }, "second-run"), out)
            self.assertEqual(error.exception.code, "OUTPUT_EXISTS")
            saved = json.loads((out / "experiment.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["config"]["experiment_id"], first_id)
            self.assertEqual(saved["items"][0]["reason_code"], "ILLEGAL_COMPOSITION")

    def test_cancel_keeps_finished_and_stops_later_decks(self):
        runner = ExperimentRunner()
        original = runner._calculate

        def once(*args, **kwargs):
            item = original(*args, **kwargs)
            runner.cancel()
            return item

        runner._calculate = once
        with tempfile.TemporaryDirectory() as folder:
            config = config_from_mapping({
                "kind": KIND_SYNTHETIC, "n_decks": (6, 7, 8), "template": "single",
                "player_ranks": ("10", "6"), "dealer_up": "10",
            }, uuid.uuid4().hex)
            saved = runner.run(config, Path(folder) / "out")
            items = saved["record"]["items"]
            self.assertEqual(items[0]["status"], "available", items[0].get("reason"))
            self.assertEqual(items[0]["n_decks"], 6)
            self.assertTrue(all(item["status"] == "cancelled" for item in items[1:]))
            self.assertEqual([item["n_decks"] for item in items], [6, 7, 8])

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

    def test_cli_defaults_do_not_erase_config_file(self):
        from scripts.run_experiment import main
        with tempfile.TemporaryDirectory() as folder:
            config_path = Path(folder) / "cfg.json"
            config_path.write_text(json.dumps({
                "n_decks": [6],
                "template": "single",
                "player_ranks": ["9", "7"],
                "dealer_up": "5",
            }), encoding="utf-8")
            out = Path(folder) / "from-config"
            code = main(["--mode", "synthetic", "--config", str(config_path),
                         "--output", str(out)])
            self.assertEqual(code, 0)
            record = json.loads((out / "experiment.json").read_text(encoding="utf-8"))
            self.assertEqual(record["config"]["n_decks"], [6])
            self.assertEqual(record["config"]["player_ranks"], ["9", "7"])
            self.assertEqual(record["config"]["dealer_up"], "5")
            override = Path(folder) / "from-flag"
            code = main(["--mode", "synthetic", "--config", str(config_path),
                         "--player", "8,8", "--output", str(override)])
            self.assertEqual(code, 0)
            changed = json.loads((override / "experiment.json").read_text(encoding="utf-8"))
            self.assertEqual(changed["config"]["player_ranks"], ["8", "8"])
            self.assertEqual(changed["config"]["dealer_up"], "5")


if __name__ == "__main__":
    unittest.main()
