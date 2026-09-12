# -*- coding: utf-8 -*-
"""从实时帧里抽出角标字形，作为真实模板库的原料。

牌在桌上是扇形叠放的，整张牌的轮廓会连成一片，按整牌找框注定失败；
但点数角标恰恰因为扇形摆放而始终露在外面。所以这里以字形为单位：
白色牌面里的深色连通块 = 点数、花色、条码，再按形状筛出点数候选。

输出一张拼版图供人工核对，不自动认点、不入账。

    python scripts/extract_rank_glyphs.py .local-evidence/live-calib-20260912
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

# 角标字形的形状范围。宽松一些，先看清真实分布再收紧。
MIN_H, MAX_H = 14, 52
MIN_W, MAX_W = 7, 46
MIN_AREA = 70
MAX_FILL = 0.92          # 实心方块多半是条码或色块，不是字
TILE = 64
# 绒面印刷字（INSURANCE PAYS 等）是孤立小白块，牌体要大得多。
# 只填够大的白块，印刷字的负空间就不会被当成字形。
CARD_BODY_MIN_AREA = 2500


def card_body_mask(cv2, np, region):
    """白色牌面区域，并把字形挖出的洞补上，得到完整牌体。"""
    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    white = ((hsv[:, :, 2] >= 150) & (hsv[:, :, 1] <= 80)).astype(np.uint8) * 255
    white = cv2.morphologyEx(
        white, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
    filled = np.zeros_like(white)
    contours, _ = cv2.findContours(white, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    kept = [c for c in contours if cv2.contourArea(c) >= CARD_BODY_MIN_AREA]
    cv2.drawContours(filled, kept, -1, 255, thickness=cv2.FILLED)
    return white, filled


def ink_kind(np, crop, mask_crop):
    """区分牌上的黑/红油墨与绒面蓝。返回 'black' / 'red' / 'felt'。"""
    pixels = crop[mask_crop > 0]
    if pixels.size == 0:
        return "felt"
    blue, green, red = (float(v) for v in pixels.mean(axis=0))
    if blue > red + 18:          # 深蓝绒面：蓝通道明显占优
        return "felt"
    if red > blue + 35 and red > green + 35:
        return "red"
    if max(blue, green, red) <= 130:
        return "black"
    return "felt"


def extract(cv2, np, path: Path, roi):
    bgr = cv2.imread(str(path))
    if bgr is None:
        raise SystemExit(f"无法读取 {path}")
    x0, y0, x1, y1 = roi
    region = bgr[y0:y1, x0:x1]
    white, filled = card_body_mask(cv2, np, region)

    # 牌体内部的深色 = 字形 / 花色 / 条码
    holes = cv2.bitwise_and(filled, cv2.bitwise_not(white))
    holes = cv2.morphologyEx(
        holes, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2)))

    count, labels, stats, _ = cv2.connectedComponentsWithStats(holes, connectivity=8)
    glyphs = []
    annotated = region.copy()
    for index in range(1, count):
        x, y, w, h, area = stats[index]
        if not (MIN_H <= h <= MAX_H and MIN_W <= w <= MAX_W):
            continue
        if area < MIN_AREA:
            continue
        fill = area / float(w * h)
        if fill > MAX_FILL:
            continue
        crop = region[y:y + h, x:x + w]
        component = (labels[y:y + h, x:x + w] == index).astype(np.uint8)
        kind = ink_kind(np, crop, component)
        if kind == "felt":
            cv2.rectangle(annotated, (x, y), (x + w, y + h), (0, 0, 255), 1)
            continue
        glyphs.append({
            "bbox": [int(x + x0), int(y + y0), int(w), int(h)],
            "area": int(area),
            "fill": round(fill, 3),
            "aspect": round(w / h, 3),
            "ink": kind,
            "_crop": crop,
        })
        cv2.rectangle(annotated, (x, y), (x + w, y + h), (0, 255, 0), 1)
    return glyphs, annotated


def contact_sheet(cv2, np, glyphs, columns=16):
    if not glyphs:
        return None
    rows = (len(glyphs) + columns - 1) // columns
    sheet = np.full((rows * TILE, columns * TILE, 3), 40, dtype=np.uint8)
    for index, glyph in enumerate(glyphs):
        crop = glyph["_crop"]
        h, w = crop.shape[:2]
        scale = min((TILE - 8) / w, (TILE - 8) / h)
        resized = cv2.resize(crop, (max(1, int(w * scale)), max(1, int(h * scale))),
                             interpolation=cv2.INTER_CUBIC)
        rh, rw = resized.shape[:2]
        r, c = divmod(index, columns)
        oy = r * TILE + (TILE - rh) // 2
        ox = c * TILE + (TILE - rw) // 2
        sheet[oy:oy + rh, ox:ox + rw] = resized
    return sheet


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="抽出角标字形供人工核对")
    parser.add_argument("directory")
    parser.add_argument("--roi", default="600,900,2300,1300")
    parser.add_argument("--frames", type=int, default=3)
    args = parser.parse_args(argv)

    import cv2
    import numpy as np

    roi = tuple(int(v) for v in args.roi.split(","))
    directory = Path(args.directory)
    frames = sorted(directory.glob("frame-*.png"))[:args.frames]
    if not frames:
        raise SystemExit("没有标定帧")

    every = []
    for path in frames:
        glyphs, annotated = extract(cv2, np, path, roi)
        cv2.imwrite(str(directory / f"glyphs-{path.stem}.png"), annotated)
        print(f"{path.name}: 字形候选 {len(glyphs)}")
        every.extend(glyphs)

    if not every:
        print("没有抽到字形，需要调整阈值。")
        return 1

    sheet = contact_sheet(cv2, np, every)
    sheet_path = directory / "glyph-sheet.png"
    cv2.imwrite(str(sheet_path), sheet)

    heights = [g["bbox"][3] for g in every]
    widths = [g["bbox"][2] for g in every]
    print(f"\n共 {len(every)} 个字形候选")
    print(f"高 中位 {statistics.median(heights):.0f}  范围 {min(heights)}–{max(heights)}")
    print(f"宽 中位 {statistics.median(widths):.0f}  范围 {min(widths)}–{max(widths)}")
    print(f"拼版图 {sheet_path}")

    (directory / "glyph-candidates.json").write_text(
        json.dumps([{k: v for k, v in g.items() if k != "_crop"} for g in every],
                   ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
