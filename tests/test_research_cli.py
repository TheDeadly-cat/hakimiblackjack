"""Research CLIs must declare surrender; omitting the flag is not late surrender."""
import contextlib
import importlib
import io
import json
import unittest


RESEARCH_CLIS = (
    ("scripts.run_shoe_windows", ["--kind", "full-reshuffle"]),
    ("scripts.run_round_windows", []),
    ("scripts.run_independent_shoes", []),
    ("scripts.run_policy_contrast", []),
    ("scripts.run_observation_error", []),
    ("scripts.run_unused_holdout", ["missing-package.json"]),
    ("scripts.run_fixed_policy_mc", ["--samples", "1"]),
    ("scripts.run_composition_interval", []),
)


class ResearchCliSurrenderTest(unittest.TestCase):
    def test_omitted_surrender_flag_exits(self):
        for modname, extra in RESEARCH_CLIS:
            with self.subTest(modname):
                module = importlib.import_module(modname)
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    with self.assertRaises(SystemExit) as caught:
                        module.main(list(extra))
                code = caught.exception.code
                self.assertNotEqual(0, code)
                self.assertIn("--surrender", stderr.getvalue())

    def test_composition_cli_none_refuses_mean_shoe_without_late_fill(self):
        from scripts.run_composition_interval import main
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            main(["--surrender", "none"])
        text = stdout.getvalue()
        payload, _, _ = text.rpartition("\nev_min")
        report = json.loads(payload)
        self.assertTrue(report["forbids_mean_shoe"])
        self.assertEqual("MEAN_SHOE_FORBIDDEN", report["reason_code"])
        self.assertNotIn("surrender", report.get("legal_actions") or [])
        self.assertNotIn("late", text.lower())


if __name__ == "__main__":
    unittest.main()
