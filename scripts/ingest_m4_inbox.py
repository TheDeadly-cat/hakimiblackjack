"""Bind files dropped into the M4 inbox. Never marks the pack accepted."""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blackjack_lab.analysis.acceptance_pack import (
    ITEMS, build_acceptance_pack, ingest_inbox,
)
from blackjack_lab.analysis.evidence import write_manifest

DEFAULT_INBOX = ROOT / ".local-evidence" / "m4-inbox"
DEFAULT_OUTPUT = ROOT / ".local-evidence" / "m4-acceptance-pending.json"


def _git_identity():
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, timeout=10).strip()
        dirty = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=ROOT, text=True, timeout=10).strip() != ""
        return commit, dirty
    except (OSError, subprocess.SubprocessError):
        return None, None


def main():
    parser = argparse.ArgumentParser(description="扫描 M4 收件箱并哈希绑定；软件不能写成通过")
    parser.add_argument("--inbox", type=Path, default=DEFAULT_INBOX)
    parser.add_argument("--pack", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    commit, dirty = _git_identity()
    if args.pack is not None:
        pack = json.loads(args.pack.read_text(encoding="utf-8"))
    else:
        pack = build_acceptance_pack(code_commit=commit, dirty_worktree=dirty)
    pack = ingest_inbox(pack, args.inbox)
    if pack.get("accepted") or pack.get("passed"):
        raise SystemExit("收件箱绑定不得把验收包写成通过")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_manifest(args.output, pack)
    print(json.dumps({
        "output": str(args.output),
        "inbox": str(args.inbox),
        "bound": len(pack.get("inbox_bound") or []),
        "accepted": pack["accepted"],
        "evidence_level": pack["evidence_level"],
        "folders": [item_id for item_id, _label in ITEMS],
        "note": "把文件放进对应子目录后重跑本脚本；哈希不是验收",
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
