"""Sample denser felt stills inside in-play occupancy windows. Does not rehash."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blackjack_lab.analysis.shoe_event_draft import load_draft, write_draft
from blackjack_lab.analysis.shoe_round_pages import (
    attach_in_play_sequence, sample_in_play_windows, write_scan,
)

DEV_CLIP = Path(
    r"C:\Users\Administrator\Videos\NVIDIA\Desktop\Desktop 2026.09.12 - 12.58.11.02.mp4")
DEFAULT_SCAN = ROOT / ".local-evidence" / "dev-clip-12.58.11.02-rounds" / "round-scan.json"
DEFAULT_DRAFT = ROOT / ".local-evidence" / "dev-clip-12.58.11.02-event-draft.json"
DEFAULT_OUT = ROOT / ".local-evidence" / "dev-clip-12.58.11.02-in-play"


def main(argv=None):
    parser = argparse.ArgumentParser(description="对局占用窗内加密静帧；不重新哈希，不发明点数")
    parser.add_argument("--video", type=Path, default=DEV_CLIP)
    parser.add_argument("--scan", type=Path, default=DEFAULT_SCAN)
    parser.add_argument("--draft", type=Path, default=DEFAULT_DRAFT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--step-s", type=float, default=1.0)
    args = parser.parse_args(argv)
    scan = json.loads(args.scan.read_text(encoding="utf-8"))
    round_ids = None
    if args.draft.is_file():
        draft = load_draft(args.draft)
        round_ids = sorted({
            event.get("round_id") for event in draft["events"]
            if event.get("kind") == "deal" and event.get("round_id")
        })
    sample = sample_in_play_windows(
        args.video, scan, args.output_dir, step_s=args.step_s, round_ids=round_ids)
    write_scan(args.output_dir / "in-play-sequence.json", sample)
    if args.draft.is_file():
        draft = load_draft(args.draft)
        attach_in_play_sequence(draft, sample)
        write_draft(args.draft, draft)
        deals = sum(1 for event in draft["events"] if event["kind"] == "deal")
        stills = sum(1 for event in draft["events"] if event["kind"] == "in_play_still")
    else:
        deals = None
        stills = None
    print(json.dumps({
        "accepted": False,
        "source_hashed": sample["source_hashed"],
        "window_count": sample["window_count"],
        "still_counts": [item["still_count"] for item in sample["windows"]],
        "round_ids": [item["round_id"] for item in sample["windows"]],
        "draft_deals": deals,
        "draft_in_play_stills": stills,
        "output_dir": str(args.output_dir),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
