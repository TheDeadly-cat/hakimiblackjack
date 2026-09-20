# -*- coding: utf-8 -*-
"""真实牌桌的角标读取：字形抽取 + 旋转穷举匹配。

为什么是这个形状（都是实测排除出来的，别退回去）：

- 不按整张牌找框：牌扇形叠放，白色牌面连成一片，整牌 minAreaRect 在 10 帧上命中 0。
- 不先求方向再摆正：字形自身 minAreaRect 对 8/A 这类近方形字有 90° 歧义，
  白掩膜边缘 Hough 在同一段里角度跨 −90°..86°。改为匹配时穷举旋转，
  顺带解决右下角标倒 180° 的问题。
- 不用 Hershey 笔画字体当模板：那是 navy_cards.py 把 A 认成 Q 的根因。
  改用系统真实字体渲染，再用真实画面里的高分样本回炉成真实模板。
- 用 matchTemplate 滑动匹配而不是整图相关：连通块经常把点数和花色连在一起，
  滑动才能在里面找到点数那一段。

匹配分数是匹配度，不是校准后的正确概率。本模块只产生候选，不写账本。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .contracts import RANKS_13
from .deps import ImageRejected, load_cv2, load_numpy

# 角标字形的形状范围（实测：高中位 19px，全屏 2560x1440）
MIN_H, MAX_H = 14, 52
MIN_W, MAX_W = 7, 52
MIN_AREA = 70
MAX_FILL = 0.92
# 叠牌之间的阴影缝也会变成牌体内部的深色块，而且又扁又长。
# 真实俯视透视会压扁角标。原帧 card-00179 的 Q 为 1.91--2.29；
# 旧 1.5 会在标注前丢掉整类。放宽只产生候选，花色/阴影仍由拒识与人工核对处理。
MAX_ASPECT = 2.6
EXTRACTION_VERSION = "navy-components-border-3"
LEGACY_MAX_W = 46
CARD_BORDER_CLOSE = 3
# 绒面印刷字是孤立小白块；牌体要大得多。只填够大的白块，
# 印刷字的负空间就不会被误当成字形。
CARD_BODY_MIN_AREA = 2500

# 模板高度（画布像素）。所有比对都归一到这个高度。
TEMPLATE_H = 32
# 粗扫步长与细化步长（度）
COARSE_STEP = 20
REFINE_SPAN = 12
REFINE_STEP = 4
# 连通块相对模板高度的倍数。点数与花色黏连时整块会是模板的两倍多高。
SCALES = (1.0, 1.5, 2.0, 2.6)

FONT_CANDIDATES = (
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/ariblk.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/calibrib.ttf",
)

BANK_SCHEMA = "0.3d-rank-bank-1"


@dataclass
class Glyph:
    """牌面上的一块深色连通区域：点数、花色或两者黏连。"""

    bbox: Tuple[int, int, int, int]
    mask: Any = field(repr=False)          # 紧裁的二值 uint8
    ink: str = "black"                     # black / red
    area: int = 0

    @property
    def height(self) -> int:
        return self.bbox[3]

    @property
    def width(self) -> int:
        return self.bbox[2]


@dataclass
class RankMatch:
    rank: str
    score: float
    angle: float
    scale: float

    def as_dict(self) -> Dict[str, Any]:
        return {
            "rank": self.rank,
            "score": round(self.score, 4),
            "angle": self.angle,
            "scale": self.scale,
            "score_is_calibrated_probability": False,
        }


# ---------------- 字形抽取 ----------------

def _ink_kind(np, crop, component) -> str:
    """牌上的墨是黑或红，绒面是深蓝。蓝通道明显占优的判为绒面。"""
    pixels = crop[component > 0]
    if pixels.size == 0:
        return "felt"
    blue, green, red = (float(v) for v in pixels.mean(axis=0))
    if blue > red + 18:
        return "felt"
    if red > blue + 35 and red > green + 35:
        return "red"
    if max(blue, green, red) <= 130:
        return "black"
    return "felt"


def card_body_mask(bgr, *, close_border: bool = True):
    """白色牌面及牌体；仅在轮廓副本闭合 1--2px 边缘缺口，不改墨迹白掩膜。"""
    cv2, np = load_cv2(), load_numpy()
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    white = ((hsv[:, :, 2] >= 150) & (hsv[:, :, 1] <= 80)).astype(np.uint8) * 255
    white = cv2.morphologyEx(
        white, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
    filled = np.zeros_like(white)
    # 角标碰到牌缘时，RETR_EXTERNAL 会把它当外部背景而非内部洞，整字消失。
    # 00349 顶部 10 的此机制由 3x3 闭运算恢复；保持 white 原样以保留字符孔洞。
    outline = (cv2.morphologyEx(white, cv2.MORPH_CLOSE,
                              cv2.getStructuringElement(cv2.MORPH_RECT,
                                                        (CARD_BORDER_CLOSE, CARD_BORDER_CLOSE)))
               if close_border else white)
    contours, _ = cv2.findContours(outline, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    kept = [c for c in contours if cv2.contourArea(c) >= CARD_BODY_MIN_AREA]
    cv2.drawContours(filled, kept, -1, 255, thickness=cv2.FILLED)
    return white, filled


def _glyph_components(bgr, *, close_border: bool = True):
    cv2, np = load_cv2(), load_numpy()
    white, filled = card_body_mask(bgr, close_border=close_border)
    holes = cv2.bitwise_and(filled, cv2.bitwise_not(white))
    holes = cv2.morphologyEx(
        holes, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2)))

    count, labels, stats, _ = cv2.connectedComponentsWithStats(holes, connectivity=8)
    for index in range(1, count):
        x, y, w, h, area = stats[index]
        reasons = []
        if not (MIN_H <= h <= MAX_H and MIN_W <= w <= MAX_W):
            reasons.append("size")
        if area < MIN_AREA or area / float(w * h) > MAX_FILL:
            reasons.append("area_or_fill")
        if w / float(h) > MAX_ASPECT:
            reasons.append("aspect")
        component = (labels[y:y + h, x:x + w] == index).astype(np.uint8)
        kind = _ink_kind(np, bgr[y:y + h, x:x + w], component)
        if kind == "felt":
            reasons.append("ink")
        yield Glyph(bbox=(int(x), int(y), int(w), int(h)),
                    mask=component * 255, ink=kind, area=int(area)), reasons


def extract_glyphs(bgr) -> List[Glyph]:
    """抽出牌体内部的深色连通块。不认点数，只给候选。"""
    return [glyph for glyph, reasons in _glyph_components(bgr) if not reasons]


def extraction_diagnostics(bgr) -> List[dict]:
    """同时记录被过滤的连通块；没有连通块的漏检仍需原帧人工框选。"""
    _, old_body = card_body_mask(bgr, close_border=False)
    rows = []
    for g, reasons in _glyph_components(bgr):
        x, y, w, h = g.bbox
        pixels = old_body[y:y+h, x:x+w][g.mask > 0]
        coverage = float((pixels > 0).mean()) if pixels.size else 0.0
        rows.append({"bbox": list(g.bbox), "area": g.area, "ink": g.ink,
                     "aspect": g.width / g.height, "accepted": not reasons,
                     "reasons": reasons, "legacy_aspect_rejected": g.width / g.height > 1.5,
                     "legacy_width_rejected": g.width > LEGACY_MAX_W,
                     "legacy_body_coverage": coverage,
                     "extraction_version": EXTRACTION_VERSION})
    return rows


def manual_glyph(bgr, bbox: Sequence[int]) -> Glyph:
    """从原帧人工框中提取墨迹，绕过连通块尺寸/牌体门槛，不推断标签或方向。"""
    cv2, np = load_cv2(), load_numpy()
    if len(bbox) != 4 or any(isinstance(v, bool) or int(v) != v for v in bbox):
        raise ImageRejected("人工框必须为四个整数 x,y,w,h")
    x, y, w, h = map(int, bbox)
    if min(x, y) < 0 or min(w, h) <= 0 or x + w > bgr.shape[1] or y + h > bgr.shape[0]:
        raise ImageRejected("人工框超出原帧")
    crop = bgr[y:y+h, x:x+w]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    mask = ((hsv[:, :, 2] < 150) | (hsv[:, :, 1] > 80)).astype(np.uint8) * 255
    return Glyph((x, y, w, h), mask, _ink_kind(np, crop, mask), int((mask > 0).sum()))


# ---------------- 模板 ----------------

def _normalise_mask(mask, target_h: int):
    """紧裁后按高度归一，保留长宽比。"""
    cv2, np = load_cv2(), load_numpy()
    points = cv2.findNonZero(mask)
    if points is None:
        return None
    x, y, w, h = cv2.boundingRect(points)
    tight = mask[y:y + h, x:x + w]
    if h < 1 or w < 1:
        return None
    scale = target_h / float(h)
    new_w = max(1, int(round(w * scale)))
    return cv2.resize(tight, (new_w, target_h), interpolation=cv2.INTER_AREA)


def render_font_templates(target_h: int = TEMPLATE_H) -> Dict[str, Any]:
    """用系统真实字体渲染 13 个点数作为起步模板。

    这只是引导用的近似字形，不是这张桌子的真实牌面；
    真实模板由 bootstrap_templates() 从实际画面回炉。
    """
    np = load_numpy()
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise ImageRejected("渲染起步模板需要 Pillow") from exc

    font_path = next((p for p in FONT_CANDIDATES if Path(p).is_file()), None)
    if font_path is None:
        raise ImageRejected("没有找到可用的系统字体")

    font = ImageFont.truetype(font_path, target_h * 3)
    templates: Dict[str, Any] = {}
    for rank in RANKS_13:
        image = Image.new("L", (target_h * 8, target_h * 6), 0)
        ImageDraw.Draw(image).text((target_h, target_h), rank, fill=255, font=font)
        mask = np.array(image, dtype=np.uint8)
        mask = (mask > 110).astype(np.uint8) * 255
        normalised = _normalise_mask(mask, target_h)
        if normalised is None:
            raise ImageRejected(f"渲染 {rank} 失败")
        templates[rank] = normalised
    return templates


# ---------------- 旋转穷举匹配 ----------------

def _rotate(mask, angle: float):
    cv2, np = load_cv2(), load_numpy()
    h, w = mask.shape[:2]
    diagonal = int((h * h + w * w) ** 0.5) + 4
    canvas = np.zeros((diagonal, diagonal), dtype=np.uint8)
    oy, ox = (diagonal - h) // 2, (diagonal - w) // 2
    canvas[oy:oy + h, ox:ox + w] = mask
    matrix = cv2.getRotationMatrix2D((diagonal / 2.0, diagonal / 2.0), angle, 1.0)
    return cv2.warpAffine(canvas, matrix, (diagonal, diagonal), flags=cv2.INTER_NEAREST)


def _blur(mask):
    cv2, np = load_cv2(), load_numpy()
    return cv2.GaussianBlur(mask.astype(np.float32) / 255.0, (3, 3), 0)


def _best_over_templates(patch, templates_blurred,
                         merged: bool = False) -> List[Tuple[str, float]]:
    """滑动匹配，打分用交并比。

    不用归一化相关：它在几乎无墨的窗口上会退化成满分，而且不同尺寸的模板
    之间不可比——窄模板（J、7）总能在大图里找到一个角落蹭高分。
    交并比对「少墨」和「多墨」同时惩罚，且天然可比。

    merged=True 表示这是点数与花色黏连的整块，此时点数必定在整块的一端，
    不会在正中间，所以只接受贴近上下两端的匹配位置。
    """
    cv2, np = load_cv2(), load_numpy()
    scores: List[Tuple[str, float]] = []
    ph, pw = patch.shape[:2]
    for rank, tmpl in templates_blurred.items():
        th, tw = tmpl.shape[:2]
        if th > ph or tw > pw:
            continue
        # 二值化后用 TM_CCORR 求交集像素数
        intersection = cv2.matchTemplate(patch, tmpl, cv2.TM_CCORR)
        window_ink = cv2.matchTemplate(patch, np.ones_like(tmpl), cv2.TM_CCORR)
        tmpl_ink = float(tmpl.sum())
        if tmpl_ink <= 0:
            continue
        union = window_ink + tmpl_ink - intersection
        with np.errstate(divide="ignore", invalid="ignore"):
            iou = np.where(union > 0, intersection / union, 0.0)
        iou = np.nan_to_num(iou, nan=0.0, posinf=0.0, neginf=0.0)
        if merged and iou.shape[0] >= 4:
            end = max(1, iou.shape[0] // 4)
            band = np.zeros(iou.shape[0], dtype=bool)
            band[:end] = True
            band[-end:] = True
            iou = iou[band]
        if iou.size == 0:
            continue
        scores.append((rank, float(iou.max())))
    return scores


# 滑动所需的少量余量。不要补到最宽模板的宽度：
# 那会抹掉长宽比这个判据，让窄字也能匹配上 "10"。
SLIDE_PAD = 3


def _patch_for(mask, angle: float, scale: float, _unused: int = 0):
    return _blur(_patch_raw(mask, angle, scale))


def match_glyph(glyph: Glyph, templates: Dict[str, Any], *,
                scales: Sequence[float] = SCALES,
                top: int = 3) -> List[RankMatch]:
    """粗扫 360° 再细化。返回按分数排序的候选。"""
    blurred = {rank: _blur(tmpl) for rank, tmpl in templates.items()}

    best: Dict[Tuple[str, float, float], float] = {}

    def evaluate(angle: float) -> None:
        for scale in scales:
            patch = _patch_for(glyph.mask, angle, scale)
            if patch is None:
                continue
            for rank, score in _best_over_templates(patch, blurred, merged=scale > 1.3):
                key = (rank, angle, scale)
                if score > best.get(key, -2.0):
                    best[key] = score

    for angle in range(0, 360, COARSE_STEP):
        evaluate(float(angle))
    if not best:
        return []

    coarse_best = max(best.items(), key=lambda kv: kv[1])
    centre = coarse_best[0][1]
    for offset in range(-REFINE_SPAN, REFINE_SPAN + 1, REFINE_STEP):
        if offset:
            evaluate((centre + offset) % 360)

    ordered = sorted(best.items(), key=lambda kv: kv[1], reverse=True)
    # 同一点数只保留最好的那次
    seen: Dict[str, RankMatch] = {}
    for (rank, angle, scale), score in ordered:
        if rank not in seen:
            seen[rank] = RankMatch(rank=rank, score=score, angle=angle, scale=scale)
    return sorted(seen.values(), key=lambda m: m.score, reverse=True)[:top]


def _patch_raw(mask, angle: float, scale: float):
    cv2 = load_cv2()
    rotated = _rotate(mask, angle)
    normalised = _normalise_mask(rotated, max(2, int(round(TEMPLATE_H * scale))))
    if normalised is None:
        return None
    return cv2.copyMakeBorder(
        normalised, SLIDE_PAD, SLIDE_PAD, SLIDE_PAD, SLIDE_PAD,
        cv2.BORDER_CONSTANT, value=0)


def matched_patch(glyph: Glyph, template, angle: float, scale: float,
                  min_w: int = 0):
    """取出匹配命中的那一小块真实字形，用于回炉成真实模板。

    返回摆正、按模板高度归一后的二值图；连通块里点数与花色黏连时，
    滑动匹配会定位到点数那一段，取出来的就只有点数。
    """
    cv2 = load_cv2()
    raw = _patch_raw(glyph.mask, angle, scale)
    if raw is None:
        return None
    patch = _blur(raw)
    tmpl = _blur(template)
    if tmpl.shape[0] > patch.shape[0] or tmpl.shape[1] > patch.shape[1]:
        return None
    result = cv2.matchTemplate(patch, tmpl, cv2.TM_CCOEFF_NORMED)
    _, _, _, max_loc = cv2.minMaxLoc(result)
    x, y = max_loc
    th, tw = tmpl.shape[:2]
    return raw[y:y + th, x:x + tw]


def template_min_width(templates: Dict[str, Any]) -> int:
    return max(t.shape[1] for t in templates.values())


# ---------------- 模板库 ----------------

class RankBank:
    """真实模板库。模板是二值掩膜，按高度归一。"""

    def __init__(self, templates: Dict[str, Any], *, origin: str,
                 sample_counts: Optional[Dict[str, int]] = None):
        self.templates = templates
        self.origin = origin
        self.sample_counts = sample_counts or {}

    @property
    def digest(self) -> str:
        import hashlib
        digest = hashlib.blake2b(digest_size=8)
        for rank in sorted(self.templates):
            digest.update(rank.encode("ascii"))
            digest.update(self.templates[rank].tobytes())
        return digest.hexdigest()

    def save(self, directory: Path | str) -> Path:
        cv2 = load_cv2()
        out = Path(directory)
        out.mkdir(parents=True, exist_ok=True)
        for rank, mask in self.templates.items():
            name = "10" if rank == "10" else rank
            cv2.imwrite(str(out / f"{name}.png"), mask)
        (out / "manifest.json").write_text(json.dumps({
            "schema": BANK_SCHEMA,
            "origin": self.origin,
            "digest": self.digest,
            "template_h": TEMPLATE_H,
            "ranks": sorted(self.templates),
            "sample_counts": self.sample_counts,
            "note": "匹配度不是校准后的正确概率。",
        }, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        return out

    @classmethod
    def load(cls, directory: Path | str) -> "RankBank":
        cv2 = load_cv2()
        source = Path(directory)
        manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
        templates = {}
        for rank in manifest["ranks"]:
            mask = cv2.imread(str(source / f"{rank}.png"), cv2.IMREAD_GRAYSCALE)
            if mask is None:
                raise ImageRejected(f"模板缺失: {rank}")
            templates[rank] = mask
        return cls(templates, origin=manifest.get("origin", "unknown"),
                   sample_counts=manifest.get("sample_counts"))
