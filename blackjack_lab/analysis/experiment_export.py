"""Persist a full shoe-window study. Stdout summaries are not the archive."""
from __future__ import annotations

import csv
import io
import json
import math
from pathlib import Path
from time import time

from ..experiments.contracts import ExperimentError
from ..experiments.results import claim_output_dir
from ..storage.safe_files import atomic_write
from .contracts import canonical
from .research_windows import evaluation_scope

STUDY_RUN_SCHEMA = "hakimi-window-study-run-v1"
CHECKPOINT_FIELDS = (
    "round_index", "physical_remaining", "composition_counts", "ev", "ci_low", "ci_high",
    "wald_ci_low", "wald_ci_high", "status", "reason_code", "window_state",
    "path_policy_id", "evaluation_policy_id", "evaluation_method", "input_scope",
    "source_mode", "elapsed_seconds", "play_error", "statistical_positive",
    "statistical_nonpositive", "window_claim_allowed", "sign_status", "sign_reason",
    "n_ok", "n_failed", "n_not_run", "family_size", "hoeffding_radius",
    "consumption_policy", "stop_reason_local", "classification",
)

CLASSIFICATION_KEYS = (
    "status", "ev", "window", "window_kind", "evaluation_method", "method",
    "window_claim_allowed", "indeterminate", "numerical_tolerance",
    "sign_status", "ci_low", "ci_high",
)


def _source_identity(root):
    try:
        from scripts.source_identity import source_identity
        return source_identity(root)
    except Exception:
        return {"commit": None, "dirty_worktree": True, "kind": "unversioned-directory"}


def checkpoint_row(item):
    predeal = dict(item.get("predeal") or {})
    row = {key: None for key in CHECKPOINT_FIELDS}
    row["round_index"] = item.get("round_index")
    row["physical_remaining"] = predeal.get("physical_remaining")
    row["composition_counts"] = list(predeal.get("composition_counts") or predeal.get("counts") or [])
    row["ev"] = predeal.get("ev")
    row["ci_low"] = predeal.get("ci_low")
    row["ci_high"] = predeal.get("ci_high")
    row["wald_ci_low"] = predeal.get("wald_ci_low")
    row["wald_ci_high"] = predeal.get("wald_ci_high")
    row["status"] = predeal.get("status")
    row["reason_code"] = predeal.get("reason_code")
    row["window_state"] = predeal.get("window_state")
    row["path_policy_id"] = item.get("path_policy_id") or predeal.get("path_policy_id")
    row["evaluation_policy_id"] = (
        item.get("evaluation_policy_id") or predeal.get("evaluation_policy_id"))
    row["evaluation_method"] = item.get("evaluation_method") or predeal.get("evaluation_method")
    row["input_scope"] = predeal.get("input_scope")
    row["source_mode"] = predeal.get("source_mode")
    row["elapsed_seconds"] = predeal.get("elapsed_seconds")
    row["play_error"] = item.get("play_error")
    row["statistical_positive"] = predeal.get("statistical_positive")
    row["statistical_nonpositive"] = predeal.get("statistical_nonpositive")
    if "window_claim_allowed" in predeal:
        row["window_claim_allowed"] = predeal.get("window_claim_allowed")
    row["sign_status"] = predeal.get("sign_status")
    row["sign_reason"] = predeal.get("sign_reason")
    row["n_ok"] = predeal.get("n_ok")
    row["n_failed"] = predeal.get("n_failed")
    row["n_not_run"] = predeal.get("n_not_run")
    row["family_size"] = predeal.get("family_size")
    row["hoeffding_radius"] = predeal.get("hoeffding_radius")
    row["consumption_policy"] = item.get("consumption_policy") or item.get("path_policy_id")
    classification = {}
    for key in CLASSIFICATION_KEYS:
        if key in predeal:
            classification[key] = predeal.get(key)
        elif key in item:
            classification[key] = item.get(key)
    if "window_state" in predeal:
        classification["recorded_window_state"] = predeal.get("window_state")
    row["classification"] = classification
    return row


def checkpoint_rows(report):
    return [checkpoint_row(item) for item in report.get("rounds") or []]


def _json_cell(text, expected_type, name):
    """Decode nested CSV data. Legacy Python repr is not JSON and is refused."""
    def reject_constant(value):
        raise ValueError(f"{name}: non-finite JSON constant {value}")
    try:
        value = json.loads(text, parse_constant=reject_constant)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name}: expected strict JSON; preserve the source file for diagnosis") from error
    if not isinstance(value, expected_type):
        raise ValueError(f"{name}: wrong JSON type")
    return value


def checkpoint_row_to_csv(row):
    """Preserve nested objects as JSON instead of Python str(dict)."""
    out = {}
    for key in CHECKPOINT_FIELDS:
        value = row.get(key)
        if value is None:
            out[key] = ""
        elif key in ("classification", "composition_counts"):
            out[key] = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        else:
            out[key] = value
    return out


def checkpoint_row_from_csv(row):
    """Restore the exported column types; never evaluate Python expressions."""
    ints = {"round_index", "physical_remaining", "n_ok", "n_failed", "n_not_run", "family_size"}
    floats = {
        "ev", "ci_low", "ci_high", "wald_ci_low", "wald_ci_high",
        "elapsed_seconds", "hoeffding_radius",
    }
    bools = {"statistical_positive", "statistical_nonpositive", "window_claim_allowed"}
    out = {}
    for key in CHECKPOINT_FIELDS:
        value = row.get(key)
        if value in (None, ""):
            out[key] = None
        elif key == "classification":
            out[key] = _json_cell(value, dict, key)
        elif key == "composition_counts":
            out[key] = _json_cell(value, list, key)
        elif key in ints:
            out[key] = int(value)
        elif key in floats:
            number = float(value)
            if not math.isfinite(number):
                raise ValueError(f"{key}: non-finite value")
            out[key] = number
        elif key in bools:
            if value not in ("True", "False", "true", "false"):
                raise ValueError(f"{key}: invalid boolean")
            out[key] = value.lower() == "true"
        else:
            out[key] = value
    return out


def _scope_record(row):
    """Rebuild classification input. Do not invent an opening-window kind."""
    raw = row.get("classification")
    if isinstance(raw, str):
        if not raw:
            raw = None
        else:
            row = checkpoint_row_from_csv(row)
            raw = row.get("classification")
    if raw is not None and not isinstance(raw, dict):
        raise ValueError("classification: expected an object")
    src = dict(raw or {})
    record = {}
    for key in CLASSIFICATION_KEYS:
        if key in src:
            record[key] = src[key]
        elif key in row and row.get(key) is not None and key not in ("window", "window_kind"):
            record[key] = row[key]
    if "window" not in record and "window_kind" not in record:
        record["indeterminate"] = True
        record["legacy_missing_window_kind"] = True
        if src.get("recorded_window_state"):
            record["recorded_window_state"] = src.get("recorded_window_state")
    return record


def summary_from_checkpoint_rows(rows, *, report=None):
    """Totals that can be recomputed from the archived checkpoint table."""
    scope = evaluation_scope([_scope_record(row) for row in rows])
    evaluated = [row for row in rows if row.get("ev") is not None
                 and row.get("status") == "available"]
    failed = [row for row in rows if row.get("play_error") or row.get("status") == "failed"]
    payload = {
        "round_count": len(rows),
        "predeal_available": sum(1 for row in rows if row.get("status") == "available"),
        "predeal_unsupported": sum(1 for row in rows if row.get("status") == "unsupported"),
        "predeal_timeout": sum(1 for row in rows if row.get("status") == "timeout"),
        "predeal_failed": sum(1 for row in rows if row.get("status") == "failed"),
        "positive_ev": scope["positive"],
        "nonpositive_ev": scope["nonpositive"],
        "indeterminate_ev": scope["indeterminate"],
        "unavailable_ev": scope["unavailable"],
        "zero_window": scope["zero_window"],
        "complete_evaluation": scope["complete_evaluation"],
        "evaluated_count": scope["evaluated_count"],
        "unassessable_count": scope["unassessable_count"],
        "verified_no_positive_over_declared_domain": scope["verified_no_positive_over_declared_domain"],
        "no_positive_signal_detected": scope["no_positive_signal_detected"],
        "incomplete_cannot_claim_zero_window": scope["incomplete_cannot_claim_zero_window"],
        "signal_coverage": (len(evaluated) / len(rows) if rows else 0.0),
        "play_error_count": len(failed),
        "recomputed_from_checkpoint_rows": True,
    }
    if report is not None:
        payload["study_kind"] = report.get("kind")
        payload["cut_remaining"] = report.get("cut_remaining")
        payload["stop_reason"] = report.get("stop_reason")
        payload["evaluation_method"] = report.get("evaluation_method")
        payload["path_policy_id"] = report.get("path_policy_id")
        payload["evaluation_policy_id"] = report.get("evaluation_policy_id")
        payload["mc_family_size"] = report.get("mc_family_size")
        payload["surrender"] = report.get("surrender")
    return payload


def run_identity(report, *, root=None):
    root = Path(root) if root is not None else Path(__file__).resolve().parents[2]
    return {
        "schema": STUDY_RUN_SCHEMA,
        "written_at": time(),
        "source_identity": _source_identity(root),
        "kind": report.get("kind"),
        "n_decks": report.get("n_decks"),
        "seed": report.get("seed"),
        "surrender": report.get("surrender"),
        "evaluation_method": report.get("evaluation_method"),
        "evaluation_policy_id": report.get("evaluation_policy_id"),
        "path_policy_id": report.get("path_policy_id"),
        "cut_remaining": report.get("cut_remaining"),
        "cut_declared": report.get("cut_declared"),
        "cut_source": report.get("cut_source"),
        "stop_reason": report.get("stop_reason"),
        "mc_family_size": report.get("mc_family_size"),
        "mc_alpha": report.get("mc_alpha"),
        "margin": report.get("margin"),
        "not_a_reliable_window_claim": True,
        "methods_not_merged": report.get("methods_not_merged"),
    }


def write_window_study(report, output_dir, *, root=None):
    """Write study.json (including rounds), checkpoint table, identity, and recomputed summary."""
    if not isinstance(report, dict) or "rounds" not in report:
        raise ExperimentError("STUDY_INCOMPLETE", "整靴研究必须包含 rounds，才能作为可复现实验归档")
    directory = claim_output_dir(output_dir)
    rows = checkpoint_rows(report)
    identity = run_identity(report, root=root)
    summary = summary_from_checkpoint_rows(rows, report=report)
    study_path = directory / "study.json"
    checkpoints_path = directory / "checkpoints.jsonl"
    csv_path = directory / "checkpoints.csv"
    identity_path = directory / "run_identity.json"
    summary_path = directory / "summary.json"
    atomic_write(
        study_path,
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8"),
        overwrite=False)
    lines = "".join(canonical(row) + "\n" for row in rows)
    atomic_write(checkpoints_path, lines.encode("utf-8"), overwrite=False)
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=list(CHECKPOINT_FIELDS), extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(checkpoint_row_to_csv(row))
    atomic_write(csv_path, stream.getvalue().encode("utf-8"), overwrite=False)
    atomic_write(
        identity_path,
        json.dumps(identity, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8"),
        overwrite=False)
    atomic_write(
        summary_path,
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8"),
        overwrite=False)
    return {
        "output_dir": str(directory),
        "study": str(study_path),
        "checkpoints": str(checkpoints_path),
        "checkpoints_csv": str(csv_path),
        "run_identity": str(identity_path),
        "summary": str(summary_path),
        "round_count": len(rows),
    }
