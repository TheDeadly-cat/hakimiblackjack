# -*- coding: utf-8 -*-
"""固定牌区内定位牌矩形。无法定位则交给流程记为空区或拒识。"""
from __future__ import annotations

from typing import List, Tuple

from .contracts import LayoutProfile, RegionBox
from .deps import load_cv2
from .image_io import LoadedImage, rgb_to_bgr

Box = Tuple[int, int, int, int]


def _felt_ok(bgr, region: RegionBox, felt_kind: str = "green") -> bool:
    roi = bgr[region.y:region.y + region.h, region.x:region.x + region.w]
    if roi.size == 0:
        return False
    mean = roi.mean(axis=(0, 1))  # B, G, R
    blue, green, red = float(mean[0]), float(mean[1]), float(mean[2])
    if felt_kind == "navy":
        luma = 0.114 * blue + 0.587 * green + 0.299 * red
        return luma < 95 and blue + 6 >= red and green <= blue + 12
    return green > red + 8 and green > blue


def _split_wide(x: int, y: int, w: int, h: int, expected_w: int) -> List[Box]:
    if expected_w <= 0 or w < int(expected_w * 1.6):
        return [(x, y, w, h)]
    cols = max(1, int(round(w / expected_w)))
    cw = w / cols
    return [(int(x + k * cw), y, max(1, int(cw)), h) for k in range(cols)]


def detect_region_boxes(loaded: LoadedImage, region: RegionBox,
                        layout: LayoutProfile | None = None) -> List[Box]:
    cv2 = load_cv2()
    bgr = rgb_to_bgr(loaded)
    roi = bgr[region.y:region.y + region.h, region.x:region.x + region.w]
    if roi.size == 0:
        return []
    min_w = layout.detect_min_w if layout else 48
    min_h = layout.detect_min_h if layout else 70
    max_w = layout.detect_max_w if layout else 160
    max_h = layout.detect_max_h if layout else 220
    min_aspect = layout.detect_min_aspect if layout else 0.52
    max_aspect = layout.detect_max_aspect if layout else 0.92
    threshold = layout.detect_threshold if layout else 200
    split = layout.split_wide_clusters if layout else False
    expected_w = layout.expected_card_w if layout else 90

    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    _, th = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3) if split else (5, 5))
    th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes: List[Box] = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        x = max(0, x - 1)
        y = max(0, y - 1)
        w = min(roi.shape[1] - x, w + 2)
        h = min(roi.shape[0] - y, h + 2)
        if h < min_h or h > max_h or w < 8:
            continue
        if split and w >= int(expected_w * 1.6):
            pieces = _split_wide(x, y, w, h, expected_w)
        else:
            pieces = [(x, y, w, h)]
        if not split and (w < min_w or w > max_w):
            continue
        for px, py, pw, ph in pieces:
            if pw < min_w or ph < min_h or pw > max_w or ph > max_h:
                continue
            aspect = pw / ph
            if aspect < min_aspect or aspect > max_aspect:
                continue
            boxes.append((region.x + px, region.y + py, pw, ph))
    # 同一张牌落在重叠座位时，检测阶段不去重；pipeline 按座位分区。
    boxes.sort(key=lambda b: (b[0], b[1]))
    return boxes


def detect_all_regions(loaded: LoadedImage, layout: LayoutProfile) -> dict:
    bgr = rgb_to_bgr(loaded)
    if layout.felt_kind == "navy":
        full = RegionBox(0, 0, loaded.width, loaded.height)
        style_ok = _felt_ok(bgr, full, "navy")
        from .navy_cards import navy_detect_boxes
        raw = navy_detect_boxes(loaded, layout)
        found = {name: [] for name in layout.regions}
        for box in raw:
            cx = box[0] + box[2] / 2.0
            cy = box[1] + box[3] / 2.0
            best = None
            for name, region in layout.regions.items():
                if region.x <= cx <= region.x + region.w and region.y <= cy <= region.y + region.h:
                    area = region.w * region.h
                    if best is None or area < best[1]:
                        best = (name, area)
            if best:
                found[best[0]].append(box)
        return {
            "style_ok": style_ok,
            "region_style_ok": {name: style_ok for name in layout.regions},
            "boxes": found,
        }
    found = {}
    region_style_ok = {}
    for name, region in layout.regions.items():
        region_style_ok[name] = _felt_ok(bgr, region, layout.felt_kind)
        found[name] = detect_region_boxes(loaded, region, layout)
    return {
        "style_ok": all(region_style_ok.values()) if region_style_ok else False,
        "region_style_ok": region_style_ok,
        "boxes": found,
    }
