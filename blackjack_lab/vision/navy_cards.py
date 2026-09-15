# -*- coding: utf-8 -*-
"""深色桌检牌与角标比分。

思路来自公开的 OpenCV 扑克检测（找亮斑、拉正角标、和点数图做差），
不是拷贝其源码，也不引入 YOLO。匹配度仍不是正确概率。
"""
from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

from .contracts import FACE_SHOWN, FACE_UNREADABLE, RANKS_13, RankHypothesis, validate_match_score
from .deps import load_cv2, load_numpy
from .image_io import LoadedImage, rgb_to_bgr

Box = Tuple[int, int, int, int]

RANK_W, RANK_H = 56, 96
NAVY_ACCEPT = 0.62
NAVY_MARGIN = 0.04


def _normalise_rank_mask(mask):
    """模板和观测使用相同紧裁/等比留边，避免把有留白模板与拉伸角标相比。"""
    cv2, np = load_cv2(), load_numpy()
    points = cv2.findNonZero((mask > 100).astype(np.uint8))
    if points is None:
        return None
    x, y, w, h = cv2.boundingRect(points)
    scale = min((RANK_W - 6) / w, (RANK_H - 6) / h)
    sw, sh = max(1, round(w * scale)), max(1, round(h * scale))
    canvas = np.zeros((RANK_H, RANK_W), dtype=np.uint8)
    ox, oy = (RANK_W - sw) // 2, (RANK_H - sh) // 2
    canvas[oy:oy+sh, ox:ox+sw] = cv2.resize(mask[y:y+h, x:x+w], (sw,sh), interpolation=cv2.INTER_AREA)
    return canvas


def _hershey_rank(rank: str):
    cv2 = load_cv2()
    np = load_numpy()
    img = np.zeros((RANK_H, RANK_W), dtype=np.uint8)
    text = "10" if rank == "10" else rank
    scale = 1.15 if rank == "10" else 1.7
    thick = 2
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thick)
    x = max(1, (RANK_W - tw) // 2)
    y = min(RANK_H - 4, (RANK_H + th) // 2)
    cv2.putText(img, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, 255, thick, cv2.LINE_AA)
    return _normalise_rank_mask(img)


def built_in_rank_templates() -> Dict[str, "object"]:
    return {rank: _hershey_rank(rank) for rank in RANKS_13}


def _white_ink_ratios(bgr) -> Tuple[float, float]:
    np = load_numpy()
    cv2 = load_cv2()
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    white = (hsv[:, :, 2] >= 150) & (hsv[:, :, 1] <= 90)
    ink = (hsv[:, :, 2] <= 90) | ((hsv[:, :, 1] >= 80) & (hsv[:, :, 2] >= 70))
    n = float(bgr.shape[0] * bgr.shape[1]) or 1.0
    return float(white.mean() if hasattr(white, "mean") else np.mean(white)), float(np.mean(ink))


def looks_like_card_face(bgr) -> bool:
    if bgr is None or bgr.size == 0:
        return False
    white, ink = _white_ink_ratios(bgr)
    return white >= 0.28 and ink >= 0.015


def isolate_rank_ink(bgr):
    """放大左上角，抽出墨迹，缩放到固定点数图。借鉴公开 OpenCV 扑克检测的角标流程。"""
    cv2 = load_cv2()
    np = load_numpy()
    h, w = bgr.shape[:2]
    corner = bgr[0:max(1, int(h * 0.55)), 0:max(1, int(w * 0.55))]
    if corner.size == 0:
        return None
    zoom = cv2.resize(corner, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(zoom, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    white = int(np.median(blur[blur > 120])) if np.any(blur > 120) else int(blur.max())
    _, inv = cv2.threshold(blur, max(1, white - 40), 255, cv2.THRESH_BINARY_INV)
    contours, _ = cv2.findContours(inv, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    x, y, rw, rh = cv2.boundingRect(max(contours, key=cv2.contourArea))
    if rw < 4 or rh < 6:
        return None
    pad = 3
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(inv.shape[1], x + rw + pad), min(inv.shape[0], y + rh + pad)
    roi = inv[y0:y1, x0:x1]
    return _normalise_rank_mask(roi)


def match_rank_ink(rank_img, templates: Dict[str, "object"]) -> List[RankHypothesis]:
    cv2 = load_cv2()
    scored = []
    for rank, tmpl in templates.items():
        if tmpl.shape != rank_img.shape:
            tmpl = cv2.resize(tmpl, (rank_img.shape[1], rank_img.shape[0]))
        ncc = cv2.matchTemplate(rank_img, tmpl, cv2.TM_CCOEFF_NORMED)
        score = float(ncc.max()) if ncc.size else -1.0
        scored.append(RankHypothesis(rank, validate_match_score(max(-1.0, min(1.0, score))), rank))
    scored.sort(key=lambda h: h.match_score, reverse=True)
    return scored[:3]


def recognize_navy_face(bgr) -> dict:
    if not looks_like_card_face(bgr):
        return {
            "rank_candidates": [],
            "reject_reason": "更像绒面印刷或空块，请拒绝或人工确认",
            "face_state_candidate": FACE_UNREADABLE,
        }
    ink = isolate_rank_ink(bgr)
    if ink is None:
        return {
            "rank_candidates": [],
            "reject_reason": "角标墨迹不足，请人工确认牌面",
            "face_state_candidate": FACE_UNREADABLE,
        }
    candidates = match_rank_ink(ink, built_in_rank_templates())
    best = candidates[0].match_score if candidates else -1.0
    second = candidates[1].match_score if len(candidates) > 1 else -1.0
    if best < NAVY_ACCEPT:
        return {
            "rank_candidates": candidates,
            "reject_reason": "角标匹配不足，请人工确认牌面",
            "face_state_candidate": FACE_UNREADABLE,
        }
    if best - second < NAVY_MARGIN:
        return {
            "rank_candidates": candidates,
            "reject_reason": "前两名接近，不猜测点数",
            "face_state_candidate": FACE_UNREADABLE,
        }
    return {
        "rank_candidates": candidates,
        "reject_reason": None,
        "face_state_candidate": FACE_SHOWN,
    }


def navy_detect_boxes(loaded: LoadedImage, layout) -> List[Box]:
    cv2 = load_cv2()
    bgr = rgb_to_bgr(loaded)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    felt = int(blur.mean())
    _, th = cv2.threshold(blur, min(220, felt + 70), 255, cv2.THRESH_BINARY)
    th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
    contours, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes: List[Box] = []
    expected = layout.expected_card_w
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if h < layout.detect_min_h or h > layout.detect_max_h:
            continue
        pieces: Sequence[Tuple[int, int, int, int]]
        if layout.split_wide_clusters and w >= int(expected * 1.6):
            cols = max(1, int(round(w / expected)))
            cw = w / cols
            pieces = [(int(x + k * cw), y, max(1, int(cw)), h) for k in range(cols)]
        else:
            pieces = [(x, y, w, h)]
        for px, py, pw, ph in pieces:
            if pw < layout.detect_min_w or ph < layout.detect_min_h:
                continue
            if pw > layout.detect_max_w or ph > layout.detect_max_h:
                continue
            aspect = pw / ph
            if aspect < layout.detect_min_aspect or aspect > layout.detect_max_aspect:
                continue
            roi = bgr[py:py + ph, px:px + pw]
            white, _ink = _white_ink_ratios(roi)
            if white < 0.28:
                continue
            boxes.append((px, py, pw, ph))
    return _nms(boxes, 0.45)


def _nms(boxes: List[Box], iou_min: float) -> List[Box]:
    kept: List[Box] = []
    for box in sorted(boxes, key=lambda b: b[2] * b[3], reverse=True):
        ax2, ay2 = box[0] + box[2], box[1] + box[3]
        overlap = False
        for other in kept:
            bx2, by2 = other[0] + other[2], other[1] + other[3]
            ix1, iy1 = max(box[0], other[0]), max(box[1], other[1])
            ix2, iy2 = min(ax2, bx2), min(ay2, by2)
            inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
            union = box[2] * box[3] + other[2] * other[3] - inter
            if union and inter / union >= iou_min:
                overlap = True
                break
        if not overlap:
            kept.append(box)
    kept.sort(key=lambda b: (b[0], b[1]))
    return kept
