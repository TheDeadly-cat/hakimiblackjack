"""Record desktop geometry and overlay pixel fixtures. Never a F11 pass."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blackjack_lab.capture.fullscreen_acceptance import (
    attach_source_frame_probe, empty_evidence, probe_environment, report,
)
from blackjack_lab.capture.overlay_exclusion import LAB_OVERLAY_BGR, source_frame_excludes_lab_overlays

FELT = (40, 90, 30)


def _solid(color, height=6, width=8):
    return [[color for _ in range(width)] for _ in range(height)]


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / ".local-evidence" / "capture-probe-20260914" / "environment.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    environment = probe_environment()
    overlay_item = source_frame_excludes_lab_overlays(_solid(LAB_OVERLAY_BGR[0]), felt_bgr=FELT)
    felt_item = source_frame_excludes_lab_overlays(_solid(FELT), felt_bgr=FELT)
    evidence = attach_source_frame_probe(
        empty_evidence(), _solid(FELT), felt_bgr=FELT, overlay_bgr=LAB_OVERLAY_BGR[0],
        evidence_path=str(out), source_is_browser_capture=False)
    body = report(evidence, environment)
    body["synthetic_pixel_probes"] = {
        "overlay_block": overlay_item,
        "felt_block": felt_item,
        "note": "合成色块不是浏览器源帧；不能把本探针写成 F11 通过",
    }
    windows = []
    try:
        from blackjack_lab.capture.window_list import list_capturable_windows
        listed = list_capturable_windows()
        windows = [{"title": item.title, "process": item.process_name,
                    "size": [item.width, item.height], "dpi": item.dpi}
                   for item in listed[:20]]
        body["listed_window_count"] = len(listed)
        body["listed_windows_head"] = windows
    except Exception as error:
        body["listed_window_error"] = str(error)
    out.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "path": str(out),
        "accepted": body["accepted"],
        "not_acceptance": environment.get("not_acceptance"),
        "system_dpi": environment.get("system_dpi"),
        "monitor_count": environment.get("monitor_count"),
        "listed_window_count": body.get("listed_window_count"),
        "overlay_block_passed": overlay_item["passed"],
        "felt_block_item_passed": felt_item["passed"],
        "source_frame_item_passed": evidence["source_frame_excludes_overlay"]["passed"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
