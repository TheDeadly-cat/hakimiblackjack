"""Frozen 336-request DAS matrix: 300 pre-split plus 12 dynamic prefixes x 3 decks."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import platform
import statistics
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from blackjack_lab.analysis.information import build_input
from blackjack_lab.analysis.native_backend import build_native
from blackjack_lab.analysis.service import AnalysisService
from blackjack_lab.analysis.split_contracts import (
    DAS_ENGINE, DAS_PROFILE, DAS_STRATEGY, HARD_BUDGET_SECONDS, P95_TARGET_SECONDS,
    das_research_rules)
from blackjack_lab.core.rules import RuleProfile
from blackjack_lab.core.table import ACTION_DOUBLE, ACTION_HIT, ACTION_SPLIT, ACTION_STAND
from scripts.source_identity import source_identity
from tests.test_analysis_integration import example

SPEC_PATH = ROOT / "fixtures" / "v02b2" / "das_benchmark_spec.json"
SCHEMA = "hakimi-das-cold-matrix-v1"
ACTIONS = {"split": ACTION_SPLIT, "hit": ACTION_HIT, "double": ACTION_DOUBLE, "stand": ACTION_STAND}


def source_manifest():
    paths = list((ROOT / "blackjack_lab").rglob("*.py")) + list((ROOT / "blackjack_lab").rglob("*.cs"))
    paths += [Path(__file__), SPEC_PATH, ROOT / "tests" / "test_analysis_integration.py"]
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


def load_spec(path=SPEC_PATH):
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = spec.get("cases") or []
    ids = [case["id"] for case in cases]
    errors = []
    if not cases:
        errors.append("empty_matrix")
    if len(ids) != len(set(ids)):
        errors.append("duplicate_id")
    expected = spec.get("count")
    counts = spec.get("expected_counts") or {}
    if expected is not None and len(cases) != expected:
        errors.append("case_mismatch")
    groups = {}
    for case in cases:
        groups[case["group"]] = groups.get(case["group"], 0) + 1
    if counts and groups != counts:
        errors.append("case_mismatch")
    if spec.get("required_engine") != DAS_ENGINE or spec.get("required_strategy") != DAS_STRATEGY:
        errors.append("wrong_engine")
    if spec.get("profile") != DAS_PROFILE:
        errors.append("wrong_engine")
    spec["_load_errors"] = errors
    return spec


def _hand_id(ledger, selector):
    hands = ledger.replay().current.table.seat("玩家1").hands
    if selector in (None, "first"):
        return hands[0].hand_id
    if selector == "second":
        return hands[1].hand_id
    raise ValueError("未知手牌选择器: " + str(selector))


def construct_case(case):
    started = time.perf_counter()
    peek = bool(case.get("negative_peek"))
    ledger = example(case["decks"], cards=tuple(case["player_cards"]), up=case["dealer_up"],
                     rules=das_research_rules(case["decks"]), peek=peek)
    for step in case.get("steps") or []:
        action = step["action"]
        if action == "deal":
            ledger.deal("玩家1", step["rank"], hand_id=_hand_id(ledger, step.get("hand")), source="自建模拟器")
            continue
        if action not in ACTIONS:
            raise ValueError("未知步骤: " + action)
        ledger.player_action("玩家1", _hand_id(ledger, step.get("hand")), ACTIONS[action])
    snapshot = build_input(ledger, "玩家1")
    assert_das_identity(snapshot)
    return snapshot, time.perf_counter() - started


def assert_das_identity(snapshot):
    rules = RuleProfile.from_json(snapshot.rules_json)
    if (rules.profile_id != DAS_PROFILE or snapshot.engine_version != DAS_ENGINE
            or snapshot.strategy_version != DAS_STRATEGY):
        raise ValueError("DAS identity mismatch")


def nearest_rank_p95(times):
    if not times:
        return None
    ordered = sorted(times)
    return ordered[math.ceil(0.95 * len(ordered)) - 1]


def build_receipt(spec, rows, identity, source_before, source_after, preparation, binary_sha256, extra_errors=()):
    errors = list(spec.get("_load_errors") or [])
    errors.extend(extra_errors)
    ids = [row["name"] for row in rows]
    if not rows:
        errors.append("empty_matrix")
    if len(ids) != len(set(ids)):
        errors.append("duplicate_id")
    expected = [case["id"] for case in spec.get("cases") or []]
    if expected and (set(ids) != set(expected) or len(ids) != spec.get("count", len(expected))):
        errors.append("case_mismatch")
    if any(row.get("engine_version") != DAS_ENGINE or row.get("strategy_version") != DAS_STRATEGY
           or row.get("profile_id") != DAS_PROFILE for row in rows):
        errors.append("wrong_engine")
    if source_before != source_after:
        errors.append("source_changed")
    times = [row["wall_seconds"] for row in rows]
    p95 = nearest_rank_p95(times)
    by_decks = {}
    for row in rows:
        by_decks.setdefault(row["n_decks"], []).append(row["wall_seconds"])
    p95_by_decks = {str(decks): nearest_rank_p95(values) for decks, values in sorted(by_decks.items())}
    statuses = {status: sum(row["status"] == status for row in rows) for status in sorted({row["status"] for row in rows})}
    all_completed = bool(rows) and all(row["status"] == "available" for row in rows)
    timed_out = sum(row["status"] == "timeout" for row in rows)
    failed = sum(row["status"] not in ("available", "timeout") for row in rows)
    target_met = p95 is not None and p95 <= spec.get("target_p95_seconds", P95_TARGET_SECONDS)
    decks_met = all(value is not None and value <= spec.get("target_p95_seconds", P95_TARGET_SECONDS)
                    for value in p95_by_decks.values()) if p95_by_decks else False
    unique_errors = list(dict.fromkeys(errors))
    receipt = dict(
        schema=SCHEMA,
        timestamp_utc=datetime.now(timezone.utc).isoformat(),
        identity=identity,
        python=sys.version,
        platform=platform.platform(),
        source_manifest=source_before,
        source_unchanged=source_before == source_after,
        spec_path=SPEC_PATH.relative_to(ROOT).as_posix(),
        spec_sha256=hashlib.sha256(SPEC_PATH.read_bytes()).hexdigest() if SPEC_PATH.exists() else None,
        cases=rows,
        count=len(rows),
        expected_count=spec.get("count"),
        p50_seconds=statistics.median(times) if times else None,
        p95_seconds=p95,
        max_seconds=max(times) if times else None,
        p95_method="nearest rank",
        p95_by_decks=p95_by_decks,
        target_p95_seconds=spec.get("target_p95_seconds", P95_TARGET_SECONDS),
        hard_budget_seconds=spec.get("budget_seconds", HARD_BUDGET_SECONDS),
        preparation_seconds=preparation,
        binary_sha256=binary_sha256,
        statuses=statuses,
        completed=sum(row["status"] == "available" for row in rows),
        failed=failed,
        timed_out=timed_out,
        all_completed=all_completed,
        target_met=bool(target_met and decks_met),
        gate_errors=unique_errors,
        peak_combined_process_bytes=max(
            ((row.get("peak_native_bytes") or 0) + (row.get("peak_python_bytes") or 0) for row in rows),
            default=0),
        scope=("Synthetic DAS finite shared-shoe requests; artifact reuse only; "
               "no probability/policy cache between requests; no user data"),
    )
    receipt["passed"] = (not unique_errors and all_completed and receipt["target_met"]
                         and receipt["source_unchanged"] and receipt["count"] == spec.get("count"))
    return receipt


def run_matrix(output, spec):
    before = source_manifest()
    identity = source_identity(ROOT)
    start = time.perf_counter()
    executable = build_native()
    preparation = time.perf_counter() - start
    rows = []
    extra = list(spec.get("_load_errors") or [])
    try:
        for case in spec["cases"]:
            snapshot, construction = construct_case(case)
            service = AnalysisService()
            started = time.perf_counter()
            try:
                service.start(snapshot, spec.get("budget_seconds", HARD_BUDGET_SECONDS))
                while True:
                    result = service.poll()
                    if result is not None:
                        break
                    time.sleep(0.003)
                elapsed = time.perf_counter() - started
                row = dict(
                    name=case["id"], group=case["group"], n_decks=case["decks"],
                    status=result["status"], wall_seconds=elapsed,
                    construction_seconds=construction,
                    compute_seconds=result.get("elapsed_seconds"),
                    input_digest=snapshot.input_digest,
                    engine_version=snapshot.engine_version,
                    strategy_version=snapshot.strategy_version,
                    profile_id=json.loads(snapshot.rules_json)["profile_id"],
                    reason=result.get("reason"),
                    peak_native_bytes=result.get("peak_memory"),
                    peak_python_bytes=result.get("worker_peak_working_set_bytes"),
                    result_file=case["id"] + ".json",
                )
                (output / row["result_file"]).write_text(
                    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            finally:
                service.close()
            rows.append(row)
            with (output / "progress.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                stream.flush()
            if len(rows) % 10 == 0 or result["status"] != "available":
                print(f"{len(rows)}/{spec['count']} {case['id']}: {result['status']} {elapsed:.3f}s", flush=True)
    except Exception as error:
        extra.append(type(error).__name__ + ": " + str(error))
        print("matrix aborted: " + str(error), flush=True)
    after = source_manifest()
    receipt = build_receipt(spec, rows, identity, before, after, preparation,
                            hashlib.sha256(executable.read_bytes()).hexdigest(), extra)
    (output / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {key: receipt[key] for key in (
        "count", "completed", "failed", "timed_out", "p50_seconds", "p95_seconds",
        "max_seconds", "p95_by_decks", "statuses", "gate_errors", "passed")}
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    print(output, flush=True)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--spec", type=Path, default=SPEC_PATH)
    args = parser.parse_args(argv)
    output = args.output or ROOT / ".local-evidence" / (
        "das-cold-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6])
    if output.exists():
        raise SystemExit("输出目录已存在，拒绝覆盖: " + str(output))
    output.mkdir(parents=True, exist_ok=False)
    spec = load_spec(args.spec)
    receipt = run_matrix(output, spec)
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
