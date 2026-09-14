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


def classify(path):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"path": str(path), "status": "not_json_holdout"}
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        return {"path": str(path), "status": "not_holdout_schema",
                "schema": payload.get("schema") if isinstance(payload, dict) else None}
    try:
        loaded = load_holdout(payload)
    except HoldoutError as error:
        return {"path": str(path), "status": "refused", "reason_code": error.code,
                "independent_video": False}
    return {
        "path": str(path),
        "status": "attested_package",
        "independent_video": loaded["independent_video"],
        "source_kind": loaded["source_kind"],
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
    attested_video = [item for item in found if item.get("independent_video")]
    report = {
        "evidence_root": str(EVIDENCE),
        "json_files": len(found),
        "attested_unused_video": len(attested_video),
        "independent_video": bool(attested_video),
        "note": "开发录像、合成对照和未声明 JSON 都不能换签成独立未使用录像",
        "items": found[:80],
        "truncated": len(found) > 80,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("attested_unused_video", len(attested_video), "independent_video", bool(attested_video))


if __name__ == "__main__":
    main()
