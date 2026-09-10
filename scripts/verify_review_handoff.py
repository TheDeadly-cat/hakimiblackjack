"""Execute the original review's unmodified tests against this full checkout."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
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
        if not path.is_relative_to(review.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
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
                "output": log.name, "output_sha256": hashlib.sha256(log.read_bytes()).hexdigest()}
        count = re.search(r"Ran (\d+) tests", result.stdout)
        if count:
            item["test_count"] = int(count.group(1))
        checks.append(item)
        print(f"{name}: exit={result.returncode}", flush=True)
        if result.returncode:
            print(result.stdout, flush=True)

    run("original-math", ["-m", "unittest", "tests.test_analysis_math", "-v"])
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
    run("original-handoff", ["-m", "unittest", "discover", "-s", str(review),
                             "-p", "test_v02a_review_regressions.py", "-v"])
    original = json.loads((review / "original-supplementary-results.json").read_text(encoding="utf-8"))
    result_path = supplement_dir / "supplementary-math-rerun.json"
    result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
    fields = ("cards", "hand", "up", "peek")
    same_scenarios = ([[s[k] for k in fields] for s in result.get("scenarios", [])]
                      == [[s[k] for k in fields] for s in original["scenarios"]])
    passed = (all(c["exit_code"] == 0 for c in checks)
              and checks[0].get("test_count") == 5 and checks[2].get("test_count") == 4
              and same_scenarios and result.get("scenario_count") == result.get("passed_count") == 48
              and result.get("max_abs_error", float("inf")) <= 1e-10 and before == source_manifest())
    receipt = {"schema": "hakimi-original-review-handoff-v1", "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "source_identity": identity, "source_manifest": before, "review_provenance": provenance,
        "original_scripts_unchanged": True, "same_48_inputs_as_original": same_scenarios,
        "original_max_abs_error": original["max_abs_error"], "current_max_abs_error": result.get("max_abs_error"),
        "checks": checks, "source_unchanged_during_checks": before == source_manifest(), "passed": passed,
        "coverage_note": "5 math methods overlap the core suite; 48 are scenarios, not 48 upstream test methods; 4 original handoff methods run separately."}
    (output / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "passed": passed, "max_abs_error": result.get("max_abs_error")}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
