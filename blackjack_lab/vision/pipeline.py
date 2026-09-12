# -*- coding: utf-8 -*-
"""独立识牌流程：本地图片 → 牌区 → 候选。不导入求解器、账本或数据库。"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .model_adapter import TrainedModelAdapter

from .contracts import (
    FACE_SHOWN, FACE_UNREADABLE, MODEL_ID, RECOGNITION_SCHEMA_VERSION,
    REVIEW_PENDING, SOURCE_OBSERVER_VIDEO, SOURCE_SYNTHETIC,
    CardObservation, LayoutProfile, RecognitionResult, default_layout,
    navy_layout, validate_result,
)
from .detector import detect_all_regions
from .image_io import LoadedImage, load_image
from .rank_recognizer import (
    TemplateBank, load_template_bank, load_template_bank_optional, recognize_card,
)
from .table_crop import apply_layout_crops

DEFAULT_WARNINGS = [
    "图像待核对：候选尚未写入账本，旧分析不能当成已考虑本图。",
    "match_score 是模板匹配度，不是经过验证的正确概率。",
    "10/J/Q/K 保持原始牌面，视觉层不会合并成 T。",
    "observation_id 不是 track_id；首版不做跨图自动跟踪。",
]


def default_bundle_dir() -> Path:
    repo = Path(__file__).resolve().parents[2]
    return repo / "fixtures" / "vision" / "synthetic-v1"


def default_templates_dir() -> Path:
    return default_bundle_dir() / "templates"


def _observation_id(asset_sha256: str, region_id: str, bbox: dict, crop_sha256: str) -> str:
    payload = json.dumps(
        {"asset": asset_sha256, "region": region_id, "bbox": bbox, "crop": crop_sha256},
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def infer_layout(loaded: LoadedImage) -> LayoutProfile:
    navy = navy_layout()
    if (navy.source_frame_width and navy.source_frame_height
            and loaded.width == navy.source_frame_width
            and loaded.height == navy.source_frame_height):
        return navy
    return default_layout()


def recognize_loaded(loaded: LoadedImage, *,
                     layout: Optional[LayoutProfile] = None,
                     bank: Optional[TemplateBank] = None,
                     adapter: Optional[TrainedModelAdapter] = None,
                     templates_dir: Optional[Path] = None,
                     source_declaration: Optional[str] = None) -> RecognitionResult:
    layout = layout or infer_layout(loaded)
    loaded = apply_layout_crops(loaded, layout)
    if adapter is not None:
        if bank is not None or templates_dir is not None:
            raise ValueError("训练模型与模板只能明确选择一种")
        result = adapter.recognize(
            loaded, layout, source_declaration or SOURCE_OBSERVER_VIDEO)
        result.recognized_at = time.time()
        return result
    if layout.felt_kind == "navy":
        source_declaration = source_declaration or SOURCE_OBSERVER_VIDEO
        bank = bank or load_template_bank_optional(templates_dir)
    else:
        source_declaration = source_declaration or SOURCE_SYNTHETIC
        bank = bank or load_template_bank(templates_dir or default_templates_dir())
    detected = detect_all_regions(loaded, layout)
    recognized_at = time.time()
    warnings = list(DEFAULT_WARNINGS)
    if layout.felt_kind == "navy":
        warnings.append("navy-live-felt-v1：只覆盖本机这段固定窗口的旁观录像，不是通用平台识别。")
        warnings.append("绒面印刷字可能被框到，须人工拒绝。中途开录不能当成完整新靴。")
        warnings.append("深色桌角标用内置点数图做差；匹配不足则待你确认，不自动入账。")
    if not detected["style_ok"]:
        if layout.felt_kind == "navy":
            warnings.append("深色绒面特征不足，不能当作已识别。")
        else:
            warnings.append("画面绿色绒面特征不足；本模型只覆盖 synthetic-felt-v1，不能当作真实平台识别。")
    observations = []
    empty_regions = []
    region_style_ok = detected.get("region_style_ok") or {}
    for region_id, boxes in detected["boxes"].items():
        region = layout.region(region_id)
        if not boxes:
            empty_regions.append(region_id)
            continue
        for box in boxes:
            x, y, w, h = box
            bbox = {"x": x, "y": y, "w": w, "h": h}
            raw = recognize_card(loaded, box, bank)
            if not region_style_ok.get(region_id, detected["style_ok"]):
                raw["reject_reason"] = raw["reject_reason"] or (
                    "该牌区不符合当前冻结绒面样式，不接受点数")
                if raw["face_state_candidate"] == FACE_SHOWN:
                    raw["face_state_candidate"] = FACE_UNREADABLE
            obs = CardObservation(
                observation_id=_observation_id(
                    loaded.sha256, region_id, bbox, raw["crop_sha256"]),
                asset_sha256=loaded.sha256,
                crop_sha256=raw["crop_sha256"],
                bbox=bbox,
                region_id=region_id,
                layout_profile_id=layout.layout_profile_id,
                model_id=bank.model_id,
                model_digest=bank.digest,
                recognition_schema_version=RECOGNITION_SCHEMA_VERSION,
                rank_candidates=raw["rank_candidates"],
                reject_reason=raw["reject_reason"],
                face_state_candidate=raw["face_state_candidate"],
                source_declaration=source_declaration,
                seat_hint=region.seat_hint,
                captured_at=None,
                notes=["检测框左右排序仅便于展示，不是历史发牌顺序"],
            )
            observations.append(obs)
    result = RecognitionResult(
        asset_sha256=loaded.sha256,
        image_path=str(loaded.path),
        layout_profile_id=layout.layout_profile_id,
        model_id=bank.model_id or MODEL_ID,
        model_digest=bank.digest,
        review_status=REVIEW_PENDING,
        source_declaration=source_declaration,
        platform_claim=layout.platform_claim,
        captured_at=None,
        recognized_at=recognized_at,
        observations=observations,
        empty_regions=empty_regions,
        warnings=warnings,
        reject_reason=None if detected["style_ok"] else "unsupported_or_uncertain_style",
    )
    return validate_result(result)


def recognize_path(path, *,
                   layout: Optional[LayoutProfile] = None,
                   adapter: Optional[TrainedModelAdapter] = None,
                   templates_dir: Optional[Path] = None,
                   source_declaration: Optional[str] = None) -> RecognitionResult:
    loaded = load_image(path, layout or default_layout())
    return recognize_loaded(
        loaded, layout=layout, adapter=adapter, templates_dir=templates_dir,
        source_declaration=source_declaration,
    )
