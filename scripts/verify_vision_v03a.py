# -*- coding: utf-8 -*-
"""V0.3a Windows 工作流证据：识别计时与入账计时分开，不把测试数量当能力证明。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.source_identity import source_identity


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--rebuild-holdout", action="store_true")
    args = parser.parse_args()
    output = args.output or (
        ROOT / ".local-evidence" /
        ("acceptance-v03a-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6])
    )
    output.mkdir(parents=True, exist_ok=False)
    identity = source_identity(ROOT)
    environment = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    checks = []

    def run(name, arguments, timeout=180):
        start = time.perf_counter()
        command = [sys.executable, *arguments]
        try:
            result = subprocess.run(
                command, cwd=ROOT, env=environment, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, encoding="utf-8", timeout=timeout)
            text, code = result.stdout, result.returncode
        except subprocess.TimeoutExpired as exc:
            text = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
            text += "\nVISION ACCEPTANCE TIMEOUT\n"
            code = None
        dest = output / (name + ".txt")
        dest.write_text(text, encoding="utf-8")
        item = {
            "name": name, "command": command, "exit_code": code,
            "elapsed_seconds": time.perf_counter() - start,
            "output": dest.name, "output_sha256": sha(dest),
        }
        checks.append(item)
        print(f"{name}: exit={code} elapsed={item['elapsed_seconds']:.3f}s", flush=True)
        return code == 0

    ok = True
    ok = run("selfcheck", ["-m", "blackjack_lab.main", "--check"]) and ok
    ok = run("vision-and-confirm-tests", [
        "-m", "unittest",
        "tests.test_vision_contracts", "tests.test_vision_image_io",
        "tests.test_vision_isolation", "tests.test_vision_pipeline",
        "tests.test_vision_bridge", "tests.test_vision_ui_confirm", "-q",
    ]) and ok
    ok = run("experiment-gates", [
        "-m", "unittest", "tests.test_experiments", "tests.test_das_release_gate", "-q",
    ], timeout=180) and ok

    demo = ROOT / "fixtures" / "vision" / "synthetic-v1" / "smoke" / "all13.png"
    if demo.is_file():
        ok = run("vision-demo-no-ledger", [
            "scripts/vision_demo.py", str(demo),
            "--json", str(output / "demo-all13.json"),
        ]) and ok

    holdout_args = ["scripts/vision_benchmarks.py", "--json", str(output / "holdout-report.json"),
                    "--errors", str(output / "error-samples")]
    if args.rebuild_holdout or not (ROOT / "fixtures/vision/synthetic-v1/holdout/manifest.json").is_file():
        holdout_args.append("--rebuild")
    ok = run("holdout-original-prediction", holdout_args, timeout=900) and ok

    confirm = _scripted_confirm(output)
    (output / "confirm-workflow.json").write_text(
        json.dumps(confirm, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = confirm.get("passed") and ok

    protection = _data_protection()
    (output / "data-protection.json").write_text(
        json.dumps(protection, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = protection.get("passed") and ok

    holdout_report = {}
    report_path = output / "holdout-report.json"
    if report_path.is_file():
        holdout_report = json.loads(report_path.read_text(encoding="utf-8"))
    receipt = {
        "schema": "hakimi-v03a-vision-acceptance-v1",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "commit": identity.get("commit"),
        "working_tree_dirty": bool(identity.get("dirty_worktree")),
        "python": sys.version,
        "platform": platform.platform(),
        "checks": checks,
        "confirm_workflow": confirm,
        "data_protection": protection,
        "holdout_size_and_accuracy_gates_met": holdout_report.get("size_and_accuracy_gates_met"),
        "suggested_e2e_recall_met": (
            (holdout_report.get("suggested_gates") or {}).get("suggested_e2e_recall") or {}
        ).get("met"),
        "platform_claim": "none",
        "not_general_ocr": True,
        "user_database_accessed": False,
    }
    receipt["passed"] = bool(ok and receipt["holdout_size_and_accuracy_gates_met"]
                             and confirm.get("passed") and protection.get("passed"))
    (output / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "passed": receipt["passed"]}, ensure_ascii=False), flush=True)
    return 0 if receipt["passed"] else 1


def _scripted_confirm(output: Path) -> dict:
    from blackjack_lab.analysis.contracts import research_rules
    from blackjack_lab.ledger.events import SOURCE_VISION_CONFIRMED
    from blackjack_lab.ui.controller import SessionController
    from blackjack_lab.ui.vision_bridge import (
        FACE_SHOWN_LEDGER, OP_NEW, OP_REJECT, ConfirmDecision, VisionReviewSession,
    )
    from blackjack_lab.vision.image_io import load_image
    from blackjack_lab.vision.pipeline import recognize_loaded
    from blackjack_lab.vision.rank_recognizer import load_template_bank

    smoke = ROOT / "fixtures" / "vision" / "synthetic-v1" / "smoke" / "two_eights.png"
    templates = ROOT / "fixtures" / "vision" / "synthetic-v1" / "templates"
    if not smoke.is_file() or not (templates / "A.png").is_file():
        return {"passed": False, "reason": "missing smoke/templates"}
    started = time.perf_counter()
    loaded = load_image(smoke)
    result = recognize_loaded(loaded, bank=load_template_bank(templates), templates_dir=templates)
    recognize_seconds = time.perf_counter() - started
    with tempfile.TemporaryDirectory() as folder:
        ctrl = SessionController(Path(folder) / "vision.db")
        try:
            ctrl.new_shoe(research_rules(6))
            ctrl.start_round(["玩家1"])
            before = list(ctrl.ledger.to_list())
            sess = VisionReviewSession(ctrl, result, Path(folder) / "ev")
            eights = [obs for obs in result.observations if obs.accepted_rank() == "8"]
            if len(eights) < 2:
                return {"passed": False, "reason": "smoke two eights not recognized",
                        "recognize_seconds": recognize_seconds}
            ledger_started = time.perf_counter()
            first = sess.confirm(ConfirmDecision(
                eights[0].observation_id, OP_NEW, seat="玩家1", confirmed_rank="8",
                face_state=FACE_SHOWN_LEDGER))
            second = sess.confirm(ConfirmDecision(
                eights[1].observation_id, OP_NEW, seat="玩家1", confirmed_rank="8",
                face_state=FACE_SHOWN_LEDGER))
            reject_blocked = False
            try:
                from blackjack_lab.ui.vision_bridge import VisionBridgeError
                sess.confirm(ConfirmDecision(eights[0].observation_id, OP_REJECT))
            except VisionBridgeError:
                reject_blocked = True
            ledger_seconds = time.perf_counter() - ledger_started
            remaining8 = ctrl.state().current.shoe.exact_out.get("8", 0)
            return {
                "passed": (
                    first.event.source == SOURCE_VISION_CONFIRMED
                    and second.event.event_id != first.event.event_id
                    and remaining8 == 2
                    and reject_blocked
                ),
                "recognize_seconds": recognize_seconds,
                "ledger_confirm_seconds": ledger_seconds,
                "two_eights_debited": remaining8,
                "reject_after_confirm_blocked": reject_blocked,
                "prefix_unchanged_length": len(before),
                "timing_split": "recognize_seconds 不含入账；ledger_confirm_seconds 不含求解",
            }
        finally:
            ctrl.close()


def _data_protection() -> dict:
    tracked = subprocess.check_output(
        ["git", "ls-files"], cwd=ROOT, text=True, encoding="utf-8").splitlines()
    forbidden = [name for name in tracked if name.endswith((".db", ".sqlite", ".sqlite3"))
                 or "holdout/images/" in name.replace("\\", "/")
                 or name.endswith(".env")]
    return {
        "passed": not forbidden,
        "forbidden_tracked": forbidden,
        "holdout_images_not_in_git": "fixtures/vision/synthetic-v1/holdout/images/" not in "\n".join(tracked),
        "note": "holdout 原图 gitignore；不把用户库或私人截图写入公开树",
    }


if __name__ == "__main__":
    raise SystemExit(main())
