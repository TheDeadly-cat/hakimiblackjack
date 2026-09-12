"""Recompute DAS 336 attachment gates. Never treat receipt.passed as proof."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from blackjack_lab.analysis.split_contracts import VALUES
from blackjack_lab.analysis.split_service import _validate_hit_bust
from blackjack_lab.storage.analysis_snapshots import SnapshotFormatError, _validate_result
from scripts.das_benchmarks import SCHEMA, SPEC_PATH, build_receipt, construct_case, load_spec

ROOT = Path(__file__).resolve().parents[1]

NUMERIC_SCOPE = (
    "blackjack_lab/analysis/native/SplitEngine.cs",
    "blackjack_lab/analysis/native_backend.py",
    "blackjack_lab/analysis/split_contracts.py",
    "blackjack_lab/analysis/split_service.py",
    "blackjack_lab/analysis/split_information.py",
    "blackjack_lab/analysis/split_actions.py",
    "blackjack_lab/analysis/service.py",
    "blackjack_lab/analysis/information.py",
    "blackjack_lab/analysis/actions.py",
    "blackjack_lab/analysis/probability.py",
    "blackjack_lab/analysis/contracts.py",
    "blackjack_lab/core/rules.py",
    "blackjack_lab/core/table.py",
    "fixtures/v02b2/das_benchmark_spec.json",
    "scripts/das_benchmarks.py",
    "tests/test_analysis_integration.py",
)


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def numeric_manifest(root=None):
    root = Path(root or ROOT)
    manifest = {}
    for relative in NUMERIC_SCOPE:
        path = root / relative
        if path.is_file():
            manifest[relative] = sha256_file(path)
    return manifest


def _add_once(errors, name):
    if name not in errors:
        errors.append(name)


def _illegal_ev(item):
    ev = item.get("ev")
    return type(ev) in (int, float) and type(ev) is not bool and math.isfinite(ev) and abs(ev) > 4 + 1e-12


def _bind_available_result(payload, case, expected, errors):
    info = payload.get("input") if type(payload.get("input")) is dict else {}
    dealer = VALUES.get(str(case.get("dealer_up") or "").upper())
    peek = bool(case.get("negative_peek"))
    mismatched = (
        info.get("n_decks") != case.get("decks")
        or info.get("dealer_up") != dealer
        or info.get("peek_negative") != peek
        or tuple(info.get("counts") or ()) != tuple(expected.counts)
        or list(info.get("legal_actions") or []) != list(expected.legal_actions)
        or payload.get("engine_version") != expected.engine_version
        or payload.get("strategy_version") != expected.strategy_version
    )
    if mismatched:
        _add_once(errors, "result_input_mismatch")


def validate_attached_matrix(directory, spec=None, current_numeric=None, root=None):
    """Return recomputed stats. receipt['passed'] is recorded and never used as the verdict."""
    directory = Path(directory)
    spec = spec if spec is not None else load_spec()
    current_numeric = dict(current_numeric if current_numeric is not None else numeric_manifest(root))
    errors = []
    claimed = None
    receipt = None
    rows = []
    receipt_path = directory / "receipt.json"
    if not receipt_path.is_file():
        _add_once(errors, "missing_receipt")
    else:
        try:
            loaded = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            loaded = None
            _add_once(errors, "invalid_receipt_json")
        if loaded is not None and type(loaded) is not dict:
            _add_once(errors, "invalid_receipt_json")
            loaded = None
        receipt = loaded
    if receipt is not None:
        claimed = receipt.get("passed")
        if receipt.get("schema") != SCHEMA:
            _add_once(errors, "wrong_schema")
        if type(receipt.get("cases")) is list:
            rows = receipt["cases"]
        else:
            _add_once(errors, "missing_cases")
        stored = receipt.get("source_manifest")
        if type(stored) is not dict or not stored:
            _add_once(errors, "missing_source_manifest")
        else:
            for relative, digest in current_numeric.items():
                if stored.get(relative) != digest:
                    _add_once(errors, "source_mismatch")
                    break
        spec_sha = receipt.get("spec_sha256")
        if SPEC_PATH.is_file():
            expected_spec = sha256_file(SPEC_PATH)
            if spec_sha and spec_sha != expected_spec:
                _add_once(errors, "spec_mismatch")
    if spec.get("_load_errors"):
        for item in spec["_load_errors"]:
            _add_once(errors, item)

    engine_digest = current_numeric.get("blackjack_lab/analysis/native/SplitEngine.cs")
    missing_result = status_mismatch = bad_timing = False
    base = directory.resolve()
    spec_cases = {case["id"]: case for case in spec.get("cases") or [] if type(case) is dict and case.get("id")}
    seen_names = []
    seen_files = {}
    expected_cache = {}
    for row in rows:
        if type(row) is not dict:
            _add_once(errors, "invalid_row")
            continue
        wall = row.get("wall_seconds")
        if type(wall) not in (int, float) or isinstance(wall, bool) or not math.isfinite(wall) or wall < 0:
            bad_timing = True
        name = row.get("name")
        if type(name) is not str or not name:
            _add_once(errors, "case_identity_mismatch")
        elif name in seen_names:
            _add_once(errors, "duplicate_case")
        else:
            seen_names.append(name)
            if name not in spec_cases:
                _add_once(errors, "case_identity_mismatch")
        result_name = row.get("result_file")
        if type(result_name) is not str or not result_name or Path(result_name).name != result_name:
            missing_result = True
            continue
        path = directory / result_name
        try:
            resolved = path.resolve()
        except OSError:
            missing_result = True
            continue
        if not path.is_file() or not resolved.is_relative_to(base):
            missing_result = True
            continue
        if resolved in seen_files:
            _add_once(errors, "shared_result_file")
        else:
            seen_files[resolved] = name
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            missing_result = True
            continue
        if type(payload) is not dict:
            missing_result = True
            continue
        if payload.get("status") != row.get("status"):
            status_mismatch = True
        if row.get("status") == "available":
            actions = payload.get("actions") if type(payload.get("actions")) is dict else {}
            probabilities = payload.get("probabilities") if type(payload.get("probabilities")) is dict else {}
            if "hit_bust" in probabilities:
                try:
                    _validate_hit_bust(probabilities.get("hit_bust"))
                except ArithmeticError:
                    _add_once(errors, "illegal_result_values")
            for item in actions.values():
                if type(item) is dict and item.get("status") == "available" and _illegal_ev(item):
                    _add_once(errors, "illegal_result_values")
                    break
            digest = payload.get("backend_source_sha256")
            if type(digest) is not str or not digest:
                _add_once(errors, "missing_result_source")
            elif engine_digest and digest != engine_digest:
                _add_once(errors, "result_source_mismatch")
            case = spec_cases.get(name)
            if case is None:
                continue
            try:
                _validate_result(payload)
            except SnapshotFormatError:
                _add_once(errors, "incomplete_result")
                continue
            if name not in expected_cache:
                try:
                    expected_cache[name] = construct_case(case)[0]
                except Exception:
                    _add_once(errors, "case_identity_mismatch")
                    continue
            _bind_available_result(payload, case, expected_cache[name], errors)
        else:
            decks = (payload.get("input") or {}).get("n_decks") if type(payload.get("input")) is dict else None
            if decks is not None and decks != row.get("n_decks"):
                _add_once(errors, "result_input_mismatch")
    if spec_cases and set(seen_names) != set(spec_cases):
        _add_once(errors, "case_identity_mismatch")
    if missing_result:
        _add_once(errors, "missing_result_file")
    if status_mismatch:
        _add_once(errors, "result_status_mismatch")
    if bad_timing:
        _add_once(errors, "invalid_timing")

    usable = [row for row in rows if type(row) is dict]
    identity = (receipt or {}).get("identity") or {}
    stored_manifest = (receipt or {}).get("source_manifest") if type((receipt or {}).get("source_manifest")) is dict else {}
    binary = (receipt or {}).get("binary_sha256") or ""
    recomputed = build_receipt(spec, usable, identity, stored_manifest, stored_manifest, 0.0, binary,
                               extra_errors=errors)
    return {
        "passed": recomputed["passed"],
        "errors": recomputed["gate_errors"],
        "claimed_passed": claimed,
        "count": recomputed["count"],
        "completed": recomputed["completed"],
        "failed": recomputed["failed"],
        "timed_out": recomputed["timed_out"],
        "p50_seconds": recomputed["p50_seconds"],
        "p95_seconds": recomputed["p95_seconds"],
        "max_seconds": recomputed["max_seconds"],
        "statuses": recomputed["statuses"],
        "all_completed": recomputed["all_completed"],
        "target_met": recomputed["target_met"],
        "schema": SCHEMA,
    }
