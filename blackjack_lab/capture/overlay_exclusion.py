"""Pixel-level overlay contamination check. Not F11/browser acceptance.

A source frame that contains the lab overlay color is not a table source.
Missing WGC or missing pixels stay failed, never a pass.
"""
from __future__ import annotations

from .contracts import CaptureRejected

OVERLAY_TITLE_MARKERS = ("Hakimi 辅助记牌", "原始画面 · 仅回看", "未定区域")
# BGR of the lab overlay backgrounds (#142b3a, #10202a, #243f50). Checklist only.
LAB_OVERLAY_BGR = ((0x3A, 0x2B, 0x14), (0x2A, 0x20, 0x10), (0x50, 0x3F, 0x24))


def is_lab_overlay(info):
    title = getattr(info, "title", None) or (info.get("title") if isinstance(info, dict) else "") or ""
    return any(marker in title for marker in OVERLAY_TITLE_MARKERS)


def refuse_overlay_source(info):
    if is_lab_overlay(info):
        raise CaptureRejected("不能把本工具浮层当作牌桌源帧")
    return info


def _pixels(frame):
    if frame is None:
        return []
    if hasattr(frame, "tolist"):
        data = frame.tolist()
        if data and data[0] and len(data[0][0]) >= 3:
            if len(data[0][0]) == 4:
                return [[tuple(px[:3]) for px in row] for row in data]
            return [[tuple(px[:3]) for px in row] for row in data]
        return []
    return frame


def overlay_pixel_fraction(frame, overlay_bgr, tolerance=18):
    rows = _pixels(frame)
    if not rows:
        return 1.0
    ob, og, or_ = (int(v) for v in overlay_bgr)
    hits = total = 0
    for row in rows:
        for px in row:
            total += 1
            b, g, r = (int(v) for v in px[:3])
            if abs(b - ob) <= tolerance and abs(g - og) <= tolerance and abs(r - or_) <= tolerance:
                hits += 1
    return hits / total if total else 1.0


def source_frame_excludes_overlay(frame, *, felt_bgr, overlay_bgr,
                                  max_overlay_fraction=0.01, min_felt_fraction=0.2):
    overlay_fraction = overlay_pixel_fraction(frame, overlay_bgr)
    felt_fraction = overlay_pixel_fraction(frame, felt_bgr)
    passed = (
        frame is not None
        and overlay_fraction <= max_overlay_fraction
        and felt_fraction >= min_felt_fraction
    )
    return {
        "item": "source_frame_excludes_overlay",
        "passed": passed,
        "overlay_fraction": overlay_fraction,
        "felt_fraction": felt_fraction,
        "not_fullscreen_acceptance": True,
        "note": "自建色块探测不是浏览器 F11 验收；浮层截图不能换签源帧",
    }


def source_frame_excludes_lab_overlays(frame, *, felt_bgr,
                                       max_overlay_fraction=0.01, min_felt_fraction=0.2):
    worst = None
    for overlay_bgr in LAB_OVERLAY_BGR:
        item = source_frame_excludes_overlay(
            frame, felt_bgr=felt_bgr, overlay_bgr=overlay_bgr,
            max_overlay_fraction=max_overlay_fraction, min_felt_fraction=min_felt_fraction)
        if worst is None or item["overlay_fraction"] > worst["overlay_fraction"]:
            worst = item
    body = dict(worst)
    body["checked_overlay_bgr"] = [list(item) for item in LAB_OVERLAY_BGR]
    return body
