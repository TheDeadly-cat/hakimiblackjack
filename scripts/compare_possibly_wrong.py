"""Live numeric compare against pinned possibly-wrong/blackjack strategy.exe.

Does not vendor GPL sources. Split/DAS cells are recorded and never gated.
Dealer rows use the five-decimal table possibly-wrong writes; action EVs use
the interactive query output.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from blackjack_lab.analysis.actions import solve_counts
from blackjack_lab.analysis.external_pw import (
    DEALER_LABELS, default_strategy_exe, hakimi_dealer_counts_from_pw_shoe,
    hakimi_remaining_from_pw_shoe, load_spec, max_abs_dealer_error,
    parse_dealer_table, parse_ev_blocks, peek_negative, pw_stdin,
    renormalize_pw_dealer_after_peek, verify_strategy_exe,
)
from blackjack_lab.analysis.native_backend import build_native, solve_presplit_native, source_digest
from blackjack_lab.analysis.probability import FiniteModel
from scripts.source_identity import source_identity

SPEC = ROOT / "fixtures" / "v02b2" / "external_pw_cases.json"
SCHEMA = "hakimi-external-pw-compare-v1"


def hakimi_dealer(counts, up, peek, budget=5.0):
    model = FiniteModel(up, peek, budget_seconds=budget)
    values = model.dealer_distribution(tuple(counts))
    return dict(zip(DEALER_LABELS, values))


def action_map(result):
    return {name: float(item["ev"]) for name, item in result.get("actions", {}).items()}


def compare_pair(ours, theirs, names, tolerance):
    errors = {}
    worst = 0.0
    failed = []
    for name in names:
        if name not in ours or name not in theirs:
            errors[name] = None
            failed.append(name)
            continue
        delta = abs(ours[name] - theirs[name])
        errors[name] = delta
        if delta > worst:
            worst = delta
        if delta > tolerance:
            failed.append(name)
    return errors, worst, failed


def classify_ev_mismatch(case, failed, ours, theirs):
    if not failed:
        return "match"
    if case.get("incomparable_actions") and set(failed) <= set(case["incomparable_actions"]):
        return "not_directly_comparable"
    return "mismatch_needs_classification"


def run_strategy(exe, spec, shoe, queries, work_dir, timeout):
    work_dir.mkdir(parents=True, exist_ok=True)
    table_name = "pw-table.txt"
    stdin = pw_stdin(spec, shoe, queries, table_name)
    (work_dir / "stdin.txt").write_text(stdin, encoding="utf-8")
    started = time.perf_counter()
    proc = subprocess.run(
        [str(exe)],
        input=stdin,
        cwd=str(work_dir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    elapsed = time.perf_counter() - started
    stdout_path = work_dir / "stdout.txt"
    stderr_path = work_dir / "stderr.txt"
    stdout_path.write_text(proc.stdout, encoding="utf-8")
    stderr_path.write_text(proc.stderr, encoding="utf-8")
    table_path = work_dir / table_name
    table_text = table_path.read_text(encoding="utf-8") if table_path.is_file() else ""
    if proc.returncode:
        raise RuntimeError("strategy.exe exit %s after %.1fs" % (proc.returncode, elapsed))
    if not table_text:
        raise RuntimeError("strategy.exe 没有写出庄家表")
    return {
        "elapsed_seconds": elapsed,
        "dealer": parse_dealer_table(table_text),
        "ev_blocks": parse_ev_blocks(proc.stdout),
        "table_text": table_text,
        "stdout_path": str(stdout_path),
        "table_path": str(table_path),
        "header": table_text.splitlines()[0] if table_text else "",
    }


def evaluate_dealer(case, pw_row, spec):
    counts = hakimi_dealer_counts_from_pw_shoe(case["pw_shoe"], case["up"])
    tol = spec["tolerances"]["dealer_raw_abs" if case["mode"] == "raw" else "dealer_peek_abs"]
    ours_raw = hakimi_dealer(counts, case["up"], False)
    ours_peek = hakimi_dealer(counts, case["up"], True) if peek_negative(case["up"]) else ours_raw
    if case["mode"] == "raw":
        ours = ours_raw
        theirs = pw_row
        conversion = "pw shoe still contains the upcard; Hakimi dealer counts drop only that upcard; peek_negative=false so blackjack remains"
    else:
        ours = ours_peek
        theirs = renormalize_pw_dealer_after_peek(pw_row)
        conversion = "same shoe conversion; PW blackjack mass removed and remaining outcomes scaled by 1/(1-P(BJ)); Hakimi peek_negative=true"
    error = max_abs_dealer_error(ours, theirs)
    passed = error <= tol
    return {
        "id": case["id"],
        "group": case["group"],
        "kind": case["kind"],
        "comparable": True,
        "incomparable_reason": "",
        "classification": "match" if passed else "mismatch_needs_classification",
        "shoe_id": case["shoe_id"],
        "up": case["up"],
        "pw_shoe": case["pw_shoe"],
        "hakimi_counts": counts,
        "conversion": conversion,
        "pw_result": theirs,
        "pw_raw": pw_row,
        "hakimi_python": ours,
        "error_abs": error,
        "tolerance": tol,
        "gate": passed,
        "note": case.get("note", ""),
    }


def evaluate_ev(case, pw_block, spec):
    counts = hakimi_remaining_from_pw_shoe(case["pw_shoe"], case["player"], case["up"])
    peek = peek_negative(case["up"])
    comparable = [name for name, flag in case["actions"].items() if flag == "comparable"]
    python = action_map(solve_counts(counts, case["player"], case["up"], peek, tuple(comparable)))
    native = action_map(solve_presplit_native(counts, case["player"], case["up"], peek, tuple(comparable)))
    recorded = [name for name in case["actions"] if name in pw_block or name in native or name in python]
    internal_errors, internal_worst, internal_failed = compare_pair(
        native, python, [name for name in comparable if name in native and name in python],
        spec["tolerances"]["internal_abs"])
    gated = [name for name in comparable if name in pw_block]
    missing = [name for name in comparable if name not in pw_block]
    ev_errors, ev_worst, ev_failed = compare_pair(native, pw_block, gated, spec["tolerances"]["ev_abs"])
    split_error = None
    if "split" in case.get("incomparable_actions", []) and "split" in pw_block:
        ours_split = native.get("split")
        split_error = None if ours_split is None else abs(ours_split - pw_block["split"])
    passed = not internal_failed and not ev_failed and not missing
    classification = "match"
    if internal_failed:
        classification = "internal_divergence"
    elif missing:
        classification = "pw_missing_comparable_action"
    elif ev_failed:
        classification = classify_ev_mismatch(case, ev_failed, native, pw_block)
    return {
        "id": case["id"],
        "group": case["group"],
        "kind": case["kind"],
        "comparable": True,
        "incomparable_actions": case.get("incomparable_actions") or [],
        "incomparable_reason": case.get("incomparable_reason", ""),
        "classification": classification,
        "shoe_id": case["shoe_id"],
        "up": case["up"],
        "player": list(case["player"]),
        "pw_shoe": case["pw_shoe"],
        "hakimi_counts": counts,
        "peek_negative": peek,
        "conversion": "PW BJPlayer shoe includes player cards and dealer upcard; Hakimi remaining excludes both and keeps the hole",
        "pw_result": {name: pw_block.get(name) for name in recorded},
        "hakimi_native": native,
        "hakimi_python": python,
        "error_abs": {name: ev_errors.get(name) for name in comparable},
        "internal_error_abs": internal_errors,
        "split_error_abs_not_gated": split_error,
        "tolerance": spec["tolerances"]["ev_abs"],
        "gate": passed,
        "failed_actions": ev_failed + (["internal:" + name for name in internal_failed]),
        "missing_actions": missing,
        "note": case.get("note", ""),
    }


def build_receipt(spec, identity, exe_digest, shoe_runs, rows, elapsed):
    comparable_rows = [row for row in rows if row.get("gate") is not None]
    gated = [row for row in comparable_rows if not (
        row["kind"] == "unsplit_ev" and row.get("incomparable_actions") and row["classification"] == "not_directly_comparable"
    )]
    failed = [row for row in gated if not row["gate"]]
    ev_errors = []
    dealer_errors = []
    for row in rows:
        if row["kind"] == "dealer_dist":
            dealer_errors.append(row["error_abs"])
        else:
            ev_errors.extend(value for value in row["error_abs"].values() if type(value) is float)
    return {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": elapsed,
        "pin": spec["engine"],
        "rules": spec["rules"],
        "adapter_notes": spec["adapter_notes"],
        "tolerances": spec["tolerances"],
        "ours": {
            "identity": identity,
            "split_engine_sha256": source_digest(),
        },
        "strategy_exe_sha256": exe_digest,
        "shoe_runs": shoe_runs,
        "cases": rows,
        "summary": {
            "total": len(rows),
            "gated": len(gated),
            "passed": len(gated) - len(failed),
            "failed": len(failed),
            "failed_ids": [row["id"] for row in failed],
            "max_abs_error_ev": max(ev_errors) if ev_errors else None,
            "max_abs_error_dealer": max(dealer_errors) if dealer_errors else None,
            "gate_passed": not failed,
        },
    }


def write_summary(path, receipt):
    lines = [
        "Hakimi vs possibly-wrong/blackjack v7.6",
        "source_commit=" + receipt["pin"]["source_commit"],
        "strategy_exe_sha256=" + receipt["strategy_exe_sha256"],
        "ours=" + str(receipt["ours"]["identity"].get("commit")),
        "split_engine_sha256=" + receipt["ours"]["split_engine_sha256"],
        "gate_passed=" + str(receipt["summary"]["gate_passed"]),
        "passed=%s failed=%s total=%s" % (
            receipt["summary"]["passed"], receipt["summary"]["failed"], receipt["summary"]["total"]),
        "max_abs_error_ev=" + str(receipt["summary"]["max_abs_error_ev"]),
        "max_abs_error_dealer=" + str(receipt["summary"]["max_abs_error_dealer"]),
        "",
    ]
    if receipt["summary"]["failed_ids"]:
        lines.append("FAILED:")
        lines.extend(receipt["summary"]["failed_ids"])
        lines.append("")
    for row in receipt["cases"]:
        if row["kind"] == "dealer_dist":
            lines.append("%s  err=%.3e  %s" % (row["id"], row["error_abs"], row["classification"]))
        else:
            worst = max((v for v in row["error_abs"].values() if type(v) is float), default=0.0)
            lines.append("%s  ev_err=%.3e  %s" % (row["id"], worst, row["classification"]))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Compare Hakimi unsplit math with possibly-wrong strategy.exe")
    parser.add_argument("--spec", type=Path, default=SPEC)
    parser.add_argument("--exe", type=Path, default=None)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    args = parser.parse_args(argv)
    output = args.output.resolve()
    if output.exists():
        raise SystemExit("输出目录已存在: " + str(output))
    spec = load_spec(args.spec)
    if spec["_load_errors"]:
        raise SystemExit("案例夹具无效: " + ",".join(spec["_load_errors"]))
    exe = Path(args.exe) if args.exe else default_strategy_exe(ROOT)
    if not exe.is_file():
        raise SystemExit("缺少 strategy.exe。先运行 python scripts/fetch_possibly_wrong.py")
    exe_digest = verify_strategy_exe(exe, spec)
    identity = source_identity(ROOT)
    output.mkdir(parents=True)
    started = time.perf_counter()
    print("prepare native accelerator")
    build_native()
    shoes = {item["id"]: item for item in spec["shoes"]}
    cases = spec["_cases"]
    queries_by_shoe = defaultdict(list)
    for case in cases:
        if case["kind"] == "unsplit_ev":
            queries_by_shoe[case["shoe_id"]].append((case["up"], case["player"]))
    shoe_runs = {}
    parsed = {}
    for shoe_id, shoe in shoes.items():
        queries = queries_by_shoe[shoe_id]
        print("strategy.exe", shoe_id, "queries", len(queries))
        parsed[shoe_id] = run_strategy(
            exe, spec, shoe, queries, output / "pw-run" / shoe_id, args.timeout_seconds)
        shoe_runs[shoe_id] = {
            "elapsed_seconds": parsed[shoe_id]["elapsed_seconds"],
            "header": parsed[shoe_id]["header"],
            "query_count": len(queries),
            "dealer_upcards": sorted(parsed[shoe_id]["dealer"]),
        }
        print("  done in %.1fs header=%s" % (parsed[shoe_id]["elapsed_seconds"], parsed[shoe_id]["header"]))
    rows = []
    ev_index = defaultdict(int)
    for case in cases:
        run = parsed[case["shoe_id"]]
        if case["kind"] == "dealer_dist":
            pw_row = run["dealer"].get(case["up"])
            if pw_row is None:
                rows.append({
                    "id": case["id"], "group": case["group"], "kind": case["kind"],
                    "classification": "pw_missing_dealer_row", "gate": False,
                    "error_abs": None, "up": case["up"], "shoe_id": case["shoe_id"],
                })
                continue
            rows.append(evaluate_dealer(case, pw_row, spec))
            continue
        blocks = run["ev_blocks"]
        idx = ev_index[case["shoe_id"]]
        ev_index[case["shoe_id"]] += 1
        if idx >= len(blocks):
            rows.append({
                "id": case["id"], "group": case["group"], "kind": case["kind"],
                "classification": "pw_missing_ev_block", "gate": False,
                "error_abs": {}, "shoe_id": case["shoe_id"],
            })
            continue
        rows.append(evaluate_ev(case, blocks[idx], spec))
    receipt = build_receipt(spec, identity, exe_digest, shoe_runs, rows, time.perf_counter() - started)
    (output / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_summary(output / "summary.txt", receipt)
    print("gate_passed=%s failed=%s max_ev=%s max_dealer=%s" % (
        receipt["summary"]["gate_passed"], receipt["summary"]["failed"],
        receipt["summary"]["max_abs_error_ev"], receipt["summary"]["max_abs_error_dealer"]))
    print("receipt", output / "receipt.json")
    return 0 if receipt["summary"]["gate_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
