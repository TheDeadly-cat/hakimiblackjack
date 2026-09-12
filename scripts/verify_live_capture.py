# -*- coding: utf-8 -*-
"""实时捕获的 Windows 实机自检。

自己开一个已知内容的测试窗口再捕获它，因此不碰任何真实牌桌、账号或桌面内容，
又能验证真东西：像素是否正确、多帧是否真的到达、静止时是否判为重复帧、
窗口关闭后是否报来源消失、停止后是否留下后台线程。

    python scripts/verify_live_capture.py
    python scripts/verify_live_capture.py --output .local-evidence/live-selftest
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from blackjack_lab.capture.contracts import (  # noqa: E402
    STATUS_LIVE, STATUS_NO_NEW_FRAME, STATUS_SOURCE_LOST, CaptureRejected,
)
from blackjack_lab.capture.wgc_source import (  # noqa: E402
    open_window_source, wgc_available,
)
from blackjack_lab.capture.window_list import find_window_by_title  # noqa: E402

WINDOW_TITLE = "HAKIMI-CAPTURE-SELFTEST"
# (名字, RGB)。捕获缓冲是 BGRA，复制后是 BGR，比对时换序。
PALETTE = [("red", (255, 0, 0)), ("green", (0, 255, 0)), ("blue", (0, 0, 255))]
CHANGE_MS = 300
CYCLE_SECONDS = 3.0
HOLD_SECONDS = 2.0

CHILD_SOURCE = f'''
import tkinter as tk
PALETTE = {[c[1] for c in PALETTE]!r}
root = tk.Tk()
root.title({WINDOW_TITLE!r})
root.geometry("480x360+120+120")
root.resizable(False, False)
panel = tk.Frame(root, bg="#%02x%02x%02x" % tuple(PALETTE[0]))
panel.pack(fill="both", expand=True)
state = {{"i": 0, "elapsed": 0}}

def tick():
    state["elapsed"] += {CHANGE_MS}
    if state["elapsed"] <= {int(CYCLE_SECONDS * 1000)}:
        state["i"] = (state["i"] + 1) % len(PALETTE)
        panel.configure(bg="#%02x%02x%02x" % tuple(PALETTE[state["i"]]))
    root.after({CHANGE_MS}, tick)

root.after({CHANGE_MS}, tick)
root.mainloop()
'''


def nearest_palette(bgr_pixel):
    """返回最接近的调色板名字与距离。"""
    blue, green, red = (int(v) for v in bgr_pixel)
    best, best_dist = None, None
    for name, (pr, pg, pb) in PALETTE:
        dist = abs(red - pr) + abs(green - pg) + abs(blue - pb)
        if best_dist is None or dist < best_dist:
            best, best_dist = name, dist
    return best, best_dist


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="实时捕获实机自检")
    parser.add_argument("--output", help="回执目录")
    parser.add_argument("--fps", type=float, default=10.0)
    args = parser.parse_args(argv)

    if not wgc_available():
        print("未安装 windows-capture，自检跳过（不算通过）。")
        return 2

    checks = {}
    threads_before = threading.active_count()

    child = subprocess.Popen([sys.executable, "-c", CHILD_SOURCE])
    try:
        info = None
        deadline = time.perf_counter() + 8.0
        while time.perf_counter() < deadline:
            info = find_window_by_title(WINDOW_TITLE)
            if info is not None:
                break
            time.sleep(0.1)
        if info is None:
            print("测试窗口没有出现，自检失败。")
            return 1
        print(f"测试窗口 hwnd={info.hwnd} {info.width}x{info.height} DPI={info.dpi}")

        source = open_window_source(info.hwnd, target_fps=args.fps)
        source.start()
        samples = []
        statuses = []

        # 阶段一：窗口在动，验证像素与多帧
        started = time.perf_counter()
        while time.perf_counter() - started < CYCLE_SECONDS:
            packet = source.latest()
            statuses.append(source.status())
            if packet is not None:
                cy, cx = packet.height // 2, packet.width // 2
                name, dist = nearest_palette(packet.pixels[cy, cx])
                samples.append({
                    "frame_id": packet.frame_id,
                    "colour": name,
                    "distance": dist,
                    "is_repeat": packet.is_repeat,
                    "size": list(packet.image_size),
                })
            time.sleep(0.02)

        # 阶段二：窗口静止。WGC 变化驱动，此时应当没有新帧，
        # 必须报「暂无新帧」，不能继续显示 live，也不能当成捕获失败。
        static_statuses = []
        static_started = time.perf_counter()
        while time.perf_counter() - static_started < HOLD_SECONDS:
            source.latest()
            static_statuses.append(source.status())
            time.sleep(0.05)

        matched = [s for s in samples if s["distance"] <= 40]
        colours = {s["colour"] for s in matched}

        checks["captured_multiple_frames"] = len(samples) >= 3
        checks["pixels_match_known_colours"] = len(matched) >= max(2, len(samples) // 2)
        checks["observed_more_than_one_colour"] = len(colours) >= 2
        checks["static_window_reports_no_new_frame"] = (
            STATUS_NO_NEW_FRAME in static_statuses)
        checks["static_window_not_reported_live"] = (
            static_statuses[-1] != STATUS_LIVE if static_statuses else False)
        checks["frame_size_matches_window"] = bool(
            samples and samples[0]["size"][0] >= info.width - 40)

        # 阶段三：捕获仍在运行时关掉窗口，状态必须变成来源消失
        child.terminate()
        child.wait(timeout=5)
        loss_deadline = time.perf_counter() + 3.0
        loss_status = source.status()
        while time.perf_counter() < loss_deadline and loss_status != STATUS_SOURCE_LOST:
            time.sleep(0.05)
            loss_status = source.status()
        checks["source_loss_detected_while_running"] = loss_status == STATUS_SOURCE_LOST
        report = source.report()
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:  # pragma: no cover
                child.kill()

    # 阶段四：停止后必须释放后台线程，且不能对已消失的窗口重开
    source.stop()
    time.sleep(0.3)
    checks["no_capture_threads_left"] = threading.active_count() <= threads_before

    try:
        source.start()
        checks["restart_on_dead_window_rejected"] = False
        source.stop()
    except CaptureRejected:
        checks["restart_on_dead_window_rejected"] = True

    passed = all(checks.values())
    print("\n=== 自检结果 ===")
    for name, ok in checks.items():
        print(f"  {'OK  ' if ok else 'FAIL'}  {name}")
    print(f"\n采样帧 {len(samples)}，命中已知颜色 {len(matched)}，"
          f"颜色种类 {sorted(colours)}")
    print(f"静止期状态取样 {len(static_statuses)} 次，出现过的状态 "
          f"{sorted(set(static_statuses))}")
    print(f"到达 {report['arrived']} / 接受 {report['accepted']} / "
          f"采样丢弃 {report['dropped_by_sampling']} / 队列丢弃 {report['dropped_by_queue']}")
    print(f"结论：{'通过' if passed else '未通过'}")

    if args.output:
        out = Path(args.output)
        out.mkdir(parents=True, exist_ok=True)
        (out / "live-capture-selftest.json").write_text(
            json.dumps({
                "passed": passed,
                "checks": checks,
                "samples": samples,
                "capture_report": report,
                "note": "自建测试窗口，不含任何真实牌桌或桌面内容。",
            }, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8")
        print(f"回执写入 {out}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
