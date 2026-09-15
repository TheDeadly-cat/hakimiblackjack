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
SPLITS = ("train", "validation", "holdout")

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
    # Optional evidence is never invented when reading legacy queues. A capture
    # signature is not necessarily the SHA256 of the actual saved frame.
    source_sha256: str = ""
    frame_sha256: str = ""
    crop_sha256: str = ""
    mask_sha256: str = ""
    mask_content_sha256: str = ""
    origin_crop_id: str = ""
    physical_card_id: str = ""
    label_provenance: str = "unspecified"
    extraction_method: str = ""
    rejection_reason: str = ""
    orientation_deg: Optional[float] = None

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
            source_sha256=str(raw.get("source_sha256") or ""),
            frame_sha256=str(raw.get("frame_sha256") or ""),
            crop_sha256=str(raw.get("crop_sha256") or ""),
            mask_sha256=str(raw.get("mask_sha256") or ""),
            mask_content_sha256=str(raw.get("mask_content_sha256") or ""),
            origin_crop_id=str(raw.get("origin_crop_id") or ""),
            physical_card_id=str(raw.get("physical_card_id") or ""),
            label_provenance=str(raw.get("label_provenance") or "unspecified"),
            extraction_method=str(raw.get("extraction_method") or ""),
            rejection_reason=str(raw.get("rejection_reason") or ""),
            orientation_deg=raw.get("orientation_deg"),
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


def round_key(item: GlyphItem) -> Tuple[str, int]:
    """Round numbers restart in each session; bare round_id is not identity."""
    return item.session, item.round_id


def sample_origin_id(item: GlyphItem) -> str:
    """Stable independent-neighbor identity; augmentation never changes this.

    Physical-card IDs are scoped to the recording (or legacy session), not the
    rank: two equal-rank cards remain separate when the annotation says so.
    """
    if item.physical_card_id:
        scope = ("source", item.source_sha256.lower()) if item.source_sha256 else ("session", item.session)
        parts = ("physical", *scope, item.physical_card_id)
    else:
        parts = ("crop", item.origin_crop_id or item.crop_id)
    return json.dumps(parts, ensure_ascii=False, separators=(",", ":"))


def leakage_keys(item: GlyphItem) -> set[tuple]:
    """All available identities, not a fallback that drops stronger evidence.

    Source SHA alone deliberately is not a key: same-source disjoint rounds are
    useful development sets. Independent final-source admission is separate.
    """
    keys = {("round", *round_key(item)), ("crop", item.crop_id)}
    if item.origin_crop_id:
        keys.add(("crop", item.origin_crop_id))
    if item.physical_card_id:
        keys.add(("physical", sample_origin_id(item)))
    if item.frame_sha256:
        keys.add(("frame_sha256", item.frame_sha256.lower()))
    if item.frame_signature:
        keys.add(("frame_signature", item.frame_signature))
    if not item.frame_sha256 and not item.frame_signature:
        keys.add(("frame", item.session, item.frame))
    if item.crop_sha256:
        keys.add(("crop_sha256", item.crop_sha256.lower()))
    if item.mask_sha256:
        keys.add(("mask_sha256", item.mask_sha256.lower()))
    if item.mask_content_sha256:
        keys.add(("mask_content_sha256", item.mask_content_sha256.lower()))
    if item.source_sha256:
        source = item.source_sha256.lower()
        keys.add(("source_round", source, item.round_id))
        keys.add(("source_frame", source, item.frame))
    return keys


def assert_no_leakage(items: Sequence[GlyphItem]) -> None:
    """Reject shared round/frame/crop/card identity across any split pair."""
    seen: Dict[tuple, str] = {}
    for item in items:
        if item.split not in SPLITS:
            raise ContractError(f"非法 split {item.split!r}")
        for key in sorted(leakage_keys(item)):
            previous = seen.setdefault(key, item.split)
            if previous != item.split:
                raise ContractError(f"同一数据身份被拆进 {previous} 与 {item.split}：{key}")


def independent_groups(items: Sequence[GlyphItem]) -> List[List[int]]:
    """Connected components keep transitive card/crop/frame identities together."""
    parents = list(range(len(items)))

    def find(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    seen: Dict[tuple, int] = {}
    for index, item in enumerate(items):
        for key in leakage_keys(item):
            if key in seen:
                parents[find(index)] = find(seen[key])
            else:
                seen[key] = index
    grouped: Dict[int, List[int]] = {}
    for index in range(len(items)):
        grouped.setdefault(find(index), []).append(index)
    return sorted(grouped.values(), key=lambda group: min(
        (items[i].session, items[i].round_id, items[i].crop_id) for i in group))


def group_summary(items: Sequence[GlyphItem]) -> Dict[str, Any]:
    groups = independent_groups(items)
    return {
        "n_items": len(items),
        "n_independent_groups": len(groups),
        "n_origin_ids": len({sample_origin_id(item) for item in items}),
        "n_items_with_physical_card_id": sum(bool(i.physical_card_id) for i in items),
        "n_items_with_source_sha256": sum(bool(i.source_sha256) for i in items),
        "sessions": sorted({i.session for i in items}),
        "source_sha256": sorted({i.source_sha256 for i in items if i.source_sha256}),
        "rounds": [list(key) for key in sorted({round_key(i) for i in items})],
        "label_provenance_counts": {
            value: sum(i.label_provenance == value for i in items)
            for value in sorted({i.label_provenance for i in items})
        },
        "groups": [[items[i].crop_id for i in indices] for indices in groups],
        "note": "Origin IDs fall back to crop IDs when physical-card evidence is absent; counts are not proof of distinct physical cards.",
    }


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
