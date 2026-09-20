"""Scan felt occupancy into round pages. Does not rehash the source or invent ranks."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blackjack_lab.analysis.shoe_event_draft import write_draft
from blackjack_lab.analysis.shoe_round_pages import (
    draft_from_round_scan, scan_round_pages, write_scan,
)

DEV_CLIP = Path(
    r"C:\Users\Administrator\Videos\NVIDIA\Desktop\Desktop 2026.09.12 - 12.58.11.02.mp4")


def main(argv=None):
    parser = argparse.ArgumentParser(description="按绒面占用切轮次页；不重新哈希，不发明点数")
    parser.add_argument("--video", type=Path, default=DEV_CLIP)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--interval-s", type=float, default=8.0)
    parser.add_argument("--video-sha256", default=None)
    parser.add_argument("--role", default="development")
    args = parser.parse_args(argv)
    scan = scan_round_pages(args.video, args.output_dir, interval_s=args.interval_s)
    write_scan(args.output_dir / "round-scan.json", scan)
    draft = draft_from_round_scan(
        scan, filename=args.video.name, video_sha256=args.video_sha256, role=args.role)
    write_draft(args.output_dir / "event-draft.json", draft)
    print(json.dumps({
        "accepted": False,
        "source_hashed": scan["source_hashed"],
        "round_count": scan["round_count"],
        "overlay_samples": scan["overlay_samples"],
        "sample_count": scan["sample_count"],
        "ranks_invented": scan["ranks_invented"],
        "output_dir": str(args.output_dir),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
