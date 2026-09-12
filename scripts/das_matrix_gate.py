"""Recompute DAS 336 attachment gates. Never treat receipt.passed as proof."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from scripts.das_benchmarks import SCHEMA, SPEC_PATH, build_receipt, load_spec

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
    for row in rows:
        if type(row) is not dict:
            _add_once(errors, "invalid_row")
            continue
        wall = row.get("wall_seconds")
        if type(wall) not in (int, float) or isinstance(wall, bool) or not math.isfinite(wall) or wall < 0:
            bad_timing = True
        name = row.get("result_file")
        if type(name) is not str or not name or Path(name).name != name:
            missing_result = True
            continue
        path = directory / name
        try:
            resolved = path.resolve()
        except OSError:
            missing_result = True
            continue
        if not path.is_file() or not resolved.is_relative_to(base):
            missing_result = True
            continue
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
            digest = payload.get("backend_source_sha256")
            if engine_digest and digest and digest != engine_digest:
                _add_once(errors, "result_source_mismatch")
            decks = (payload.get("input") or {}).get("n_decks") if type(payload.get("input")) is dict else None
            if decks is not None and decks != row.get("n_decks"):
                _add_once(errors, "result_input_mismatch")
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
