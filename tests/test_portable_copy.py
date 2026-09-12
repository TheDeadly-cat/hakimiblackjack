"""Portable copies come from the git file list and a package-wide manifest."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import make_portable_copy as pack
from scripts.source_identity import source_identity


class TestPortableCopyRealTree(unittest.TestCase):
    def test_copy_has_no_user_database_and_validates_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            dest = Path(folder) / "runtime"
            self.assertEqual(pack.main(["--output", str(dest)]), 0)
            self.assertTrue((dest / "BUILD_INFO.json").is_file())
            self.assertTrue((dest / "启动界面.bat").is_file())
            self.assertTrue((dest / "blackjack_lab" / "main.py").is_file())
            self.assertTrue((dest / "fixtures" / "v02b2" / "das-eight-before.json").is_file())
            self.assertFalse((dest / "data").exists())
            self.assertEqual(list(dest.rglob("*.db")), [])
            self.assertEqual(list(dest.rglob(".env")), [])
            info = json.loads((dest / "BUILD_INFO.json").read_text(encoding="utf-8"))
            self.assertIn("package_manifest", info)
            self.assertIn("fixtures/v02b2/das-eight-before.json", info["package_manifest"])
            packed = source_identity(dest)
            self.assertEqual(packed["kind"], "portable-package-manifest")
            self.assertFalse(packed["dirty_worktree"])
            with self.assertRaises(SystemExit):
                pack.main(["--output", str(dest)])


class PortableCopyBoundary(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hakimi-package-")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.root = self.folder / "repo"
        self.root.mkdir()
        (self.root / "blackjack_lab").mkdir()
        (self.root / "docs").mkdir()
        (self.root / "fixtures").mkdir()
        (self.root / "blackjack_lab" / "main.py").write_text(
            'print("synthetic-code")\n', encoding="utf-8", newline="\n")
        (self.root / "fixtures" / "demo.json").write_text(
            '{"kind":"synthetic-demo"}\n', encoding="utf-8", newline="\n")
        (self.root / ".gitignore").write_text(
            "data/\n*.db\n*.db-*\n*.sqlite\n*.sqlite3\n*.bak\n.env\n.env.*\n",
            encoding="utf-8", newline="\n")
        for name in pack.TOP_FILES:
            (self.root / name).write_text("synthetic documentation\n", encoding="utf-8", newline="\n")
        self.git("init", "-q")
        self.git("config", "core.autocrlf", "false")
        self.git("add", ".")
        self.git("-c", "user.name=ReviewFixture", "-c", "user.email=review@example.invalid",
                 "commit", "-qm", "synthetic fixture only")

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.root, stderr=subprocess.STDOUT, text=True)

    def committed_bytes(self, posix):
        return subprocess.check_output(["git", "show", "HEAD:" + posix], cwd=self.root)

    def execute(self, require_clean=True, expect_clean=True):
        if expect_clean:
            self.assertEqual(self.git("status", "--porcelain"), "")
        out = self.folder / "runtime"
        if out.exists():
            raise AssertionError("runtime destination already exists")
        args = ["--output", str(out)]
        if require_clean:
            args.append("--require-clean")
        with patch.object(pack, "ROOT", self.root):
            code = pack.main(args)
        info = json.loads((out / "BUILD_INFO.json").read_text(encoding="utf-8"))
        return code, out, info

    def test_tracked_code_is_packaged_control(self):
        code, out, info = self.execute()
        self.assertEqual(code, 0)
        self.assertEqual((out / "blackjack_lab" / "main.py").read_bytes(),
                         self.committed_bytes("blackjack_lab/main.py"))
        self.assertIn("blackjack_lab/main.py", info["source_manifest"])
        self.assertIn("fixtures/demo.json", info["package_manifest"])
        self.assertNotIn("fixtures/demo.json", info["source_manifest"])

    def test_default_database_is_excluded_control(self):
        (self.root / "data").mkdir()
        (self.root / "data" / "session.db").write_bytes(b"SYNTHETIC-DB-NOT-USER-DATA")
        code, out, _ = self.execute()
        self.assertEqual(code, 0)
        self.assertFalse((out / "data").exists())

    def test_gitignored_env_is_not_packaged(self):
        (self.root / "docs" / ".env").write_text("TEST_SENTINEL=not_a_real_key\n", encoding="utf-8")
        self.assertEqual(self.git("status", "--porcelain"), "")
        code, out, info = self.execute()
        self.assertEqual(code, 0)
        self.assertFalse((out / "docs" / ".env").exists(), "Git-ignored .env was copied by a --require-clean run")
        self.assertFalse(info["user_data_copied"])

    def test_gitignored_sqlite3_is_not_packaged(self):
        (self.root / "fixtures" / "session.sqlite3").write_bytes(b"SYNTHETIC-SQLITE-CONTENT-NOT-USER-DATA")
        self.assertEqual(self.git("status", "--porcelain"), "")
        code, out, _ = self.execute()
        self.assertEqual(code, 0)
        self.assertFalse((out / "fixtures" / "session.sqlite3").exists(), "Git-ignored SQLite file was copied")

    def test_changed_fixture_invalidates_package_identity(self):
        _, out, _ = self.execute()
        (out / "fixtures" / "demo.json").write_text('{"kind":"altered-synthetic-demo"}\n', encoding="utf-8")
        status = source_identity(out)
        self.assertTrue(status["dirty_worktree"], "Changed shipped fixture was not covered by the package manifest")

    def test_require_clean_refuses_dirty_worktree(self):
        (self.root / "blackjack_lab" / "dirty.py").write_text("x=1\n", encoding="utf-8")
        self.assertTrue(self.git("status", "--porcelain").strip())
        out = self.folder / "runtime"
        with patch.object(pack, "ROOT", self.root):
            with self.assertRaises(SystemExit) as error:
                pack.main(["--output", str(out), "--require-clean"])
        self.assertIn("不干净", str(error.exception))
        self.assertFalse(out.exists())

    def test_clean_worktree_is_not_skipped(self):
        self.assertEqual(self.git("status", "--porcelain"), "")
        code, out, _ = self.execute()
        self.assertEqual(code, 0)
        self.assertTrue((out / "BUILD_INFO.json").is_file())

    def test_symlink_is_rejected(self):
        target = self.root / "blackjack_lab" / "main.py"
        link = self.root / "blackjack_lab" / "alias.py"
        try:
            os.symlink(target, link)
        except OSError:
            self.skipTest("this Windows account cannot create symlinks")
        self.git("add", "blackjack_lab/alias.py")
        self.git("-c", "user.name=ReviewFixture", "-c", "user.email=review@example.invalid",
                 "commit", "-qm", "add symlink")
        out = self.folder / "runtime"
        with patch.object(pack, "ROOT", self.root):
            with self.assertRaises(SystemExit) as error:
                pack.main(["--output", str(out), "--require-clean"])
        self.assertIn("符号链接", str(error.exception))

    def test_untracked_file_is_not_packaged(self):
        (self.root / "blackjack_lab" / "local_only.py").write_text("not-committed\n", encoding="utf-8", newline="\n")
        self.assertTrue(self.git("status", "--porcelain").strip())
        code, out, info = self.execute(require_clean=False, expect_clean=False)
        self.assertEqual(code, 0)
        self.assertFalse((out / "blackjack_lab" / "local_only.py").exists())
        self.assertNotIn("blackjack_lab/local_only.py", info["package_manifest"])

    def test_dirty_tracked_file_packs_committed_bytes(self):
        path = self.root / "blackjack_lab" / "main.py"
        committed = self.committed_bytes("blackjack_lab/main.py")
        path.write_text("mutated-worktree\n", encoding="utf-8", newline="\n")
        code, out, _ = self.execute(require_clean=False, expect_clean=False)
        self.assertEqual(code, 0)
        self.assertEqual((out / "blackjack_lab" / "main.py").read_bytes(), committed)
        self.assertNotEqual((out / "blackjack_lab" / "main.py").read_bytes(), path.read_bytes())

    def test_extra_or_missing_packed_file_is_detected(self):
        _, out, _ = self.execute()
        extra = out / "fixtures" / "extra.json"
        extra.write_text('{"kind":"extra"}\n', encoding="utf-8", newline="\n")
        self.assertTrue(source_identity(out)["dirty_worktree"])
        extra.unlink()
        (out / "fixtures" / "demo.json").unlink()
        self.assertTrue(source_identity(out)["dirty_worktree"])


if __name__ == "__main__":
    unittest.main()
