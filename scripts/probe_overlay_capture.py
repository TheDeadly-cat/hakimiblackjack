# -*- coding: utf-8 -*-
"""实验：记牌器窗口盖在牌桌窗口上，捕获还成立吗？

单显示器时只能把记牌器叠在牌桌上，于是必须先回答两件事：
1. 遮挡窗口的像素会不会串进捕获结果？
2. 被遮挡（含完全盖住）之后，目标窗口还会不会继续出帧？

全程只用本脚本自己开的两个窗口，不碰真实牌桌、账号或桌面内容。

    python scripts/probe_overlay_capture.py --output .local-evidence/overlay-probe
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from blackjack_lab.capture.wgc_source import open_window_source, wgc_available  # noqa: E402
from blackjack_lab.capture.window_list import find_window_by_title  # noqa: E402

TARGET_TITLE = "HAKIMI-OVERLAY-TARGET"
COVER_TITLE = "HAKIMI-OVERLAY-COVER"

# 目标窗口在这三种颜色之间循环；遮挡窗口用洋红，任何一种都不在目标调色板里。
PALETTE = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
COVER_RGB = (255, 0, 255)

TARGET_SOURCE = f'''
import tkinter as tk
PALETTE = {PALETTE!r}
root = tk.Tk()
root.title({TARGET_TITLE!r})
root.geometry("520x380+150+150")
panel = tk.Frame(root, bg="#%02x%02x%02x" % tuple(PALETTE[0]))
panel.pack(fill="both", expand=True)
state = {{"i": 0}}

def tick():
    state["i"] = (state["i"] + 1) % len(PALETTE)
    panel.configure(bg="#%02x%02x%02x" % tuple(PALETTE[state["i"]]))
    root.after(200, tick)

root.after(200, tick)
root.mainloop()
'''

COVER_SOURCE = f'''
import tkinter as tk
root = tk.Tk()
root.title({COVER_TITLE!r})
root.geometry("{{}}x{{}}+{{}}+{{}}".format(*__import__("sys").argv[1:5]))
root.attributes("-topmost", True)
tk.Frame(root, bg="#%02x%02x%02x" % {COVER_RGB!r}).pack(fill="both", expand=True)
root.mainloop()
'''


def classify(bgr_pixel):
    """返回 (最接近的名字, 距离)。名字是 target-<索引> 或 cover。"""
    blue, green, red = (int(v) for v in bgr_pixel)
    best, best_dist = None, None
    options = [(f"target-{i}", rgb) for i, rgb in enumerate(PALETTE)]
    options.append(("cover", COVER_RGB))
    for name, (pr, pg, pb) in options:
        dist = abs(red - pr) + abs(green - pg) + abs(blue - pb)
        if best_dist is None or dist < best_dist:
            best, best_dist = name, dist
    return best, best_dist


def sample(source, seconds):
    """采样一段时间，返回中心像素分类结果。"""
    seen = []
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        packet = source.latest()
        if packet is not None:
            cy, cx = packet.height // 2, packet.width // 2
            name, dist = classify(packet.pixels[cy, cx])
            seen.append({"frame_id": packet.frame_id, "class": name, "distance": dist})
        time.sleep(0.02)
    return seen


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="遮挡下的窗口捕获实验")
    parser.add_argument("--output")
    parser.add_argument("--phase-seconds", type=float, default=3.0)
    args = parser.parse_args(argv)

    if not wgc_available():
        print("未安装 windows-capture，实验跳过。")
        return 2

    target = subprocess.Popen([sys.executable, "-c", TARGET_SOURCE])
    cover = None
    source = None
    try:
        info = None
        deadline = time.perf_counter() + 8.0
        while time.perf_counter() < deadline:
            info = find_window_by_title(TARGET_TITLE)
            if info:
                break
            time.sleep(0.1)
        if info is None:
            print("目标窗口没有出现。")
            return 1
        print(f"目标窗口 hwnd={info.hwnd} {info.width}x{info.height} @({info.x},{info.y})")

        source = open_window_source(info.hwnd, target_fps=10.0)
        source.start()

        print("阶段一：无遮挡 …")
        clear = sample(source, args.phase_seconds)

        # 完全盖住：遮挡窗口比目标大一圈，且置顶
        print("阶段二：用置顶窗口完全盖住目标 …")
        cover = subprocess.Popen([
            sys.executable, "-c", COVER_SOURCE,
            str(info.width + 60), str(info.height + 60),
            str(info.x - 30), str(info.y - 30),
        ])
        time.sleep(1.5)
        covered = sample(source, args.phase_seconds)
        report = source.report()
    finally:
        for proc in (cover, target):
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:  # pragma: no cover
                    proc.kill()
        if source is not None:
            source.stop()

    covered_targets = [s for s in covered if s["class"].startswith("target")]
    covered_bleed = [s for s in covered if s["class"] == "cover"]

    checks = {
        "frames_before_cover": len(clear) >= 2,
        "frames_still_arrive_while_fully_covered": len(covered) >= 2,
        "no_cover_pixels_bleed_into_capture": len(covered_bleed) == 0,
        "still_sees_target_colours_while_covered": len(covered_targets) >= 2,
    }
    passed = all(checks.values())

    print("\n=== 实验结果 ===")
    for name, ok in checks.items():
        print(f"  {'OK  ' if ok else 'FAIL'}  {name}")
    print(f"\n无遮挡采样 {len(clear)} 帧；完全遮挡采样 {len(covered)} 帧，"
          f"其中看到目标颜色 {len(covered_targets)}，串入遮挡颜色 {len(covered_bleed)}")
    print(f"到达 {report['arrived']} / 接受 {report['accepted']}")
    print(f"结论：{'窗口捕获不受遮挡影响' if passed else '遮挡会影响捕获，需另想办法'}")
    print("注意：本实验用的是 Tk 窗口。浏览器另有「完全遮挡就降帧」的省电策略，"
          "需要用真实牌桌窗口单独验证。")

    if args.output:
        out = Path(args.output)
        out.mkdir(parents=True, exist_ok=True)
        (out / "overlay-probe.json").write_text(
            json.dumps({
                "passed": passed, "checks": checks,
                "clear_phase": clear, "covered_phase": covered,
                "capture_report": report,
                "caveat": "Tk 窗口，不代表浏览器的遮挡降帧策略。",
            }, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        print(f"回执写入 {out}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
