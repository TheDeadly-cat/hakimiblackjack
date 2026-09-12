# -*- coding: utf-8 -*-
"""采集真实牌局素材，供建模板库与留出集使用。

只存牌区裁片，不存整屏：既避开余额/聊天，也把体积压到可接受。
触发用「牌区白色像素数的跳变」而不是整体画面变化——荷官一直在动，
按画面差分会一路误触发。白块数量跳变才对应发牌/收牌/翻牌。

素材按会话分目录。训练调参与验收素材必须用不同会话，不能混。

    python scripts/capture_material.py --window <句柄> --minutes 15
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from blackjack_lab.capture.contracts import STATUS_LABELS  # noqa: E402
from blackjack_lab.capture.wgc_source import open_window_source, wgc_available  # noqa: E402
from blackjack_lab.capture.window_list import describe_window  # noqa: E402

# 牌区（庄家 + 7 座的牌阵），相对 2560x1440 全屏画面。
DEFAULT_ROI = "500,800,2350,1320"
JPEG_QUALITY = 92


def white_pixel_count(cv2, np, bgr) -> int:
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    return int(((hsv[:, :, 2] >= 150) & (hsv[:, :, 1] <= 80)).sum())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="采集真实牌局素材")
    parser.add_argument("--window", type=int, required=True)
    parser.add_argument("--minutes", type=float, default=15.0)
    parser.add_argument("--roi", default=DEFAULT_ROI, help="牌区 x0,y0,x1,y1")
    parser.add_argument("--fps", type=float, default=6.0, help="识别采样率")
    parser.add_argument("--delta", type=int, default=1200,
                        help="白色像素数跳变阈值，超过就存一帧")
    parser.add_argument("--heartbeat", type=float, default=8.0,
                        help="无论是否变化，每隔这么多秒也存一帧")
    parser.add_argument("--min-gap", type=float, default=0.25, help="两次存盘最小间隔秒")
    parser.add_argument("--max-mb", type=float, default=800.0, help="磁盘配额")
    parser.add_argument("--output", help="素材目录，默认按时间新建")
    args = parser.parse_args(argv)

    if not wgc_available():
        print("未安装 windows-capture。")
        return 2

    import cv2
    import numpy as np

    x0, y0, x1, y1 = (int(v) for v in args.roi.split(","))
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = Path(args.output or f".local-evidence/material-{stamp}")
    if out.exists() and any(out.iterdir()):
        print(f"拒绝覆盖已有素材目录 {out}")
        return 1
    frames_dir = out / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    info = describe_window(args.window)
    print(f"来源：{info.title}")
    print(f"窗口 {info.width}x{info.height}  牌区 {x1 - x0}x{y1 - y0}  "
          f"计划 {args.minutes} 分钟  配额 {args.max_mb} MB")

    source = open_window_source(args.window, target_fps=args.fps)
    source.start()

    manifest = []
    saved_bytes = 0
    last_white = None
    last_save = 0.0
    last_heartbeat = 0.0
    quota_hit = False
    full_frames = 0
    started = time.perf_counter()
    deadline = started + args.minutes * 60

    try:
        while time.perf_counter() < deadline:
            packet = source.latest()
            if packet is None:
                time.sleep(0.02)
                continue
            frame = packet.pixels
            if frame.shape[1] < x1 or frame.shape[0] < y1:
                print(f"画面尺寸 {frame.shape[1]}x{frame.shape[0]} 小于牌区，停止。")
                break

            # 开头先留几张整幅画面，用于座位与布局标定
            if full_frames < 3:
                path = frames_dir / f"full-{full_frames:02d}.png"
                cv2.imwrite(str(path), frame)
                saved_bytes += path.stat().st_size
                full_frames += 1

            card_area = frame[y0:y1, x0:x1]
            white = white_pixel_count(cv2, np, card_area)
            now = time.perf_counter()
            changed = last_white is None or abs(white - last_white) >= args.delta
            beat = (now - last_heartbeat) >= args.heartbeat
            if not (changed or beat):
                continue
            if (now - last_save) < args.min_gap:
                continue
            if saved_bytes / (1024 * 1024) >= args.max_mb:
                if not quota_hit:
                    print("已达磁盘配额，停止存盘但继续统计。")
                    quota_hit = True
                continue

            name = f"card-{len(manifest):05d}.jpg"
            path = frames_dir / name
            cv2.imwrite(str(path), card_area,
                        [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
            size = path.stat().st_size
            saved_bytes += size
            manifest.append({
                "file": name,
                "frame_id": packet.frame_id,
                "observed_at": packet.observed_at,
                "media_time_ns": packet.media_time_ns,
                "signature": packet.frame_content_signature,
                "white_pixels": white,
                "white_delta": None if last_white is None else white - last_white,
                "trigger": "change" if changed else "heartbeat",
                "elapsed_s": round(now - started, 2),
                "bytes": size,
            })
            last_white = white
            last_save = now
            if beat:
                last_heartbeat = now
            if len(manifest) % 50 == 0:
                print(f"  已存 {len(manifest)} 张 / {saved_bytes / (1024*1024):.0f} MB / "
                      f"{(now - started)/60:.1f} 分钟")
    except KeyboardInterrupt:
        print("\n收到中断，正常收尾。")
    finally:
        report = source.report()
        source.stop()

    (out / "manifest.json").write_text(json.dumps({
        "session": out.name,
        "source_title": info.title,
        "window_size": [info.width, info.height],
        "roi": [x0, y0, x1, y1],
        "requested_minutes": args.minutes,
        "delta_threshold": args.delta,
        "quota_mb": args.max_mb,
        "quota_hit": quota_hit,
        "saved_mb": round(saved_bytes / (1024 * 1024), 1),
        "capture_report": report,
        "frames": manifest,
        "note": "训练调参与验收必须用不同会话目录，不能把同一张牌的相邻帧分到两侧。",
    }, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    print(f"\n共存 {len(manifest)} 张牌区裁片 + {full_frames} 张整幅，"
          f"{saved_bytes / (1024*1024):.0f} MB")
    print(f"到达 {report['arrived']} / 接受 {report['accepted']} / "
          f"重复 {report['repeats']} / 全黑 {report['black_frames']}")
    print(f"最终状态 {report['status']}：{STATUS_LABELS.get(report['status'], '')}")
    print(f"素材目录 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
