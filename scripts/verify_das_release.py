"""DAS research-candidate acceptance. Unique output dirs; b1 scripts stay separate."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.source_identity import source_identity
from scripts.verify_release import source_manifest, sha
from scripts.das_matrix_gate import validate_attached_matrix


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--matrix-dir", type=Path,
                        help="已存在的 DAS 336 回执目录；提供则核对 receipt.json，不再重跑矩阵")
    args = parser.parse_args()
    output = (args.output or ROOT / ".local-evidence" / (
        "acceptance-v02b2-das-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6])).resolve()
    if output.exists():
        raise SystemExit("输出目录已存在，拒绝覆盖: " + str(output))
    output.mkdir(parents=True, exist_ok=False)
    before = source_manifest()
    identity = source_identity(ROOT)
    environment = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    checks = []
    not_run = []

    def run(name, arguments, timeout=180, env=None):
        started = time.perf_counter()
        command = [sys.executable, *arguments]
        try:
            result = subprocess.run(command, cwd=ROOT, env=env or environment, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, encoding="utf-8", timeout=timeout)
            code, text = result.returncode, result.stdout
        except subprocess.TimeoutExpired as error:
            code = None
            text = error.stdout or ""
            if isinstance(text, bytes):
                text = text.decode("utf-8", errors="replace")
            text += "\nACCEPTANCE COMMAND TIMEOUT\n"
        path = output / (name + ".txt")
        path.write_text(text, encoding="utf-8")
        checks.append(dict(name=name, command=command, exit_code=code,
                           elapsed_seconds=time.perf_counter() - started,
                           output=path.name, sha256=sha(path)))
        print(f"{name}: exit={code} elapsed={checks[-1]['elapsed_seconds']:.3f}s", flush=True)
        if code != 0:
            print(text[-3500:], flush=True)

    run("selfcheck", ["-m", "blackjack_lab.main", "--check"])
    run("prepare-native", ["-m", "blackjack_lab.main", "--prepare-split"])
    run("das-focus", ["-m", "unittest",
                      "tests.test_das_pending_hit", "tests.test_das_split_reference",
                      "tests.test_das_split_engine", "tests.test_das_workflow",
                      "tests.test_das_benchmark_contract", "tests.test_das_history_roundtrip",
                      "tests.test_das_release_gate", "tests.test_das_optimization_diff",
                      "tests.test_portable_copy", "tests.test_portable_runtime", "-v"])
    run("compile", ["-m", "compileall", "-q", "blackjack_lab", "tests", "scripts"])
    run("original-5-48-4", ["scripts/verify_review_handoff.py", "--output", str(output / "original-5-48-4")])
    run("original-n1-four", ["-m", "unittest", "discover",
                             "-s", "docs/acceptance/n1-20260910-220300-9f6f68/original",
                             "-p", "test_snapshot_shape.py", "-v"])
    with tempfile.TemporaryDirectory() as temporary:
        guard = Path(temporary) / "sitecustomize.py"
        guard.write_text("""import os,socket
from pathlib import Path
with Path(os.environ['HAKIMI_GUARD_LOG']).open('a',encoding='utf-8') as stream: stream.write(str(os.getpid())+'\\n')
def blocked(*args,**kwargs):
    with Path(os.environ['HAKIMI_ATTEMPT_LOG']).open('a',encoding='utf-8') as stream: stream.write('blocked network call\\n')
    raise RuntimeError('Python network blocked by acceptance guard')
socket.create_connection=blocked
socket.getaddrinfo=blocked
socket.socket.connect=blocked
socket.socket.connect_ex=blocked
socket._hakimi_offline_guard=True
""", encoding="utf-8")
        env = dict(environment, PYTHONPATH=temporary + os.pathsep + str(ROOT), HAKIMI_OFFLINE_REQUIRED="1",
                   HAKIMI_GUARD_LOG=str(output / "guard-processes.txt"),
                   HAKIMI_ATTEMPT_LOG=str(output / "network-attempts.txt"))
        run("guarded-das-workflows", ["-m", "unittest", "tests.test_das_workflow", "tests.test_split_workflow", "-v"],
            env=env)
    run("das-ui", ["scripts/das_ui_check.py", "--output", str(output / "screens")])
    run("portable-copy", ["scripts/make_portable_copy.py", "--output", str(output / "runtime"),
                          "--require-clean"], timeout=180)
    matrix_receipt = None
    if args.matrix_dir:
        source = args.matrix_dir.resolve()
        receipt_path = source / "receipt.json"
        if not receipt_path.is_file():
            raise SystemExit("矩阵目录缺少 receipt.json")
        attached = output / "performance"
        shutil.copytree(source, attached, dirs_exist_ok=False)
        verdict = validate_attached_matrix(attached)
        (attached / "matrix-gate.json").write_text(
            json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8")
        matrix_receipt = verdict
        checks.append(dict(name="das-cold-matrix", command=["attached-verified", str(source)],
                           exit_code=0 if verdict["passed"] else 1,
                           elapsed_seconds=0.0, output="performance/matrix-gate.json",
                           sha256=sha(attached / "matrix-gate.json"),
                           claimed_passed=verdict.get("claimed_passed"),
                           gate_errors=verdict.get("errors")))
        print("das-cold-matrix: attached claimed=%s verified=%s errors=%s" % (
            verdict.get("claimed_passed"), verdict["passed"], verdict.get("errors")), flush=True)
    else:
        run("das-cold-matrix", ["scripts/das_benchmarks.py", "--output", str(output / "performance")], timeout=1200)
        receipt_path = output / "performance" / "receipt.json"
        if receipt_path.is_file():
            verdict = validate_attached_matrix(output / "performance")
            (output / "performance" / "matrix-gate.json").write_text(
                json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8")
            matrix_receipt = verdict
            if not verdict["passed"]:
                checks[-1]["exit_code"] = 1
                checks[-1]["gate_errors"] = verdict.get("errors")
                print("das-cold-matrix: live claimed ignored; verified=%s errors=%s" % (
                    verdict["passed"], verdict.get("errors")), flush=True)
    receipt = dict(
        schema="hakimi-v02b2-das-acceptance-v1",
        timestamp_utc=datetime.now(timezone.utc).isoformat(),
        identity=identity, python=sys.version, platform=platform.platform(),
        source_manifest=before, source_unchanged=before == source_manifest(),
        checks=checks, not_run=not_run, user_database_accessed=False,
        data_scope="Only temporary SQLite and explicitly synthetic input fixtures",
        matrix=None if matrix_receipt is None else {
            k: matrix_receipt.get(k) for k in (
                "count", "completed", "failed", "timed_out", "p50_seconds", "p95_seconds",
                "max_seconds", "passed", "statuses", "errors", "claimed_passed")
        },
        network_scope=("Python socket guard includes spawned Python workers; "
                       "native code uses local stdin/stdout only. This is not an OS firewall."),
        network_attempts=(output / "network-attempts.txt").read_text(encoding="utf-8")
        if (output / "network-attempts.txt").exists() else "",
        note="DAS 336 matrix and b1 318 cold requests are different artifacts; unittest count is a third artifact.",
    )
    receipt["passed"] = (all(c["exit_code"] == 0 for c in checks) and receipt["source_unchanged"]
                         and not receipt["network_attempts"] and not not_run
                         and matrix_receipt is not None and matrix_receipt.get("passed") is True)
    (output / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "evidence-manifest.json").write_text(json.dumps(
        {p.relative_to(output).as_posix(): sha(p) for p in sorted(output.rglob("*")) if p.is_file()},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(dict(output=str(output), passed=receipt["passed"], identity=identity,
                          matrix=receipt["matrix"]), ensure_ascii=False), flush=True)
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
