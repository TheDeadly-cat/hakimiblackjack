# -*- coding: utf-8 -*-
"""只读探测本机旁观录像：裁牌桌、标座位、出候选框。不改原文件，不写账本。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("video", type=Path)
    parser.add_argument("--frame", type=int, default=49222)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--model", type=Path, help="指定本地训练模型目录；仅生成候选")
    parser.add_argument("--style", type=Path, help="指定静态或归一化样式 JSON")
    args = parser.parse_args(argv)
    if args.model and not args.style:
        parser.error("--model 必须同时指定 --style")
    from blackjack_lab.vision.pipeline import recognize_loaded
    from blackjack_lab.vision.model_adapter import TrainedModelAdapter, load_style, prepare_style_image
    from blackjack_lab.vision.video_io import VideoReader
    style = load_style(args.style) if args.style else None
    adapter = TrainedModelAdapter(args.model, style_id=style.style_id) if args.model else None

    out = args.output or (ROOT / ".local-evidence" / "navy-live-probe")
    out.mkdir(parents=True, exist_ok=True)
    with VideoReader(args.video) as reader:
        loaded = reader.seek(args.frame)
        layout, canvas = prepare_style_image(loaded, style)
        result = recognize_loaded(canvas, layout=layout, adapter=adapter)
    from blackjack_lab.vision.image_io import write_png_rgb
    write_png_rgb(out / "felt.png", canvas.width, canvas.height, canvas.rgb)
    (out / "candidates.json").write_text(result.to_json(), encoding="utf-8")
    summary = {
        "video": str(args.video),
        "original_preserved": True,
        "frame": args.frame,
        "style": layout.style_id,
        "model_id": result.model_id,
        "model_digest": result.model_digest,
        "validation_scope": "开发候选；本探针不提供真实识牌准确率验收",
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
