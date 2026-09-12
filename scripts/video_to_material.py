# -*- coding: utf-8 -*-
"""已有本地录像 → capture_material 兼容会话；原片只读，不创建录屏器。"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from blackjack_lab.vision.video_io import VideoReader, VideoRejected
from blackjack_lab.vision.deps import load_cv2, load_numpy
from scripts.capture_material import white_pixel_count


def convert(video, output, *, interval_s=2.0, roi=None, max_frames=4000):
    if not math.isfinite(interval_s) or interval_s <= 0 or max_frames <= 0:
        raise ValueError("采样间隔和帧数上限必须为正数")
    out = Path(output)
    if out.exists():
        raise ValueError("输出目录已存在，拒绝覆盖")
    cv2, np = load_cv2(), load_numpy()
    with VideoReader(video) as reader:
        asset = reader.asset
        if asset.fps <= 0 or asset.frame_count <= 0:
            raise VideoRejected("录像缺少可靠帧率/帧数，无法建立时间采样；请转换为带时间信息的本地文件")
        box = list(roi or (0, 0, asset.width, asset.height))
        x0, y0, x1, y1 = box
        if not 0 <= x0 < x1 <= asset.width or not 0 <= y0 < y1 <= asset.height:
            raise ValueError("ROI 必须为原片内的 x0,y0,x1,y1")
        step = max(1, round(interval_s * asset.fps))
        indices = range(0, asset.frame_count, step)
        (out / "frames").mkdir(parents=True)
        frames, errors = [], []
        last_white = None
        for index in list(indices)[:max_frames]:
            try:
                loaded = reader.seek(index)
                rgb = np.frombuffer(loaded.rgb, np.uint8).reshape(loaded.height, loaded.width, 3)
                bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)[y0:y1, x0:x1]
                name = f"card-{index:08d}.png"
                ok, data = cv2.imencode(".png", bgr)
                if not ok:
                    raise VideoRejected("PNG 编码失败")
                payload = data.tobytes()
                (out / "frames" / name).write_bytes(payload)
                white = white_pixel_count(cv2, np, bgr)
                frames.append({"file": name, "frame_id": index,
                               "elapsed_s": index / asset.fps,
                               "media_time_ns": round(index / asset.fps * 1e9),
                               "signature": hashlib.sha256(bgr.tobytes()).hexdigest(),
                               "sha256": hashlib.sha256(payload).hexdigest(),
                               "bytes": len(payload), "white_pixels": white,
                               "white_delta": None if last_white is None else white - last_white,
                               "trigger": "fixed_time_sample"})
                last_white = white
            except (VideoRejected, OSError) as exc:
                errors.append({"frame_id": index, "elapsed_s": index / asset.fps, "error": str(exc)})
        manifest = {"schema": "video-material-1", "session": f"video-{asset.sha256[:24]}",
                    "source_sha256": asset.sha256, "source_file": str(asset.path),
                    "window_size": [asset.width, asset.height], "roi": box,
                    "sample_interval_s": interval_s, "source_frame_count": asset.frame_count,
                    "source_fps": asset.fps, "frames": frames, "decode_errors": errors,
                    "truncated": len(indices) > max_frames,
                    "valid": not errors and len(indices) <= max_frames,
                    "original_preserved": True,
                    "note": "固定时间采样；短暂出现的牌可能落在采样间隔之间，不等同连续视频全覆盖。"}
        (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video")
    parser.add_argument("output")
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--roi", help="x0,y0,x1,y1；省略则保留完整画面")
    parser.add_argument("--max-frames", type=int, default=4000)
    args = parser.parse_args(argv)
    report = convert(args.video, args.output, interval_s=args.interval,
                     roi=[int(v) for v in args.roi.split(",")] if args.roi else None,
                     max_frames=args.max_frames)
    print(json.dumps({"frames": len(report["frames"]), "valid": report["valid"],
                      "decode_errors": report["decode_errors"], "truncated": report["truncated"]}, ensure_ascii=False))
    return 0 if report["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
