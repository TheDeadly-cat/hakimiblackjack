"""Identify a checkout or a portable archive without requiring Git to run the app."""
import hashlib
import json
from pathlib import Path
import subprocess


def source_identity(root):
    root = Path(root).resolve()
    try:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=root, capture_output=True, text=True)
        if top.returncode == 0 and Path(top.stdout.strip()).resolve() == root:
            head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
            dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True))
            return {"commit": head, "dirty_worktree": dirty, "kind": "git"}
    except OSError:
        pass
    info_path = root / "BUILD_INFO.json"
    if info_path.exists():
        info = json.loads(info_path.read_text(encoding="utf-8"))
        manifest = info.get("source_manifest", {})
        valid = bool(manifest)
        for name, expected in manifest.items():
            path = (root / name).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                valid = False
                continue
            valid = valid and hashlib.sha256(path.read_bytes()).hexdigest() == expected
        return {"commit": info.get("source_commit"), "dirty_worktree": not valid, "kind": "portable-package-manifest"}
    return {"commit": None, "dirty_worktree": True, "kind": "unversioned-directory"}
