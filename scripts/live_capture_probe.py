# -*- coding: utf-8 -*-
"""实时捕获探针：列窗口、真捕获、报真实指标。

默认只输出指标，不落盘任何画面；要保存标定用帧必须显式 --save-frames，
且只保存选定区域，避免录到余额、聊天和其他窗口。

    python scripts/live_capture_probe.py --list
    python scripts/live_capture_probe.py --title "Blackjack" --seconds 10
    python scripts/live_capture_probe.py --window 12345 --crop 95,448,1180,542 --save-frames 3
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from blackjack_lab.capture.contracts import (  # noqa: E402
    STATUS_LABELS, CaptureRejected, CaptureUnavailable,
)
from blackjack_lab.capture.wgc_source import (  # noqa: E402
    open_monitor_source, open_window_source, wgc_available,
)
from blackjack_lab.capture.window_list import (  # noqa: E402
    find_window_by_title, list_capturable_windows,
)


def parse_crop(text):
    if not text:
        return None
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != 4:
        raise SystemExit("--crop 需要 x,y,w,h 四个数")
    return tuple(int(p) for p in parts)


def cmd_list() -> int:
    windows = list_capturable_windows()
    if not windows:
        print("没有找到可捕获的窗口。")
        return 1
    print(f"{'句柄':>10}  {'尺寸':>11}  {'DPI':>4}  进程 / 标题")
    for info in windows:
        size = f"{info.width}x{info.height}"
        print(f"{info.hwnd:>10}  {size:>11}  {info.dpi:>4}  {info.process_name}  {info.title}")
    print("\n本进程窗口已排除，避免预览被再次捕获形成反馈循环。")
    return 0


def cmd_capture(args) -> int:
    style, runtime = None, None
    if args.style:
        from blackjack_lab.vision.live_input import LiveStyle
        from blackjack_lab.vision.model_adapter import RecognitionRuntime, TrainedModelAdapter
        style = LiveStyle.load(args.style)
        if style.capture_crop is not None and args.crop:
            raise SystemExit("样式已定义 capture_crop，不能再用 --crop 重复裁区")
        adapter = TrainedModelAdapter(args.model, style_id=style.style_id) if args.model else None
        runtime = RecognitionRuntime(adapter)
    if not wgc_available():
        print("未安装 windows-capture。录像回放与手动录牌不受影响。")
        print("安装：pip install -r requirements-capture.txt")
        return 2

    crop = parse_crop(args.crop)
    if args.monitor is not None:
        source = open_monitor_source(args.monitor, target_fps=args.fps, crop=crop)
        target_desc = f"显示器 {args.monitor}"
    else:
        hwnd = args.window
        if hwnd is None:
            info = find_window_by_title(args.title or "")
            if info is None:
                print(f"没有找到标题包含 {args.title!r} 的窗口。先用 --list 查看。")
                return 1
            hwnd = info.hwnd
            print(f"匹配窗口：{info.title}  [{info.process_name}]  {info.width}x{info.height}"
                  f"  DPI={info.dpi}")
        source = open_window_source(hwnd, target_fps=args.fps, crop=crop)
        target_desc = f"窗口 {hwnd}"

    print(f"开始捕获 {target_desc}，采样 {args.fps} 帧/秒，持续 {args.seconds} 秒 …")
    latencies = []
    statuses = {}
    saved = []
    first_frame_s = None
    latest_result = None
    recognition = {"frames": 0, "candidate_outputs": 0, "rank_outputs": 0,
                   "stale_results_discarded": 0, "repeat_or_black_skipped": 0,
                   "writes_ledger": False,
                   "validation_scope": "开发候选；捕获运行不是识牌准确率验收"}
    if runtime is not None:
        recognition["style_id"] = style.style_id
        if runtime.adapter is not None:
            print(runtime.adapter.identity_text)
            recognition.update(model_id=runtime.adapter.model_id, model_digest=runtime.adapter.digest)

    started = time.perf_counter()
    try:
        source.start()
    except (CaptureRejected, CaptureUnavailable) as exc:
        print(f"捕获未开始：{exc}")
        return 3

    try:
        deadline = started + args.seconds
        while time.perf_counter() < deadline:
            packet = source.latest()
            status = source.status()
            statuses[status] = statuses.get(status, 0) + 1
            if packet is not None:
                if first_frame_s is None:
                    first_frame_s = time.perf_counter() - started
                latencies.append(packet.age_ms())
                if runtime is not None:
                    from blackjack_lab.vision.live_input import capture_crop_pixels, recognize_frame
                    selected_crop = (capture_crop_pixels(style, *packet.source_size)
                                     if style.capture_crop is not None else crop)
                    if selected_crop is not None:
                        expected_origin = selected_crop[:2]
                        expected_size = selected_crop[2:]
                    else:
                        expected_origin, expected_size = (0, 0), packet.source_size
                    version = style.layout_version(*expected_size)
                    if (packet.crop_origin != expected_origin or packet.image_size != expected_size
                            or packet.layout_version != version):
                        runtime.invalidate("捕获区域或尺寸已变化")
                        latest_result = None
                        source.intake.set_crop(selected_crop)
                        source.intake.set_layout_version(version)
                        continue
                    if packet.is_repeat or packet.is_black:
                        recognition["repeat_or_black_skipped"] += 1
                    else:
                        result = recognize_frame(packet, style, runtime=runtime)
                        if result is None or packet.token() != source.token():
                            runtime.invalidate("采集代际已改变")
                            latest_result = None
                            recognition["stale_results_discarded"] += 1
                        else:
                            latest_result = result
                            recognition["frames"] += 1
                            recognition["candidate_outputs"] += len(result.observations)
                            recognition["rank_outputs"] += sum(o.accepted_rank() is not None
                                                               for o in result.observations)
                if args.save_frames and len(saved) < args.save_frames:
                    saved.append(packet)
            time.sleep(0.01)
    finally:
        source.stop()

    report = source.report()
    if runtime is not None:
        recognition["generation"] = runtime.generation
        report["recognition"] = recognition
        print("识牌候选统计（不等于准确率）：" + json.dumps(recognition, ensure_ascii=False))
    report["probe"] = {
        "target": target_desc,
        "requested_seconds": args.seconds,
        "requested_fps": args.fps,
        "crop": list(crop) if crop else None,
        "time_to_first_frame_s": round(first_frame_s, 3) if first_frame_s else None,
        "consumer_pickups": len(latencies),
        "status_polls": statuses,
    }
    if latencies:
        ordered = sorted(latencies)
        report["probe"]["queue_age_ms"] = {
            "p50": round(statistics.median(ordered), 2),
            "p95": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 2),
            "max": round(ordered[-1], 2),
        }

    print("\n=== 真实指标 ===")
    print(f"到达帧 {report['arrived']}  被采样接受 {report['accepted']}  "
          f"采样丢弃 {report['dropped_by_sampling']}  队列丢弃 {report['dropped_by_queue']}")
    print(f"重复帧 {report['repeats']}  全黑帧 {report['black_frames']}  "
          f"尺寸变化 {report['resize_events']}")
    print(f"到达帧率 {report['arrival_fps']}  采样帧率 {report['accepted_fps']}  "
          f"运行 {report['elapsed_s']} 秒")
    print(f"最终状态 {report['status']}：{STATUS_LABELS.get(report['status'], '')}")
    if report["probe"].get("queue_age_ms"):
        age = report["probe"]["queue_age_ms"]
        print(f"取帧时的帧龄 p50={age['p50']}ms p95={age['p95']}ms max={age['max']}ms")
    if report.get("source_note"):
        print(f"来源提示：{report['source_note']}")
    print(report["fps_note"])

    if args.output:
        out = Path(args.output)
        out.mkdir(parents=True, exist_ok=True)
        (out / "capture-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8")
        if latest_result is not None:
            (out / "latest-candidates.json").write_text(latest_result.to_json(), encoding="utf-8")
        for index, packet in enumerate(saved):
            _save_frame(out / f"frame-{index:02d}.png", packet)
        if saved:
            (out / "frames.json").write_text(
                json.dumps([p.as_dict() for p in saved],
                           ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8")
        print(f"\n回执写入 {out}")
    return 0


def _save_frame(path: Path, packet) -> None:
    import cv2
    cv2.imwrite(str(path), packet.pixels)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="实时捕获探针（只捕获你选定的来源）")
    parser.add_argument("--list", action="store_true", help="列出可捕获窗口")
    parser.add_argument("--window", type=int, help="窗口句柄")
    parser.add_argument("--title", help="按标题片段匹配窗口")
    parser.add_argument("--monitor", type=int, help="显示器序号，从 1 开始")
    parser.add_argument("--seconds", type=float, default=8.0)
    parser.add_argument("--fps", type=float, default=10.0, help="识别采样帧率，不是捕获上限")
    parser.add_argument("--crop", help="来源内的捕获区域 x,y,w,h")
    parser.add_argument("--model", type=Path, help="本地训练模型目录；必须同时指定 --style")
    parser.add_argument("--style", type=Path, help="归一化实时样式 JSON；开启同模型实时候选识别")
    parser.add_argument("--save-frames", type=int, default=0,
                        help="保存前 N 帧（只含选定区域）用于标定")
    parser.add_argument("--output", help="回执目录，建议放 .local-evidence/ 下")
    args = parser.parse_args(argv)
    if args.model and not args.style:
        parser.error("--model 必须同时指定 --style")

    if args.list:
        return cmd_list()
    if args.window is None and args.title is None and args.monitor is None:
        parser.print_help()
        return 1
    return cmd_capture(args)


if __name__ == "__main__":
    raise SystemExit(main())
