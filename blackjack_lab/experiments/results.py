"""Persist experiment outputs without touching the event database."""
import csv
import json
from pathlib import Path

from ..analysis.contracts import canonical
from .contracts import SCHEMA


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


def write_experiment(output_dir, record):
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / "experiment.json"
    csv_path = directory / "experiment.csv"
    json_path.write_text(json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
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
        for action, payload in actions.items():
            rows.append({
                "n_decks": item.get("n_decks"),
                "status": item.get("status"),
                "reason": item.get("reason"),
                "action": action,
                "action_status": payload.get("status"),
                "ev": payload.get("ev", ""),
                "engine_version": item.get("engine_version") or "",
            })
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["n_decks", "status", "reason", "action",
                                                    "action_status", "ev", "engine_version"])
        writer.writeheader()
        writer.writerows(rows)
    return {"json": json_path, "csv": csv_path, "record": record, "canonical": canonical(record)}
