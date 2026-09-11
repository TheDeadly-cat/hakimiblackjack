"""Copy a runnable source tree without user databases, git history, or local caches."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.source_identity import source_identity
from scripts.verify_release import source_manifest

SKIP_DIR_NAMES = {
    ".git", ".local-evidence", ".local-native", "data", "backups", "dist",
    "__pycache__", ".venv", "venv", ".cursor", ".pytest_cache", ".idea",
}
SKIP_RELATIVE_PREFIXES = ("docs/acceptance/",)
SKIP_SUFFIXES = {".pyc", ".pyo", ".db", ".zip", ".log"}
TOP_DIRS = ("blackjack_lab", "tests", "scripts", "fixtures", "docs", "review_tests", ".github")
TOP_FILES = (
    "README.md", "NOTICE.md", "requirements.txt", "requirements-qa.txt",
    "启动界面.bat", "运行测试.bat",
)


def _skip_dir(name):
    return name in SKIP_DIR_NAMES or name.endswith(".analysis")


def copy_tree(source, dest):
    dest.mkdir(parents=True, exist_ok=False)
    for directory in TOP_DIRS:
        src = source / directory
        if not src.is_dir():
            continue
        for path in src.rglob("*"):
            if any(_skip_dir(part) for part in path.relative_to(source).parts):
                continue
            if path.is_dir():
                continue
            if path.suffix.lower() in SKIP_SUFFIXES:
                continue
            relative = path.relative_to(source)
            posix = relative.as_posix()
            if any(posix == prefix.rstrip("/") or posix.startswith(prefix)
                   for prefix in SKIP_RELATIVE_PREFIXES):
                continue
            target = dest / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    for name in TOP_FILES:
        path = source / name
        if path.is_file():
            shutil.copy2(path, dest / name)


def write_build_info(dest, identity):
    original = source_manifest()
    copied = {}
    missing = []
    for relative, expected in original.items():
        path = dest / relative
        if not path.is_file():
            missing.append(relative)
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        copied[relative] = digest
        if digest != expected:
            missing.append(relative + " (hash mismatch)")
    info = {
        "schema": "hakimi-portable-package-v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_commit": identity.get("commit"),
        "packed_from_kind": identity.get("kind"),
        "dirty_worktree_when_packed": bool(identity.get("dirty_worktree")),
        "source_manifest": copied,
        "missing_or_mismatched": missing,
        "user_data_copied": False,
        "note": "Empty new data directory on first run. Do not copy user SQLite or .analysis.",
    }
    (dest / "BUILD_INFO.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    return info


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-clean", action="store_true",
                        help="Refuse to pack a dirty git worktree")
    args = parser.parse_args(argv)
    identity = source_identity(ROOT)
    if args.require_clean and identity.get("dirty_worktree"):
        raise SystemExit("工作树不干净，拒绝打包固定运行副本")
    output = args.output or ROOT / ".local-evidence" / (
        "runtime-v02b2-das-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6])
    output = output.resolve()
    if output.exists():
        raise SystemExit("输出目录已存在，拒绝覆盖: " + str(output))
    copy_tree(ROOT, output)
    info = write_build_info(output, identity)
    packed = source_identity(output)
    summary = dict(
        output=str(output),
        source_commit=info["source_commit"],
        packed_identity=packed,
        files=sum(1 for path in output.rglob("*") if path.is_file()),
        missing_or_mismatched=info["missing_or_mismatched"],
        user_data_copied=False,
    )
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    if info["missing_or_mismatched"] or packed.get("dirty_worktree"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
