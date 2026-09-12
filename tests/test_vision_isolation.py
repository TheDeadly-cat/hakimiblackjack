# -*- coding: utf-8 -*-
"""识牌不得在导入时拖入 OpenCV，也不得依赖账本/求解器。"""
import ast
import subprocess
import sys
import unittest
from pathlib import Path

VISION = Path(__file__).resolve().parents[1] / "blackjack_lab" / "vision"
FORBIDDEN_IMPORTS = {
    "blackjack_lab.ledger",
    "blackjack_lab.ui",
    "blackjack_lab.storage",
    "blackjack_lab.analysis",
    "blackjack_lab.simulator",
    "blackjack_lab.capture",
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level >= 2:
                found.add("blackjack_lab")
            if node.module:
                prefix = "blackjack_lab.vision" if node.level == 1 else ""
                if node.level >= 2:
                    found.add(f"blackjack_lab.{node.module}" if node.module else "blackjack_lab")
                elif node.level == 1:
                    found.add(f"blackjack_lab.vision.{node.module}")
                else:
                    found.add(node.module)
    return found


class TestVisionIsolation(unittest.TestCase):
    def test_source_does_not_import_ledger_or_solver(self):
        for path in VISION.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("from ..ledger", text)
            self.assertNotIn("from ..ui", text)
            self.assertNotIn("from ..storage", text)
            self.assertNotIn("from ..analysis", text)
            self.assertNotIn("SplitEngine", text)
            self.assertNotIn("deal_shown", text)

    def test_package_import_without_cv2(self):
        code = (
            "import blackjack_lab.vision as v, sys; "
            "assert 'cv2' not in sys.modules; "
            "assert v.REVIEW_PENDING == '图像待核对'"
        )
        subprocess.check_call([sys.executable, "-c", code], cwd=str(VISION.parents[1]))

    def test_main_check_without_loading_vision_cv2(self):
        proc = subprocess.run(
            [sys.executable, "-m", "blackjack_lab.main", "--check"],
            cwd=str(VISION.parents[1]),
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("自检通过", proc.stdout)


if __name__ == "__main__":
    unittest.main()
