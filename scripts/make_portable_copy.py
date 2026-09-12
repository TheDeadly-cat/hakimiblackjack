"""Copy a runnable tree from the frozen Git file list, not the live working directory."""
import argparse
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.source_identity import package_files_on_disk, sha256_file, source_identity

SKIP_RELATIVE_PREFIXES = ("docs/acceptance/",)
SKIP_SUFFIXES = {".pyc", ".pyo", ".db", ".db-journal", ".db-wal", ".db-shm",
                 ".sqlite", ".sqlite3", ".log", ".zip", ".bak"}
SKIP_NAMES = {".env"}
TOP_DIRS = ("blackjack_lab", "tests", "scripts", "fixtures", "docs", "review_tests", ".github")
TOP_FILES = (
    "README.md", "NOTICE.md", "requirements.txt", "requirements-qa.txt",
    "启动界面.bat", "运行测试.bat",
)
COMPUTE_TOP = ("blackjack_lab", "tests", "scripts", "review_tests")


def git_tracked_files(root):
    try:
        result = subprocess.run(["git", "-C", str(root), "ls-tree", "-r", "--name-only", "-z", "HEAD"],
                                capture_output=True, check=False)
    except OSError as error:
        raise SystemExit("没有可用的 git，拒绝正式发行打包") from error
    if result.returncode:
        raise SystemExit("当前目录不是可核对的 git 仓库，拒绝按固定提交打包")
    names = [name.decode("utf-8") for name in result.stdout.split(b"\0") if name]
    if not names:
        raise SystemExit("固定提交没有可打包文件")
    return names


def committed_symlinks(root):
    result = subprocess.run(["git", "-C", str(root), "ls-tree", "-r", "HEAD"],
                            capture_output=True, check=False, text=True)
    if result.returncode:
        raise SystemExit("无法读取固定提交树")
    links = []
    for line in result.stdout.splitlines():
        if line.startswith("120000"):
            links.append(line.split("\t", 1)[-1].replace("\\", "/"))
    return links


def allowed_relative(posix):
    name = Path(posix).name
    if name in SKIP_NAMES or name.startswith(".env."):
        return False
    if Path(posix).suffix.lower() in SKIP_SUFFIXES:
        return False
    if posix in TOP_FILES:
        return True
    if any(posix == prefix.rstrip("/") or posix.startswith(prefix) for prefix in SKIP_RELATIVE_PREFIXES):
        return False
    return any(posix == directory or posix.startswith(directory + "/") for directory in TOP_DIRS)


def is_compute_relative(posix):
    if posix in TOP_FILES:
        return True
    suffix = Path(posix).suffix.lower()
    if posix.startswith(".github/") and suffix in {".yml", ".yaml"}:
        return True
    if posix.startswith("review_tests/") and suffix == ".json":
        return True
    if suffix in {".py", ".cs"}:
        return any(posix == directory or posix.startswith(directory + "/") for directory in COMPUTE_TOP)
    return False


def copy_commit_tree(source, dest):
    dest.mkdir(parents=True, exist_ok=False)
    blocked = [posix for posix in committed_symlinks(source) if allowed_relative(posix)]
    if blocked:
        raise SystemExit("拒绝打包符号链接: " + blocked[0])
    try:
        result = subprocess.run(["git", "-C", str(source), "archive", "--format=tar", "HEAD"],
                                capture_output=True, check=False)
    except OSError as error:
        raise SystemExit("没有可用的 git，拒绝正式发行打包") from error
    if result.returncode:
        raise SystemExit("无法导出固定提交文件树")
    copied = []
    with tarfile.open(fileobj=io.BytesIO(result.stdout), mode="r:") as archive:
        for member in archive.getmembers():
            posix = member.name.replace("\\", "/")
            if posix.startswith("./"):
                posix = posix[2:]
            if not posix or posix.endswith("/"):
                continue
            if not allowed_relative(posix):
                continue
            if member.issym() or member.islnk():
                raise SystemExit("拒绝打包符号链接: " + posix)
            if not member.isfile():
                continue
            extracted = archive.extractfile(member)
            if extracted is None:
                raise SystemExit("无法读取固定提交中的文件: " + posix)
            payload = extracted.read()
            target = dest / posix
            if target.exists() or target.is_symlink():
                raise SystemExit("拒绝覆盖已有路径: " + posix)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            if target.is_symlink():
                raise SystemExit("拒绝打包符号链接: " + posix)
            copied.append(posix)
    if not copied:
        raise SystemExit("允许清单为空，拒绝打包")
    return copied


def write_build_info(dest, identity, packed_files, source):
    tracked = git_tracked_files(source)
    expected_compute = [posix for posix in tracked if allowed_relative(posix) and is_compute_relative(posix)]
    missing = []
    copied = {}
    packed_set = set(packed_files)
    for relative in expected_compute:
        path = dest / relative
        if relative not in packed_set or not path.is_file() or path.is_symlink():
            missing.append(relative)
            continue
        copied[relative] = sha256_file(path)
    packed = package_files_on_disk(dest)
    extra = sorted(set(packed) - packed_set)
    info = {
        "schema": "hakimi-portable-package-v2",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_commit": identity.get("commit"),
        "packed_from_kind": identity.get("kind"),
        "dirty_worktree_when_packed": bool(identity.get("dirty_worktree")),
        "source_manifest": copied,
        "package_manifest": packed,
        "packed_file_count": len(packed),
        "missing_or_mismatched": missing,
        "unexpected_packed_files": extra,
        "user_data_copied": False,
        "note": "Packed from git HEAD plus an allowlist. Empty data directory on first run.",
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
    if identity.get("kind") != "git":
        raise SystemExit("正式运行包必须从 git 提交树打包")
    if args.require_clean and identity.get("dirty_worktree"):
        raise SystemExit("工作树不干净，拒绝打包固定运行副本")
    output = args.output or ROOT / ".local-evidence" / (
        "runtime-v02b2-das-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6])
    output = output.resolve()
    if output.exists():
        raise SystemExit("输出目录已存在，拒绝覆盖: " + str(output))
    packed_files = copy_commit_tree(ROOT, output)
    info = write_build_info(output, identity, packed_files, ROOT)
    packed = source_identity(output)
    summary = dict(
        output=str(output),
        source_commit=info["source_commit"],
        packed_identity=packed,
        files=info["packed_file_count"],
        missing_or_mismatched=info["missing_or_mismatched"],
        unexpected_packed_files=info["unexpected_packed_files"],
        user_data_copied=False,
    )
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    if (info["missing_or_mismatched"] or info["unexpected_packed_files"]
            or packed.get("dirty_worktree") or packed.get("kind") != "portable-package-manifest"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
