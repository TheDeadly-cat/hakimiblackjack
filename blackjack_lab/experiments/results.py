"""Persist experiment outputs without touching the event database."""
import csv
import io
import json
from pathlib import Path

from ..analysis.contracts import canonical
from ..storage.safe_files import atomic_write
from .contracts import SCHEMA, ExperimentError


def _source_identity(root):
    try:
        from scripts.source_identity import source_identity
        return source_identity(root)
    except Exception:
        return {"commit": None, "dirty_worktree": True, "kind": "unversioned-directory"}


def experiment_record(config, items, elapsed=0.0):
    root = Path(__file__).resolve().parents[2]
    return {
        "schema": SCHEMA,
        "config": config.to_dict(),
        "source_identity": _source_identity(root),
        "elapsed_seconds": elapsed,
        "items": items,
        "note": "固定移除已知牌面不等于实际经过若干完整轮次。当前手牌EV不是下一轮下注优势。",
    }


def claim_output_dir(output_dir):
    directory = Path(output_dir)
    try:
        directory.mkdir(parents=True, exist_ok=False)
    except FileExistsError as error:
        raise ExperimentError("OUTPUT_EXISTS", f"输出目录已存在，拒绝覆盖已有实验: {directory}") from error
    return directory


def write_experiment(output_dir, record):
    directory = Path(output_dir)
    if not directory.is_dir():
        directory = claim_output_dir(directory)
    json_path = directory / "experiment.json"
    csv_path = directory / "experiment.csv"
    if json_path.exists() or csv_path.exists():
        raise ExperimentError("OUTPUT_EXISTS", f"输出目录已有实验文件，拒绝覆盖: {directory}")
    json_bytes = json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
    rows = []
    for item in record["items"]:
        actions = item.get("actions") or {}
        if not actions:
            rows.append({
                "n_decks": item.get("n_decks"),
                "status": item.get("status"),
                "reason": item.get("reason"),
                "action": "",
                "action_status": "",
                "ev": "",
                "engine_version": item.get("engine_version") or "",
            })
            continue
        for action, action_payload in actions.items():
            rows.append({
                "n_decks": item.get("n_decks"),
                "status": item.get("status"),
                "reason": item.get("reason"),
                "action": action,
                "action_status": action_payload.get("status"),
                "ev": action_payload.get("ev", ""),
                "engine_version": item.get("engine_version") or "",
            })
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=["n_decks", "status", "reason", "action",
                                                "action_status", "ev", "engine_version"])
    writer.writeheader()
    writer.writerows(rows)
    atomic_write(json_path, json_bytes, overwrite=False)
    atomic_write(csv_path, stream.getvalue().encode("utf-8"), overwrite=False)
    return {"json": json_path, "csv": csv_path, "record": record, "canonical": canonical(record)}
