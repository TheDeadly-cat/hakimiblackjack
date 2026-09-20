"""List local evidence that is not an attested unused-video holdout.

Does not relabel development recordings as independent video.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blackjack_lab.analysis.unused_holdout import SCHEMA, HoldoutError, load_holdout

EVIDENCE = ROOT / ".local-evidence"
DECLARED_SCHEMAS = {
    "hakimi-operator-study-v1": "operator_study_declared",
    "hakimi-fullscreen-acceptance-v1": "fullscreen_declared",
    "hakimi-table-rule-archive-v1": "table_archive_declared",
    "hakimi-m4-acceptance-pack-v1": "acceptance_pack_declared",
}


def classify(path):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"path": str(path), "status": "not_json_holdout"}
    if not isinstance(payload, dict):
        return {"path": str(path), "status": "not_holdout_schema", "schema": None}
    schema = payload.get("schema")
    if schema in DECLARED_SCHEMAS:
        return {
            "path": str(path),
            "status": DECLARED_SCHEMAS[schema],
            "schema": schema,
            "accepted": False,
            "paired": False,
            "independent_video": False,
            "hand_edited_accepted": payload.get("accepted") is True,
            "identity_chain": payload.get("identity_chain") or [],
        }
    if schema != SCHEMA:
        return {"path": str(path), "status": "not_holdout_schema", "schema": schema}
    try:
        loaded = load_holdout(payload)
    except HoldoutError as error:
        return {"path": str(path), "status": "refused", "reason_code": error.code,
                "independent_video": False}
    return {
        "path": str(path),
        "status": "attested_package",
        "declared_unused_video": loaded.get("declared_unused_video", False),
        "independent_video": False,
        "source_kind": loaded["source_kind"],
        "evidence_level": loaded.get("evidence_level"),
        "round_count": len(loaded["rounds"]),
    }


def main():
    found = []
    if EVIDENCE.exists():
        for path in sorted(EVIDENCE.rglob("*")):
            if not path.is_file() or path.name.startswith("."):
                continue
            if path.suffix.lower() == ".json":
                found.append(classify(path))
            elif path.suffix.lower() in {".mp4", ".mkv", ".avi", ".mov", ".webm"}:
                found.append({"path": str(path), "status": "video_not_attested",
                              "independent_video": False})
    declared_video = [item for item in found if item.get("declared_unused_video")]
    report = {
        "evidence_root": str(EVIDENCE),
        "json_files": len(found),
        "declared_unused_video": len(declared_video),
        "independent_video": False,
        "note": "开发录像、合成对照和未声明 JSON 都不能换签成独立未使用录像；具名 video_id 仍只是声明",
        "items": found[:80],
        "truncated": len(found) > 80,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("declared_unused_video", len(declared_video), "independent_video", False)


if __name__ == "__main__":
    main()
