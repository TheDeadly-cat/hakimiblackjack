"""Write an unchecked M4 acceptance pack. Never marks human items passed."""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blackjack_lab.analysis.acceptance_pack import build_acceptance_pack
from blackjack_lab.analysis.evidence import write_manifest


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
    parser = argparse.ArgumentParser(description="生成未勾选真实材料验收包；软件不能写成通过")
    parser.add_argument("--local-root", type=Path, default=ROOT / ".local-evidence",
                        help="只哈希本机候选，默认 .local-evidence；不把它们写成已验收")
    parser.add_argument("--output", type=Path,
                        default=ROOT / ".local-evidence" / "m4-acceptance-pending.json")
    args = parser.parse_args()
    commit, dirty = _git_identity()
    local_root = args.local_root if args.local_root.is_dir() else None
    pack = build_acceptance_pack(code_commit=commit, dirty_worktree=dirty, local_root=local_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_manifest(args.output, pack)
    print(json.dumps({
        "output": str(args.output),
        "accepted": pack["accepted"],
        "evidence_level": pack["evidence_level"],
        "local_candidates": len(pack["local_candidates"]),
        "code_commit": commit,
        "dirty_worktree": dirty,
        "note": pack["note"],
    }, ensure_ascii=False, indent=2))
    if pack["accepted"]:
        raise SystemExit("验收包不得为 accepted")


if __name__ == "__main__":
    main()
