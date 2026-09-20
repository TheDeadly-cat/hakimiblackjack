"""Execute the original review's unmodified tests against this full checkout."""
import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.source_identity import source_identity
from scripts.verify_release import source_manifest

REQUIRED_MATH_METHODS = (
    "test_small_physical_worlds_match_all_outcomes",
    "test_peek_changes_both_hole_and_next_card_distribution",
    "test_hidden_card_oracle_cannot_inflate_continuation_ev",
    "test_three_deck_counts_actually_change_probabilities_and_ev",
    "test_hit_includes_repeated_decisions_not_forced_stand",
)
REQUIRED_HANDOFF_METHODS = (
    "test_missing_initial_card_in_unsettled_round_blocks_next_round_analysis",
    "test_committed_input_invalidates_inflight_request_even_if_render_fails",
    "test_committed_input_removes_displayed_result_even_if_render_fails",
    "test_cancel_clears_scheduled_auto_request",
)
MATH_SOURCE = ROOT / "tests" / "test_analysis_math.py"
HANDOFF_SOURCE = ROOT / "review_tests" / "test_v02a_review_regressions.py"
ARTIFACT_WHITELIST = (
    "receipt.json",
    "artifact-manifest.json",
    "original-math.txt",
    "original-48-scenarios.txt",
    "original-handoff.txt",
    "supplementary/supplementary-math-rerun.json",
)
UNITTEST_METHOD = re.compile(
    r"^(test_\w+) \([^)]+\) \.\.\. (ok|FAIL|ERROR|skipped(?: .+)?)\s*$",
    re.M,
)
MAX_ABS_ERROR = 1e-10


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def declared_test_method_names(path):
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    names = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name.startswith("test_"):
                    names.append(item.name)
    return frozenset(names)


def parse_unittest_methods(output):
    results = []
    for name, raw in UNITTEST_METHOD.findall(output or ""):
        status = "skipped" if raw.startswith("skipped") else raw
        results.append({"name": name, "status": status, "detail": raw})
    return results


def required_method_failures(prefix, required, parsed, declared):
    status_by_name = {item["name"]: item["status"] for item in parsed}
    failed = []
    for name in required:
        if name not in declared:
            failed.append(f"{prefix}_missing:{name}")
        elif name not in status_by_name:
            failed.append(f"{prefix}_not_run:{name}")
        elif status_by_name[name] != "ok":
            failed.append(f"{prefix}_not_passed:{name}:{status_by_name[name]}")
    additional = [item["name"] for item in parsed if item["name"] not in required]
    passed = [name for name in required if status_by_name.get(name) == "ok" and name in declared]
    return failed, additional, passed


def error_within_tolerance(value, limit=MAX_ABS_ERROR):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number <= limit


def evaluate_handoff_conditions(checks, same_scenarios, result, source_unchanged,
                                math_failures, handoff_failures):
    failed = []
    by_name = {item.get("name"): item for item in checks}
    if by_name.get("original-math", {}).get("exit_code") != 0:
        failed.append("original_math_exit_0")
    if by_name.get("original-48-scenarios", {}).get("exit_code") != 0:
        failed.append("original_48_scenarios_exit_0")
    if by_name.get("original-handoff", {}).get("exit_code") != 0:
        failed.append("original_handoff_exit_0")
    failed.extend(math_failures)
    failed.extend(handoff_failures)
    if same_scenarios is not True:
        failed.append("same_48_inputs_as_original")
    if result.get("scenario_count") != 48:
        failed.append("scenario_count_48")
    if result.get("passed_count") != 48:
        failed.append("passed_count_48")
    if not error_within_tolerance(result.get("max_abs_error", float("inf"))):
        failed.append("max_abs_error_at_most_1e-10")
    if source_unchanged is not True:
        failed.append("source_unchanged_during_checks")
    return failed


def write_artifact_manifest(output):
    files = []
    for relative in ARTIFACT_WHITELIST:
        if relative == "artifact-manifest.json":
            continue
        path = output / relative
        if path.is_file() and path.resolve().is_relative_to(output.resolve()):
            files.append({
                "path": relative,
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            })
    manifest = {
        "schema": "hakimi-review-handoff-artifacts-v1",
        "whitelist": list(ARTIFACT_WHITELIST),
        "files": files,
        "note": "Synthetic receipts and logs only. Do not upload .local-evidence or private media.",
    }
    path = output / "artifact-manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = (args.output or ROOT / ".local-evidence" / (
        "review-handoff-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6])).resolve()
    output.mkdir(parents=True, exist_ok=False)
    identity, before = source_identity(ROOT), source_manifest()
    review = ROOT / "review_tests"
    provenance = json.loads((review / "source-provenance.json").read_text(encoding="utf-8"))
    for name, expected in provenance["files"].items():
        path = (review / name).resolve()
        if not path.is_relative_to(review.resolve()) or sha256_file(path) != expected:
            raise ValueError("Original review material changed: " + name)
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONPATH=str(ROOT))
    checks = []

    def run(name, arguments, cwd=ROOT):
        command = [sys.executable, *arguments]
        started = time.perf_counter()
        result = subprocess.run(command, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, encoding="utf-8", timeout=60)
        log = output / (name + ".txt")
        log.write_text(result.stdout, encoding="utf-8")
        item = {"name": name, "command": command, "cwd": str(cwd), "exit_code": result.returncode,
                "elapsed_seconds": time.perf_counter() - started,
                "output": log.name, "output_sha256": sha256_file(log)}
        count = re.search(r"Ran (\d+) tests", result.stdout)
        if count:
            item["test_count"] = int(count.group(1))
        checks.append(item)
        print(f"{name}: exit={result.returncode}", flush=True)
        if result.returncode:
            print(result.stdout, flush=True)
        return result.stdout

    math_output = run("original-math", ["-m", "unittest", "tests.test_analysis_math", "-v"])
    # run_path on a FILE keeps the current checkout on sys.path. The working
    # directory is fresh, so the original script cannot overwrite its old JSON.
    supplement_dir = output / "supplementary"
    supplement_dir.mkdir()
    code = ("import json,runpy,sys; from pathlib import Path; "
            "import blackjack_lab.analysis.actions as a; import tests.analysis_reference as r; "
            "root=Path(sys.argv[2]).resolve(); "
            "assert Path(a.__file__).resolve()==root/'blackjack_lab/analysis/actions.py'; "
            "assert Path(r.__file__).resolve()==root/'tests/analysis_reference.py'; "
            "print(json.dumps({'production_module':a.__file__,'reference_module':r.__file__})); "
            "runpy.run_path(sys.argv[1],run_name='__main__')")
    run("original-48-scenarios", ["-c", code, str(review / "run_supplementary_math.py"), str(ROOT)], supplement_dir)
    handoff_output = run("original-handoff", ["-m", "unittest", "discover", "-s", str(review),
                             "-p", "test_v02a_review_regressions.py", "-v"])
    original = json.loads((review / "original-supplementary-results.json").read_text(encoding="utf-8"))
    result_path = supplement_dir / "supplementary-math-rerun.json"
    result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
    fields = ("cards", "hand", "up", "peek")
    same_scenarios = ([[s[k] for k in fields] for s in result.get("scenarios", [])]
                      == [[s[k] for k in fields] for s in original["scenarios"]])
    math_failures, additional_math, math_passed = required_method_failures(
        "required_math_method", REQUIRED_MATH_METHODS, parse_unittest_methods(math_output),
        declared_test_method_names(MATH_SOURCE))
    handoff_failures, additional_handoff, handoff_passed = required_method_failures(
        "required_handoff_method", REQUIRED_HANDOFF_METHODS, parse_unittest_methods(handoff_output),
        declared_test_method_names(HANDOFF_SOURCE))
    source_unchanged = before == source_manifest()
    failed_conditions = evaluate_handoff_conditions(
        checks, same_scenarios, result, source_unchanged, math_failures, handoff_failures)
    passed = not failed_conditions
    receipt = {
        "schema": "hakimi-original-review-handoff-v1",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "source_identity": identity,
        "source_manifest": before,
        "review_provenance": provenance,
        "original_scripts_unchanged": True,
        "same_48_inputs_as_original": same_scenarios,
        "original_max_abs_error": original["max_abs_error"],
        "current_max_abs_error": result.get("max_abs_error"),
        "checks": checks,
        "source_unchanged_during_checks": source_unchanged,
        "passed": passed,
        "failed_conditions": failed_conditions,
        "required_math_methods": list(REQUIRED_MATH_METHODS),
        "required_handoff_methods": list(REQUIRED_HANDOFF_METHODS),
        "required_math_passed": math_passed,
        "required_handoff_passed": handoff_passed,
        "additional_math_methods": additional_math,
        "additional_handoff_methods": additional_handoff,
        "coverage_note": (
            "Original 5 math methods must still exist, run, and pass; extra math methods are "
            "allowed and counted separately. 48 are scenarios, not 48 upstream test methods. "
            "Original 4 handoff methods run separately and must exist, run, and pass."
        ),
    }
    (output / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    write_artifact_manifest(output)
    print(json.dumps({"output": str(output), "passed": passed,
                      "max_abs_error": result.get("max_abs_error"),
                      "failed_conditions": failed_conditions}), flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
