# -*- coding: utf-8 -*-
"""只读探测本机旁观录像：裁牌桌、标座位、出候选框。不改原文件，不写账本。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("video", type=Path)
    parser.add_argument("--frame", type=int, default=49222)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    from blackjack_lab.vision.pipeline import infer_layout, recognize_loaded
    from blackjack_lab.vision.table_crop import apply_layout_crops
    from blackjack_lab.vision.video_io import VideoReader

    out = args.output or (ROOT / ".local-evidence" / "navy-live-probe")
    out.mkdir(parents=True, exist_ok=True)
    with VideoReader(args.video) as reader:
        loaded = reader.seek(args.frame)
        layout = infer_layout(loaded)
        canvas = apply_layout_crops(loaded, layout)
        result = recognize_loaded(canvas, layout=layout)
    from blackjack_lab.vision.image_io import write_png_rgb
    write_png_rgb(out / "felt.png", canvas.width, canvas.height, canvas.rgb)
    (out / "candidates.json").write_text(result.to_json(), encoding="utf-8")
    summary = {
        "video": str(args.video),
        "original_preserved": True,
        "frame": args.frame,
        "style": layout.style_id,
        "platform_claim": layout.platform_claim,
        "felt": [canvas.width, canvas.height],
        "observations": len(result.observations),
        "accepted_ranks": [o.accepted_rank() for o in result.observations],
        "seats": [o.seat_hint for o in result.observations],
        "writes_ledger": False,
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
