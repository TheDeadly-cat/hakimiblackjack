"""Contract tests for the possibly-wrong adapter. Does not run strategy.exe."""
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from blackjack_lab.analysis.external_pw import (
    hakimi_dealer_counts_from_pw_shoe, hakimi_remaining_from_pw_shoe,
    load_spec, max_abs_dealer_error, parse_dealer_table, parse_ev_blocks, peek_negative,
    pw_shoe_from_hakimi_remaining, pw_stdin, renormalize_pw_dealer_after_peek,
    verify_strategy_exe, full_shoe,
)

SPEC = ROOT / "fixtures" / "v02b2" / "external_pw_cases.json"
SAMPLE = ROOT / "fixtures" / "v02b2" / "external_pw_parser_sample.txt"


class TestExternalPwSpec(unittest.TestCase):
    def test_frozen_spec_expands_to_declared_counts(self):
        spec = load_spec(SPEC)
        self.assertEqual(spec["_load_errors"], [])
        self.assertEqual(spec["engine"]["release"], "v7.6")
        self.assertEqual(spec["engine"]["source_commit"], "a1f7dbb74266fb39296292bdff568b076120a61c")
        self.assertEqual(spec["engine"]["not_vendored"], True)
        self.assertEqual(spec["engine"]["license"], "GPL-3.0-or-later")
        self.assertFalse(spec["rules"]["hit_soft_17"])
        self.assertTrue(spec["rules"]["double_any_total"])
        self.assertFalse(spec["rules"]["double_after_hit"])
        self.assertFalse(spec["rules"]["double_after_split"])
        self.assertFalse(spec["rules"]["cdz"])
        cases = spec["_cases"]
        self.assertEqual(len(cases), 66)
        self.assertEqual(len({case["id"] for case in cases}), 66)
        self.assertEqual(sum(case["group"] == "dealer_raw" for case in cases), 20)
        self.assertEqual(sum(case["group"] == "dealer_peek" for case in cases), 10)
        self.assertEqual(sum(case["group"] == "ev" for case in cases), 36)
        split_cases = [case for case in cases if "split" in case.get("incomparable_actions", [])]
        self.assertEqual(len(split_cases), 3)
        for case in split_cases:
            self.assertEqual(case["incomparable_reason"], "split_cdz_cdp_vs_sequential_two_hand_total_net")
            self.assertEqual(case["actions"]["stand"], "comparable")
        three = [case for case in cases if case["id"].startswith("ev-") and case["id"].endswith("3c16-6")]
        self.assertEqual(len(three), 3)
        self.assertEqual(three[0]["player"], (10, 2, 4))
        self.assertNotIn("double", three[0]["actions"])


class TestExternalPwAdapter(unittest.TestCase):
    def test_player_and_upcard_return_to_the_pw_shoe(self):
        shoe = full_shoe(6)
        player, up = (10, 6), 10
        remaining = hakimi_remaining_from_pw_shoe(shoe, player, up)
        self.assertEqual(remaining[9], 94)
        self.assertEqual(remaining[5], 23)
        self.assertEqual(pw_shoe_from_hakimi_remaining(remaining, player, up), shoe)
        dealer_counts = hakimi_dealer_counts_from_pw_shoe(shoe, up)
        self.assertEqual(dealer_counts[9], 95)
        self.assertEqual(sum(dealer_counts), 6 * 52 - 1)

    def test_empty_player_only_removes_upcard(self):
        shoe = [16, 24, 24, 24, 24, 24, 24, 24, 24, 96]
        counts = hakimi_dealer_counts_from_pw_shoe(shoe, 1)
        self.assertEqual(counts[0], 15)
        self.assertEqual(counts[9], 96)

    def test_peek_only_for_ace_and_ten(self):
        self.assertTrue(peek_negative(1))
        self.assertTrue(peek_negative(10))
        self.assertFalse(peek_negative(6))

    def test_renormalize_peek_drops_blackjack_mass(self):
        raw = {"blackjack": 0.2, "17": 0.1, "18": 0.1, "19": 0.1, "20": 0.1, "21": 0.1, "bust": 0.3}
        peeked = renormalize_pw_dealer_after_peek(raw)
        self.assertEqual(peeked["blackjack"], 0.0)
        self.assertAlmostEqual(peeked["bust"], 0.375)
        self.assertAlmostEqual(sum(peeked.values()), 1.0)
        self.assertAlmostEqual(max_abs_dealer_error(peeked, peeked), 0.0)

    def test_missing_cards_are_rejected(self):
        with self.assertRaises(ValueError):
            hakimi_remaining_from_pw_shoe([0] * 10, (10, 6), 10)


class TestExternalPwParser(unittest.TestCase):
    def test_sample_table_and_optional_double_split_lines(self):
        text = SAMPLE.read_text(encoding="utf-8")
        rows = parse_dealer_table(text)
        self.assertEqual(set(rows), {2, 6, 10, 1})
        self.assertEqual(rows[6]["bust"], 0.42082)
        self.assertEqual(rows[10]["blackjack"], 0.07843)
        self.assertEqual(rows[1]["blackjack"], 0.31373)
        blocks = parse_ev_blocks(text)
        self.assertEqual(len(blocks), 3)
        self.assertAlmostEqual(blocks[0]["stand"], -0.54295185382)
        self.assertAlmostEqual(blocks[0]["double"], -1.01385848516)
        self.assertNotIn("double", blocks[1])
        self.assertNotIn("split", blocks[1])
        self.assertAlmostEqual(blocks[2]["split"], 0.23371584494)


class TestExternalPwStdin(unittest.TestCase):
    def test_full_shoe_and_custom_shoe_prompts(self):
        spec = load_spec(SPEC)
        six = next(item for item in spec["shoes"] if item["id"] == "full-6")
        text = pw_stdin(spec, six, ((10, (10, 6)), (6, (10, 2, 4))))
        lines = text.splitlines()
        self.assertEqual(lines[0], "6")
        self.assertEqual(lines[1:8], ["n", "y", "y", "n", "n", "n", "n"])
        self.assertEqual(lines[8:12], ["y", "y", "1.5", "pw-table.txt"])
        self.assertEqual(lines[12], "10 2 10 6")
        self.assertEqual(lines[13], "6 3 10 2 4")
        self.assertEqual(lines[-1], "0 0")
        depleted = next(item for item in spec["shoes"] if item["id"] == "dep-6-t16")
        custom = pw_stdin(spec, depleted, ((10, (10, 6)),)).splitlines()
        self.assertEqual(custom[0], "0")
        self.assertEqual(custom[1], "24 24 24 24 24 24 24 24 24 80")


class TestExternalPwExePin(unittest.TestCase):
    def test_wrong_bytes_are_rejected(self):
        spec = load_spec(SPEC)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "strategy.exe"
            path.write_bytes(b"not-the-pinned-binary")
            with self.assertRaises(ValueError):
                verify_strategy_exe(path, spec)


if __name__ == "__main__":
    unittest.main()
