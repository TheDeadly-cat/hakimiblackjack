# -*- coding: utf-8 -*-
"""标定分析：在实时帧上量出牌的真实尺寸与倾角。

只读本地已抓好的标定帧，输出尺寸/角度分布与带框图，供人工核对。
不入账、不改原帧、不声称识别达标。

    python scripts/analyze_live_cards.py .local-evidence/live-calib-20260912
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def analyse_frame(cv2, np, path: Path, *, roi, min_area, max_area):
    bgr = cv2.imread(str(path))
    if bgr is None:
        raise SystemExit(f"无法读取 {path}")
    x0, y0, x1, y1 = roi
    region = bgr[y0:y1, x0:x1]

    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    # 牌面是高亮低饱和的白块；深蓝绒面与筹码都不满足
    mask = ((hsv[:, :, 2] >= 150) & (hsv[:, :, 1] <= 80)).astype(np.uint8) * 255
    mask = cv2.morphologyEx(
        mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
    mask = cv2.morphologyEx(
        mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)))

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cards = []
    annotated = region.copy()
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < min_area or area > max_area:
            continue
        rect = cv2.minAreaRect(contour)
        (cx, cy), (w, h), angle = rect
        if w < 1 or h < 1:
            continue
        long_side, short_side = max(w, h), min(w, h)
        aspect = short_side / long_side
        # 扑克牌短边/长边约 0.70；放宽到 0.5–0.95 以容忍遮挡与透视
        if not 0.5 <= aspect <= 0.95:
            continue
        fill = area / (w * h)
        if fill < 0.75:            # minAreaRect 填充率低说明不是矩形牌
            continue
        # OpenCV 的角度定义随边长顺序跳变，统一折算到 -45..45
        norm_angle = angle if w < h else angle - 90
        while norm_angle < -45:
            norm_angle += 90
        while norm_angle > 45:
            norm_angle -= 90
        cards.append({
            "center": [round(cx + x0, 1), round(cy + y0, 1)],
            "long": round(long_side, 1),
            "short": round(short_side, 1),
            "aspect": round(aspect, 3),
            "angle": round(norm_angle, 1),
            "fill": round(fill, 3),
        })
        box = np.int32(cv2.boxPoints(rect))
        cv2.drawContours(annotated, [box], 0, (0, 255, 0), 2)

    return cards, annotated, mask


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="量出实时帧里牌的尺寸与倾角")
    parser.add_argument("directory", help="标定帧目录")
    parser.add_argument("--roi", default="600,900,2300,1300",
                        help="牌阵搜索范围 x0,y0,x1,y1")
    parser.add_argument("--min-area", type=float, default=1500)
    parser.add_argument("--max-area", type=float, default=20000)
    args = parser.parse_args(argv)

    import cv2
    import numpy as np

    roi = tuple(int(v) for v in args.roi.split(","))
    directory = Path(args.directory)
    frames = sorted(directory.glob("frame-*.png"))
    if not frames:
        raise SystemExit(f"{directory} 下没有 frame-*.png")

    per_frame = {}
    all_cards = []
    for path in frames:
        cards, annotated, mask = analyse_frame(
            cv2, np, path, roi=roi, min_area=args.min_area, max_area=args.max_area)
        per_frame[path.name] = len(cards)
        all_cards.extend(cards)
        cv2.imwrite(str(directory / f"boxes-{path.stem}.png"), annotated)
    cv2.imwrite(str(directory / "mask-frame-00.png"), mask)

    print(f"分析 {len(frames)} 帧，共命中 {len(all_cards)} 个牌形候选")
    for name, count in per_frame.items():
        print(f"  {name}: {count}")

    if not all_cards:
        print("没有命中任何牌形，需要调整 ROI 或阈值。")
        return 1

    longs = [c["long"] for c in all_cards]
    shorts = [c["short"] for c in all_cards]
    angles = [c["angle"] for c in all_cards]
    summary = {
        "frames": len(frames),
        "candidates": len(all_cards),
        "long_side": {"min": min(longs), "median": round(statistics.median(longs), 1),
                      "max": max(longs)},
        "short_side": {"min": min(shorts), "median": round(statistics.median(shorts), 1),
                       "max": max(shorts)},
        "angle_deg": {"min": min(angles), "median": round(statistics.median(angles), 1),
                      "max": max(angles),
                      "abs_p90": round(sorted(abs(a) for a in angles)[int(len(angles) * 0.9)], 1)},
        "roi": list(roi),
    }
    print("\n=== 尺寸与倾角 ===")
    print(f"长边 中位 {summary['long_side']['median']}  "
          f"范围 {summary['long_side']['min']}–{summary['long_side']['max']}")
    print(f"短边 中位 {summary['short_side']['median']}  "
          f"范围 {summary['short_side']['min']}–{summary['short_side']['max']}")
    print(f"倾角 中位 {summary['angle_deg']['median']}°  "
          f"范围 {summary['angle_deg']['min']}°–{summary['angle_deg']['max']}°  "
          f"|角度| 九成位 {summary['angle_deg']['abs_p90']}°")

    (directory / "card-geometry.json").write_text(
        json.dumps({"summary": summary, "cards": all_cards},
                   ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\n写入 {directory / 'card-geometry.json'}，带框图 boxes-frame-*.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
