# -*- coding: utf-8 -*-
"""自建 synthetic-felt-v1 画面：绿色绒面 + 块体点数。不模仿真实平台。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .contracts import RANKS_13, STYLE_ID, default_layout
from .image_io import write_png_rgb

FELT = (18, 92, 48)
FELT_DARK = (10, 58, 32)
# 故意不是绿绒面：用于“不支持样式”干扰，不得被当成 synthetic-felt-v1 接受点数。
UNSUPPORTED_FELT = (118, 42, 96)
CARD_FILL = (248, 248, 242)
CARD_BORDER = (22, 22, 22)
INK = (22, 18, 16)
BACK_BLUE = (36, 58, 148)
BACK_STRIPE = (24, 40, 110)
OCCLUSION = (12, 12, 12)
SUIT_RED = (176, 28, 36)
SUIT_BLACK = (18, 18, 18)

CARD_W = 90
CARD_H = 126
INDEX_X, INDEX_Y, INDEX_W, INDEX_H = 6, 6, 48, 36
SCALE = 4

# 5x7 点阵；10 由 1 与 0 拼接。这是自建字形，不是任何赌场字体。
_GLYPHS: Dict[str, Tuple[str, ...]] = {
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "2": ("11110", "00001", "00001", "01110", "10000", "10000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("10010", "10010", "10010", "11111", "00010", "00010", "00010"),
    "5": ("11111", "10000", "11110", "00001", "00001", "00001", "11110"),
    "6": ("01110", "10000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "00100", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "J": ("00111", "00010", "00010", "00010", "00010", "10010", "01100"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
}


class RgbCanvas:
    def __init__(self, width: int, height: int, color: Tuple[int, int, int] = (0, 0, 0)):
        self.width = width
        self.height = height
        r, g, b = color
        self.buf = bytearray((r, g, b) * (width * height))

    def fill_rect(self, x: int, y: int, w: int, h: int, color: Tuple[int, int, int]) -> None:
        x0 = max(0, x)
        y0 = max(0, y)
        x1 = min(self.width, x + w)
        y1 = min(self.height, y + h)
        if x1 <= x0 or y1 <= y0:
            return
        r, g, b = color
        row = bytes((r, g, b)) * (x1 - x0)
        stride = self.width * 3
        for yy in range(y0, y1):
            start = yy * stride + x0 * 3
            self.buf[start:start + len(row)] = row

    def set_pixel(self, x: int, y: int, color: Tuple[int, int, int]) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            i = (y * self.width + x) * 3
            self.buf[i:i + 3] = bytes(color)

    def blit(self, src: "RgbCanvas", x: int, y: int) -> None:
        for yy in range(src.height):
            dy = y + yy
            if dy < 0 or dy >= self.height:
                continue
            for xx in range(src.width):
                dx = x + xx
                if dx < 0 or dx >= self.width:
                    continue
                si = (yy * src.width + xx) * 3
                di = (dy * self.width + dx) * 3
                self.buf[di:di + 3] = src.buf[si:si + 3]

    def crop(self, x: int, y: int, w: int, h: int) -> "RgbCanvas":
        out = RgbCanvas(w, h)
        self_stride = self.width * 3
        out_stride = w * 3
        for yy in range(h):
            src = ((y + yy) * self_stride) + x * 3
            dst = yy * out_stride
            out.buf[dst:dst + out_stride] = self.buf[src:src + out_stride]
        return out

    def to_bytes(self) -> bytes:
        return bytes(self.buf)

    def save_png(self, path: Path | str) -> None:
        write_png_rgb(path, self.width, self.height, self.to_bytes())


def _paint_glyph(canvas: RgbCanvas, rows: Sequence[str], x: int, y: int,
                 color: Tuple[int, int, int] = INK, scale: int = SCALE) -> None:
    for gy, row in enumerate(rows):
        for gx, ch in enumerate(row):
            if ch != "1":
                continue
            canvas.fill_rect(x + gx * scale, y + gy * scale, scale, scale, color)


def paint_rank(canvas: RgbCanvas, rank: str, x: int, y: int,
               color: Tuple[int, int, int] = INK, scale: int = SCALE) -> None:
    if rank == "10":
        _paint_glyph(canvas, _GLYPHS["1"], x, y, color, scale)
        _paint_glyph(canvas, _GLYPHS["0"], x + 5 * scale + 2, y, color, scale)
        return
    _paint_glyph(canvas, _GLYPHS[rank], x, y, color, scale)


def render_index_template(rank: str) -> RgbCanvas:
    canvas = RgbCanvas(INDEX_W, INDEX_H, CARD_FILL)
    paint_rank(canvas, rank, 2, 4)
    return canvas


def render_card(rank: Optional[str], *, face: str = "shown",
                suit: Optional[str] = None, occlude_index: bool = False,
                width: int = CARD_W, height: int = CARD_H) -> RgbCanvas:
    canvas = RgbCanvas(width, height, CARD_FILL)
    canvas.fill_rect(0, 0, width, height, CARD_BORDER)
    canvas.fill_rect(2, 2, width - 4, height - 4, CARD_FILL)
    if face == "back" or rank is None:
        canvas.fill_rect(4, 4, width - 8, height - 8, BACK_BLUE)
        for i in range(-height, width, 10):
            for t in range(4):
                x0 = i + t
                canvas.fill_rect(x0, 6, 2, height - 12, BACK_STRIPE)
        return canvas
    sx = int(round(INDEX_X * width / CARD_W))
    sy = int(round(INDEX_Y * height / CARD_H))
    paint_rank(canvas, rank, sx + 2, sy + 4)
    if suit:
        color = SUIT_RED if suit in {"H", "D"} else SUIT_BLACK
        canvas.fill_rect(width - 18, height - 18, 10, 10, color)
    if occlude_index:
        canvas.fill_rect(sx, sy, INDEX_W, INDEX_H, OCCLUSION)
    return canvas


@dataclass
class PlacedCard:
    rank: Optional[str]
    x: int
    y: int
    region_id: str
    face: str = "shown"
    suit: Optional[str] = None
    occlude_index: bool = False
    scale: float = 1.0

    @property
    def width(self) -> int:
        return max(20, int(round(CARD_W * self.scale)))

    @property
    def height(self) -> int:
        return max(28, int(round(CARD_H * self.scale)))

    def label(self) -> dict:
        return {
            "rank": self.rank,
            "face": "back" if self.face == "back" or self.rank is None else (
                "unreadable" if self.occlude_index else "shown"
            ),
            "bbox": {"x": self.x, "y": self.y, "w": self.width, "h": self.height},
            "region_id": self.region_id,
            "identifiable": bool(self.rank) and self.face == "shown" and not self.occlude_index,
        }


def render_table(cards: Sequence[PlacedCard], *,
                 width: Optional[int] = None, height: Optional[int] = None,
                 draw_guides: bool = True,
                 felt: Optional[Tuple[int, int, int]] = None) -> Tuple[RgbCanvas, List[dict]]:
    layout = default_layout()
    width = width or layout.canvas_width
    height = height or layout.canvas_height
    felt = felt or FELT
    felt_edge = FELT_DARK if felt == FELT else tuple(max(0, c - 24) for c in felt)
    canvas = RgbCanvas(width, height, felt)
    canvas.fill_rect(0, 0, width, 8, felt_edge)
    canvas.fill_rect(0, height - 8, width, 8, felt_edge)
    if draw_guides:
        for region in layout.regions.values():
            canvas.fill_rect(region.x, region.y, region.w, 2, felt_edge)
            canvas.fill_rect(region.x, region.y + region.h - 2, region.w, 2, felt_edge)
            canvas.fill_rect(region.x, region.y, 2, region.h, felt_edge)
            canvas.fill_rect(region.x + region.w - 2, region.y, 2, region.h, felt_edge)
    labels = []
    for card in cards:
        sprite = render_card(
            card.rank, face=card.face, suit=card.suit,
            occlude_index=card.occlude_index,
            width=card.width, height=card.height,
        )
        canvas.blit(sprite, card.x, card.y)
        labels.append(card.label())
    return canvas, labels


def write_templates(directory: Path) -> Dict[str, str]:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    hashes = {}
    from .image_io import sha256_bytes
    for rank in RANKS_13:
        path = directory / f"{rank}.png"
        tmpl = render_index_template(rank)
        tmpl.save_png(path)
        hashes[rank] = sha256_bytes(path.read_bytes())
    return hashes


def smoke_all13_cards() -> List[PlacedCard]:
    dealer = default_layout().region("dealer")
    player = default_layout().region("player_target")
    left = dealer.x + 20
    gap = 100
    cards = []
    top = ("A", "2", "3", "4", "5", "6", "7")
    bottom = ("8", "9", "10", "J", "Q", "K")
    for i, rank in enumerate(top):
        cards.append(PlacedCard(rank, left + i * gap, dealer.y + 40, "dealer"))
    for i, rank in enumerate(bottom):
        cards.append(PlacedCard(rank, left + i * gap, player.y + 50, "player_target"))
    return cards


def smoke_two_eights() -> List[PlacedCard]:
    player = default_layout().region("player_target")
    return [
        PlacedCard("8", player.x + 80, player.y + 50, "player_target"),
        PlacedCard("8", player.x + 200, player.y + 50, "player_target"),
    ]


def smoke_backs() -> List[PlacedCard]:
    dealer = default_layout().region("dealer")
    return [
        PlacedCard(None, dealer.x + 80, dealer.y + 40, "dealer", face="back"),
        PlacedCard("A", dealer.x + 200, dealer.y + 40, "dealer"),
    ]


def smoke_occluded() -> List[PlacedCard]:
    player = default_layout().region("player_target")
    return [PlacedCard("K", player.x + 120, player.y + 40, "player_target", occlude_index=True)]
