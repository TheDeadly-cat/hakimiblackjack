# -*- coding: utf-8 -*-
"""13 种原始点数模板匹配。匹配度不是校准后的正确概率。"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from .contracts import (
    FACE_BACK, FACE_SHOWN, FACE_UNREADABLE, MODEL_ID, RANKS_13,
    RankHypothesis, validate_match_score, validate_rank,
)
from .deps import ImageRejected, load_cv2, load_numpy
from .image_io import LoadedImage, crop_rgb, sha256_bytes
from .synthetic import INDEX_H, INDEX_W, INDEX_X, INDEX_Y, CARD_H, CARD_W

# 在 synthetic-felt-v1 保守阈值：宁拒识，不强猜。
ACCEPT_SCORE = 0.85
MIN_MARGIN = 0.05
BACK_BLUE_GAP = 18.0


@dataclass
class TemplateBank:
    model_id: str
    digest: str
    templates: Dict[str, "object"]  # grayscale ndarray
    size: Tuple[int, int]


def _bgr_from_rgb_bytes(rgb: bytes, width: int, height: int):
    np = load_numpy()
    arr = np.frombuffer(rgb, dtype=np.uint8).reshape(height, width, 3)
    return arr[:, :, ::-1].copy()


def load_template_bank(directory: Path | str) -> TemplateBank:
    cv2 = load_cv2()
    np = load_numpy()
    directory = Path(directory)
    templates = {}
    hasher = hashlib.sha256()
    size = None
    for rank in RANKS_13:
        path = directory / f"{rank}.png"
        if not path.is_file():
            raise ImageRejected(f"缺少点数模板: {path}")
        data = path.read_bytes()
        hasher.update(rank.encode("ascii"))
        hasher.update(data)
        arr = np.frombuffer(data, dtype=np.uint8)
        bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if bgr is None:
            raise ImageRejected(f"无法读取模板 {path}")
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        templates[rank] = gray
        size = (int(gray.shape[1]), int(gray.shape[0]))
        validate_rank(rank)
    return TemplateBank(MODEL_ID, hasher.hexdigest(), templates, size or (INDEX_W, INDEX_H))


def load_template_bank_optional(directory: Path | str | None) -> TemplateBank:
    """旁观样式可以先没有模板；此时只出框，点数等人确认。"""
    if directory is None:
        return TemplateBank("human-confirm-only", "0" * 64, {}, (16, 16))
    path = Path(directory)
    if all((path / f"{rank}.png").is_file() for rank in RANKS_13):
        return load_template_bank(path)
    return TemplateBank("human-confirm-only", "0" * 64, {}, (16, 16))


def _looks_like_back(bgr) -> bool:
    mean = bgr.mean(axis=(0, 1))
    blue, green, red = float(mean[0]), float(mean[1]), float(mean[2])
    return blue > red + BACK_BLUE_GAP and blue > green + 8


def match_index(index_gray, bank: TemplateBank) -> List[RankHypothesis]:
    cv2 = load_cv2()
    tw, th = bank.size
    search = index_gray
    if search.shape[0] < th or search.shape[1] < tw:
        search = cv2.resize(
            search,
            (max(tw, int(search.shape[1])), max(th, int(search.shape[0]))),
            interpolation=cv2.INTER_LINEAR,
        )
    scored = []
    for rank, tmpl in bank.templates.items():
        result = cv2.matchTemplate(search, tmpl, cv2.TM_CCOEFF_NORMED)
        score = float(result.max())
        scored.append(RankHypothesis(rank, validate_match_score(score), rank))
    scored.sort(key=lambda h: h.match_score, reverse=True)
    return scored[:3]


def recognize_card(loaded: LoadedImage, card_box: Sequence[int],
                   bank: TemplateBank) -> dict:
    x, y, w, h = (int(v) for v in card_box)
    card_rgb = crop_rgb(loaded, x, y, w, h)
    card_bgr = _bgr_from_rgb_bytes(card_rgb, w, h)
    crop_digest = sha256_bytes(card_rgb)
    if not bank.templates:
        from .navy_cards import recognize_navy_face
        extra = recognize_navy_face(card_bgr)
        extra.update({
            "crop_sha256": crop_digest,
            "crop_rgb": card_rgb,
            "crop_size": (w, h),
        })
        return extra
    if _looks_like_back(card_bgr):
        return {
            "rank_candidates": [],
            "reject_reason": "疑似牌背，未作为可见点数",
            "face_state_candidate": FACE_BACK,
            "crop_sha256": crop_digest,
            "crop_rgb": card_rgb,
            "crop_size": (w, h),
        }
    cv2 = load_cv2()
    interp = cv2.INTER_AREA if (w > CARD_W or h > CARD_H) else cv2.INTER_LINEAR
    canonical = cv2.resize(card_bgr, (CARD_W, CARD_H), interpolation=interp)
    pad = 4
    y0 = max(0, INDEX_Y - pad)
    x0 = max(0, INDEX_X - pad)
    y1 = min(CARD_H, INDEX_Y + INDEX_H + pad)
    x1 = min(CARD_W, INDEX_X + INDEX_W + pad)
    index_bgr = canonical[y0:y1, x0:x1]
    if index_bgr.size == 0:
        return {
            "rank_candidates": [],
            "reject_reason": "角标区域无效或越界",
            "face_state_candidate": FACE_UNREADABLE,
            "crop_sha256": crop_digest,
            "crop_rgb": card_rgb,
            "crop_size": (w, h),
        }
    index_gray = cv2.cvtColor(index_bgr, cv2.COLOR_BGR2GRAY)
    if float(index_bgr.mean()) < 40:
        return {
            "rank_candidates": match_index(index_gray, bank),
            "reject_reason": "角标过暗或被遮挡，待核对",
            "face_state_candidate": FACE_UNREADABLE,
            "crop_sha256": crop_digest,
            "crop_rgb": card_rgb,
            "crop_size": (w, h),
        }
    candidates = match_index(index_gray, bank)
    best = candidates[0].match_score if candidates else -1.0
    second = candidates[1].match_score if len(candidates) > 1 else -1.0
    if best < ACCEPT_SCORE:
        return {
            "rank_candidates": candidates,
            "reject_reason": "最高匹配度过低，待核对／无法识别",
            "face_state_candidate": FACE_UNREADABLE,
            "crop_sha256": crop_digest,
            "crop_rgb": card_rgb,
            "crop_size": (w, h),
        }
    if best - second < MIN_MARGIN:
        return {
            "rank_candidates": candidates,
            "reject_reason": "前两名匹配度接近，不猜测点数",
            "face_state_candidate": FACE_UNREADABLE,
            "crop_sha256": crop_digest,
            "crop_rgb": card_rgb,
            "crop_size": (w, h),
        }
    return {
        "rank_candidates": candidates,
        "reject_reason": None,
        "face_state_candidate": FACE_SHOWN,
        "crop_sha256": crop_digest,
        "crop_rgb": card_rgb,
        "crop_size": (w, h),
    }
