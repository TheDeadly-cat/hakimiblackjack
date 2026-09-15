"""Write a development-clip event draft. Does not rehash the source video."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blackjack_lab.analysis.shoe_event_draft import development_clip_stub, write_draft

DEV_CLIP = "Desktop 2026.09.12 - 12.58.11.02.mp4"


def main(argv=None):
    parser = argparse.ArgumentParser(description="写出开发片事件草稿；不重新哈希原片，不能写成留出")
    parser.add_argument("--filename", default=DEV_CLIP)
    parser.add_argument("--video-sha256", default=None)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--role", default="development")
    args = parser.parse_args(argv)
    draft = development_clip_stub(
        filename=args.filename, video_sha256=args.video_sha256, role=args.role)
    write_draft(args.output, draft)
    print(json.dumps({
        "accepted": draft["accepted"],
        "role": draft["role"],
        "event_count": len(draft["events"]),
        "output": str(args.output),
        "note": draft["note"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
