# -*- coding: utf-8 -*-
"""真实角标标注集：按局切分，禁止把同一张牌的相邻帧拆进训练与留出。

标签是人写的点数（或 junk），不是模型分数。训练调参不得看留出集。
素材与标签默认放在 .local-evidence/，不入库、不宣称平台授权。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .contracts import ContractError, RANKS_13

LABEL_SCHEMA = "0.3e-glyph-labels-1"
JUNK_LABEL = "junk"
LABEL_RANKS = RANKS_13 + (JUNK_LABEL,)
SPLITS = ("train", "holdout")

# 白像素骤降 ≈ 收牌。连续几帧的同一波收牌合并为一局结束。
DEFAULT_DROP_THRESHOLD = -15000
DEFAULT_MIN_ROUND_S = 8.0
DEFAULT_HOLDOUT_FRAC = 0.30


def crop_id_for(session: str, frame: str, bbox: Sequence[int]) -> str:
    digest = hashlib.blake2b(digest_size=8)
    digest.update(session.encode("utf-8"))
    digest.update(b"\0")
    digest.update(frame.encode("utf-8"))
    digest.update(b"\0")
    digest.update(",".join(str(int(v)) for v in bbox).encode("ascii"))
    return digest.hexdigest()


def validate_label(label: str) -> str:
    if label not in LABEL_RANKS:
        raise ContractError(f"非法标签 {label!r}，只接受 13 点数或 {JUNK_LABEL}")
    return label


@dataclass
class GlyphItem:
    crop_id: str
    session: str
    frame: str
    frame_signature: str
    round_id: int
    split: str
    bbox: Tuple[int, int, int, int]
    ink: str
    crop_file: str
    mask_file: str
    label: Optional[str] = None
    elapsed_s: Optional[float] = None

    def as_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["bbox"] = list(self.bbox)
        data["schema"] = LABEL_SCHEMA
        return data

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "GlyphItem":
        bbox = raw.get("bbox") or [0, 0, 0, 0]
        item = cls(
            crop_id=str(raw["crop_id"]),
            session=str(raw["session"]),
            frame=str(raw["frame"]),
            frame_signature=str(raw.get("frame_signature") or ""),
            round_id=int(raw.get("round_id") or 0),
            split=str(raw.get("split") or "train"),
            bbox=(int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])),
            ink=str(raw.get("ink") or "black"),
            crop_file=str(raw.get("crop_file") or ""),
            mask_file=str(raw.get("mask_file") or ""),
            label=raw.get("label"),
            elapsed_s=raw.get("elapsed_s"),
        )
        if item.split not in SPLITS:
            raise ContractError(f"非法 split {item.split!r}")
        if item.label is not None:
            validate_label(item.label)
        return item


def detect_round_ids(
    frames: Sequence[Mapping[str, Any]],
    *,
    drop_threshold: int = DEFAULT_DROP_THRESHOLD,
    min_round_s: float = DEFAULT_MIN_ROUND_S,
) -> List[int]:
    """给每一帧一个 round_id。收牌那一帧仍属当前局，之后的帧开新局。"""
    ids: List[int] = []
    round_id = 0
    last_boundary = -1e9
    for frame in frames:
        elapsed = float(frame.get("elapsed_s") or 0.0)
        delta = frame.get("white_delta")
        ids.append(round_id)
        if (
            delta is not None
            and int(delta) <= drop_threshold
            and (elapsed - last_boundary) >= min_round_s
        ):
            round_id += 1
            last_boundary = elapsed
    return ids


def assign_splits(
    round_ids: Sequence[int],
    *,
    holdout_frac: float = DEFAULT_HOLDOUT_FRAC,
    min_rounds: int = 3,
) -> Dict[int, str]:
    """整局切分：最后 holdout_frac 的局进留出集。同一局不会出现在两侧。"""
    if not 0.0 < holdout_frac < 1.0:
        raise ContractError("holdout_frac 必须在 (0, 1) 内")
    unique = sorted(set(int(r) for r in round_ids))
    if len(unique) < min_rounds:
        raise ContractError(
            f"只有 {len(unique)} 局，不足以按局切分。"
            "请另采一个会话做留出集，不要把相邻帧随机拆开。"
        )
    n_holdout = max(1, int(round(len(unique) * holdout_frac)))
    if n_holdout >= len(unique):
        n_holdout = len(unique) - 1
    holdout = set(unique[-n_holdout:])
    return {rid: ("holdout" if rid in holdout else "train") for rid in unique}


def assert_no_leakage(items: Sequence[GlyphItem]) -> None:
    """同一帧签名、同一裁片不得同时出现在 train 与 holdout。"""
    train_frames = {i.frame_signature or i.frame for i in items if i.split == "train"}
    holdout_frames = {i.frame_signature or i.frame for i in items if i.split == "holdout"}
    leaked_frames = train_frames & holdout_frames
    if leaked_frames:
        raise ContractError(
            f"同一帧被拆进训练与留出：{sorted(leaked_frames)[:5]}"
        )
    train_ids = {i.crop_id for i in items if i.split == "train"}
    holdout_ids = {i.crop_id for i in items if i.split == "holdout"}
    leaked_ids = train_ids & holdout_ids
    if leaked_ids:
        raise ContractError(f"同一裁片被拆进训练与留出：{sorted(leaked_ids)[:5]}")
    train_rounds = {i.round_id for i in items if i.split == "train"}
    holdout_rounds = {i.round_id for i in items if i.split == "holdout"}
    leaked_rounds = train_rounds & holdout_rounds
    if leaked_rounds:
        raise ContractError(f"同一局被拆进训练与留出：{sorted(leaked_rounds)}")


def load_queue(path: Path | str) -> List[GlyphItem]:
    source = Path(path)
    if source.is_dir():
        source = source / "queue.jsonl"
    items = []
    for line in source.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        items.append(GlyphItem.from_dict(json.loads(line)))
    return items


def save_queue(items: Sequence[GlyphItem], path: Path | str) -> Path:
    dest = Path(path)
    if dest.suffix != ".jsonl":
        dest = dest / "queue.jsonl"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        "".join(json.dumps(item.as_dict(), ensure_ascii=False) + "\n" for item in items),
        encoding="utf-8",
    )
    return dest


def labeled_only(items: Iterable[GlyphItem], *, split: Optional[str] = None) -> List[GlyphItem]:
    out = []
    for item in items:
        if item.label is None:
            continue
        if split is not None and item.split != split:
            continue
        out.append(item)
    return out


def label_counts(items: Iterable[GlyphItem]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in items:
        if item.label is None:
            continue
        counts[item.label] = counts.get(item.label, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: kv[0]))
