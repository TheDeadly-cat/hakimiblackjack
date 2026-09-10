"""V0.2a acceptance: unique immutable outputs, frozen source identity, actual workflows."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.source_identity import source_identity


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_manifest():
    paths = []
    for directory in ("blackjack_lab", "tests", "scripts", "review_tests"):
        paths.extend((ROOT / directory).rglob("*.py"))
    paths.extend((ROOT / "blackjack_lab/analysis/native").glob("*.cs"))
    paths.extend(ROOT.glob("*.bat"))
    paths.extend([ROOT / "requirements.txt", ROOT / "requirements-qa.txt", ROOT / "NOTICE.md", ROOT / "README.md"])
    paths.extend((ROOT / ".github").rglob("*.yml"))
    paths.extend((ROOT / "review_tests").glob("*.json"))
    return {p.relative_to(ROOT).as_posix(): sha(p) for p in sorted(set(paths))}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or ROOT / ".local-evidence" / ("acceptance-v02a-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6])
    output.mkdir(parents=True, exist_ok=False)
    before = source_manifest()
    identity = source_identity(ROOT)
    head, dirty = identity["commit"], identity["dirty_worktree"]
    environment = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    checks = []
    def run(name, arguments, env=None):
        start = time.perf_counter()
        command = [sys.executable, *arguments]
        try:
            result = subprocess.run(command, cwd=ROOT, env=env or environment, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, encoding="utf-8", timeout=120)
            text, code = result.stdout, result.returncode
        except subprocess.TimeoutExpired as exc:
            text = (exc.stdout or b"").decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            text += "\nACCEPTANCE COMMAND TIMEOUT\n"
            code = None
        file = output / (name + ".txt")
        file.write_text(text, encoding="utf-8")
        item = {"name": name, "command": command, "cwd": str(ROOT), "exit_code": code,
                "elapsed_seconds": time.perf_counter() - start, "output": file.name, "output_sha256": sha(file)}
        count = re.search(r"Ran (\d+) tests", text)
        if count:
            item["test_count"] = int(count.group(1))
        checks.append(item)
        print(f"{name}: exit={code} elapsed={item['elapsed_seconds']:.3f}s", flush=True)
        if code != 0:
            print(text[-3500:], flush=True)
    run("tests", ["-m", "unittest", "discover", "-s", "tests", "-v"])
    run("selfcheck", ["-m", "blackjack_lab.main", "--check"])
    run("compile", ["-m", "compileall", "-q", "blackjack_lab", "tests", "scripts"])
    with tempfile.TemporaryDirectory() as temp:
        guard = Path(temp) / "sitecustomize.py"
        guard.write_text("""import os, socket
from pathlib import Path
with Path(os.environ['HAKIMI_GUARD_LOG']).open('a', encoding='utf-8') as log:
    log.write(str(os.getpid()) + '\\n')
def blocked(*args, **kwargs):
    with Path(os.environ['HAKIMI_ATTEMPT_LOG']).open('a', encoding='utf-8') as log:
        log.write('blocked network call\\n')
    raise RuntimeError('Network disabled by acceptance guard')
socket.create_connection = blocked
socket.getaddrinfo = blocked
socket.socket.connect = blocked
socket.socket.connect_ex = blocked
socket._hakimi_offline_guard = True
""".replace("\n+", "\n"), encoding="utf-8")
        offline_env = dict(environment, PYTHONPATH=str(Path(temp)) + os.pathsep + str(ROOT),
            HAKIMI_GUARD_LOG=str(output / "offline-processes.txt"),
            HAKIMI_ATTEMPT_LOG=str(output / "offline-network-attempts.txt"), HAKIMI_OFFLINE_REQUIRED="1")
        code = ("import socket,sys,unittest; assert socket._hakimi_offline_guard; "
                "suite=unittest.defaultTestLoader.loadTestsFromNames(['tests.test_analysis_ui','tests.test_analysis_integration','tests.test_round_observation','tests.test_v02a_review_regressions']); "
                "r=unittest.TextTestRunner(verbosity=2).run(suite); sys.exit(0 if r.wasSuccessful() else 1)")
        run("offline-workflows", ["-c", code], offline_env)
    run("math-performance", ["scripts/analysis_checks.py", "--output", str(output / "math-performance")])
    run("ui-visual", ["scripts/ui_visual_check.py", "--output", str(output / "screens")])
    run("review-visual", ["scripts/review_visual_check.py", "--output", str(output / "review-screens")])
    run("original-review-handoff", ["scripts/verify_review_handoff.py", "--output", str(output / "original-review-handoff")])
    manifest_file = output / "source-manifest.json"
    manifest_file.write_text(json.dumps(before, ensure_ascii=False, indent=2), encoding="utf-8")
    receipt = {"schema": "hakimi-v02a-acceptance-v1", "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "commit": head, "working_tree_dirty_before": bool(dirty), "source_identity_kind": identity["kind"], "python": sys.version,
        "platform": platform.platform(), "source_manifest_sha256": sha(manifest_file),
        "source_unchanged_during_checks": before == source_manifest(), "checks": checks,
        "user_database_accessed": False, "test_data": "temporary SQLite and self-generated observations only",
        "offline_scope": "socket connect/connect_ex/create_connection/getaddrinfo blocked in separate Python processes including analysis workers; not an OS firewall test",
        "offline_network_attempts": (output / "offline-network-attempts.txt").read_text(encoding="utf-8") if (output / "offline-network-attempts.txt").exists() else "",
        "scope": "V0.2a selected single-player unsplit S17/3:2/US-peek/zero-burn template; no split EV, vision, capture or opening advantage"}
    receipt["passed"] = all(c["exit_code"] == 0 for c in checks) and receipt["source_unchanged_during_checks"] and not receipt["offline_network_attempts"]
    (output / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "passed": receipt["passed"], "commit": head, "dirty": bool(dirty)}), flush=True)
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
