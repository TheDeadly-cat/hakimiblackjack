"""Portable runtime copy excludes user data and can be identity-checked without git."""
import tempfile
import unittest
from pathlib import Path

from scripts.make_portable_copy import main
from scripts.source_identity import source_identity


class TestPortableCopy(unittest.TestCase):
    def test_copy_has_no_user_database_and_validates_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            dest = Path(folder) / "runtime"
            self.assertEqual(main(["--output", str(dest)]), 0)
            self.assertTrue((dest / "BUILD_INFO.json").is_file())
            self.assertTrue((dest / "启动界面.bat").is_file())
            self.assertTrue((dest / "blackjack_lab" / "main.py").is_file())
            self.assertTrue((dest / "fixtures" / "v02b2" / "das-eight-before.json").is_file())
            self.assertFalse((dest / "data").exists())
            self.assertEqual(list(dest.rglob("*.db")), [])
            packed = source_identity(dest)
            self.assertEqual(packed["kind"], "portable-package-manifest")
            self.assertFalse(packed["dirty_worktree"])
            with self.assertRaises(SystemExit):
                main(["--output", str(dest)])

    def test_require_clean_refuses_dirty_worktree(self):
        identity = source_identity(Path(__file__).resolve().parents[1])
        if not identity.get("dirty_worktree"):
            self.skipTest("worktree is already clean")
        with tempfile.TemporaryDirectory() as folder:
            dest = Path(folder) / "runtime"
            with self.assertRaises(SystemExit):
                main(["--output", str(dest), "--require-clean"])
            self.assertFalse(dest.exists())
