"""保存最终源文件身份、真实命令、退出码与验收输出；不打开用户数据库。"""
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "review-20260910"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_manifest():
    paths = []
    for directory in ("blackjack_lab", "tests", "scripts"):
        paths.extend((ROOT / directory).rglob("*.py"))
    paths.extend(ROOT.glob("*.bat"))
    paths.extend([ROOT / "requirements.txt", ROOT / "NOTICE.md"])
    return {p.relative_to(ROOT).as_posix(): sha(p) for p in sorted(paths)}


def main():
    OUTPUT.mkdir(exist_ok=True)
    manifest = source_manifest()
    original_db = ROOT / "data" / "blackjack_lab.db"
    original_hash = sha(original_db) if original_db.exists() else None
    environment = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    commands = [
        ("final-tests", [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]),
        ("final-selfcheck", [sys.executable, "-m", "blackjack_lab.main", "--check"]),
        ("final-compile", [sys.executable, "-m", "compileall", "-q", "blackjack_lab", "tests", "scripts"]),
        ("final-ui-visual", [sys.executable, "scripts/ui_visual_check.py"]),
    ]
    results = []
    for name, command in commands:
        start = time.time()
        result = subprocess.run(command, cwd=ROOT, env=environment, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, encoding="utf-8", timeout=60)
        text = result.stdout
        (OUTPUT / (name + ".txt")).write_text(text, encoding="utf-8")
        item = {"name": name, "command": command, "cwd": str(ROOT),
                "exit_code": result.returncode, "elapsed_seconds": round(time.time() - start, 3),
                "output": name + ".txt", "output_sha256": sha(OUTPUT / (name + ".txt"))}
        matched = re.search(r"Ran (\d+) tests", text)
        if matched:
            item["test_count"] = int(matched.group(1))
        results.append(item)
        print(f"{name}: exit={result.returncode}; {item['elapsed_seconds']}s")
        if result.returncode:
            print(text[-5000:])
    (OUTPUT / "source-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    receipt = {"created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "python": sys.version, "platform": platform.platform(), "source_manifest": "source-manifest.json",
               "source_manifest_sha256": sha(OUTPUT / "source-manifest.json"),
               "source_unchanged_during_checks": manifest == source_manifest(),
               "user_database_sha256_before": original_hash,
               "user_database_sha256_after": sha(original_db) if original_db.exists() else None,
               "checks": results,
               "scope": "V0.1 manual recording only; temporary SQLite and synthetic events; no EV, vision, capture or live platform tests"}
    receipt["passed"] = all(r["exit_code"] == 0 for r in results) and receipt["source_unchanged_during_checks"] and receipt["user_database_sha256_after"] == original_hash
    (OUTPUT / "final-receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": receipt["passed"], "manifest_sha256": receipt["source_manifest_sha256"]}))
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
