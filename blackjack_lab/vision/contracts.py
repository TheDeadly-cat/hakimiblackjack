# -*- coding: utf-8 -*-
"""V0.3a 识牌候选契约。识别器只产生观察，不产生账本事实。"""
from __future__ import annotations
from copy import deepcopy

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

RANKS_13: Tuple[str, ...] = (
    "A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K",
)
# 视觉层禁止输出 T。十点未细分只能在人工确认后由账本记录。
FORBIDDEN_VISION_RANKS = frozenset({"T", "t", "10点", "?"})

RECOGNITION_SCHEMA_VERSION = "0.3a-r1"
MODEL_ID = "template-ncc-synthetic-felt-v1"
LAYOUT_PROFILE_ID = "synthetic-felt-v1"
STYLE_ID = "synthetic-felt-v1"
SOURCE_SYNTHETIC = "自建合成样式"
REVIEW_PENDING = "图像待核对"

FACE_SHOWN = "shown"
FACE_BACK = "back"
FACE_UNREADABLE = "unreadable"
FACE_STATES = (FACE_SHOWN, FACE_BACK, FACE_UNREADABLE)

MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 16_000_000
MAX_SIDE = 4096
QUEUE_LENGTH = 1

PACKAGE_DIR = Path(__file__).resolve().parent
STYLE_PATH = PACKAGE_DIR / "styles" / "synthetic_felt_v1.json"
NAVY_STYLE_PATH = PACKAGE_DIR / "styles" / "navy_live_felt_v1.json"
SOURCE_OBSERVER_VIDEO = "旁观录像人工确认"


class ContractError(ValueError):
    pass


@dataclass(frozen=True)
class RegionBox:
    x: int
    y: int
    w: int
    h: int
    seat_hint: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        d = {"x": self.x, "y": self.y, "w": self.w, "h": self.h}
        if self.seat_hint:
            d["seat_hint"] = self.seat_hint
        return d


@dataclass(frozen=True)
class LayoutProfile:
    layout_profile_id: str
    style_id: str
    canvas_width: int
    canvas_height: int
    regions: Dict[str, RegionBox]
    style_claim: str = "self-built-synthetic"
    platform_claim: str = "none"
    max_image_bytes: int = MAX_IMAGE_BYTES
    max_pixels: int = MAX_PIXELS
    max_side: int = MAX_SIDE
    queue_length: int = QUEUE_LENGTH
    felt_kind: str = "green"
    detect_min_w: int = 48
    detect_min_h: int = 70
    detect_max_w: int = 160
    detect_max_h: int = 220
    detect_min_aspect: float = 0.52
    detect_max_aspect: float = 0.92
    detect_threshold: int = 200
    split_wide_clusters: bool = False
    expected_card_w: int = 90
    source_frame_width: Optional[int] = None
    source_frame_height: Optional[int] = None
    source_crop: Optional[Tuple[int, int, int, int]] = None
    felt_crop: Optional[Tuple[int, int, int, int]] = None

    def region(self, name: str) -> RegionBox:
        try:
            return self.regions[name]
        except KeyError as exc:
            raise ContractError(f"未知牌区: {name}") from exc


@dataclass
class RankHypothesis:
    rank: str
    match_score: float
    template_id: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "rank": self.rank,
            "match_score": self.match_score,
            "template_id": self.template_id,
            "score_is_calibrated_probability": False,
        }


@dataclass
class CardObservation:
    observation_id: str
    asset_sha256: str
    crop_sha256: str
    bbox: Dict[str, int]
    region_id: str
    layout_profile_id: str
    model_id: str
    model_digest: str
    recognition_schema_version: str
    rank_candidates: List[RankHypothesis]
    reject_reason: Optional[str]
    face_state_candidate: str
    source_declaration: str
    seat_hint: Optional[str] = None
    hand_hint: Optional[str] = None
    captured_at: Optional[float] = None
    relative_time_ms: Optional[int] = None
    frame_index: Optional[int] = None
    crop_relpath: Optional[str] = None
    notes: List[str] = field(default_factory=list)

    def accepted_rank(self) -> Optional[str]:
        if self.reject_reason or self.face_state_candidate != FACE_SHOWN:
            return None
        if not self.rank_candidates:
            return None
        return self.rank_candidates[0].rank

    def as_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["rank_candidates"] = [c.as_dict() for c in self.rank_candidates]
        d["accepted_rank"] = self.accepted_rank()
        d["track_id"] = None  # 观察身份 ≠ 物理牌身份；首版不自动跟踪
        d["score_is_calibrated_probability"] = False
        return d


@dataclass
class RecognitionResult:
    asset_sha256: str
    image_path: str
    layout_profile_id: str
    model_id: str
    model_digest: str
    recognition_schema_version: str = RECOGNITION_SCHEMA_VERSION
    review_status: str = REVIEW_PENDING
    source_declaration: str = SOURCE_SYNTHETIC
    platform_claim: str = "none"
    captured_at: Optional[float] = None
    recognized_at: Optional[float] = None
    clock_note: str = "未知拍摄时钟时不得用模型运行时间冒充采集时间"
    observations: List[CardObservation] = field(default_factory=list)
    # Evidence only: never fed to tracking, rank metrics, or confirmation IDs.
    geometry_review: List[Dict[str, Any]] = field(default_factory=list)
    empty_regions: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    reject_reason: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "asset_sha256": self.asset_sha256,
            "image_path": self.image_path,
            "layout_profile_id": self.layout_profile_id,
            "model_id": self.model_id,
            "model_digest": self.model_digest,
            "recognition_schema_version": self.recognition_schema_version,
            "review_status": self.review_status,
            "source_declaration": self.source_declaration,
            "platform_claim": self.platform_claim,
            "captured_at": self.captured_at,
            "recognized_at": self.recognized_at,
            "clock_note": self.clock_note,
            "observations": [o.as_dict() for o in self.observations],
            **({"geometry_review": deepcopy(self.geometry_review)} if self.geometry_review else {}),
            "empty_regions": list(self.empty_regions),
            "warnings": list(self.warnings),
            "reject_reason": self.reject_reason,
            "writes_ledger": False,
            "score_is_calibrated_probability": False,
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False, indent=2, sort_keys=True)


def default_layout() -> LayoutProfile:
    return load_layout_profile(STYLE_PATH)


def load_layout_profile(path: Path | str) -> LayoutProfile:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return layout_from_dict(data)


def layout_from_dict(data: Mapping[str, Any]) -> LayoutProfile:
    canvas = data["canvas"]
    regions = {}
    for name, raw in data["regions"].items():
        regions[name] = RegionBox(
            x=int(raw["x"]), y=int(raw["y"]), w=int(raw["w"]), h=int(raw["h"]),
            seat_hint=raw.get("seat_hint"),
        )
    limits = data.get("limits", {})
    detect = data.get("detect") or {}
    source_frame = data.get("source_frame") or {}
    source_crop = data.get("source_crop")
    felt_crop = data.get("felt_crop")

    def _box4(raw) -> Optional[Tuple[int, int, int, int]]:
        if not raw:
            return None
        return (int(raw["x"]), int(raw["y"]), int(raw["w"]), int(raw["h"]))

    return LayoutProfile(
        layout_profile_id=str(data["layout_profile_id"]),
        style_id=str(data.get("style_id", data["layout_profile_id"])),
        canvas_width=int(canvas["width"]),
        canvas_height=int(canvas["height"]),
        regions=regions,
        style_claim=str(data.get("style_claim", "self-built-synthetic")),
        platform_claim=str(data.get("platform_claim", "none")),
        max_image_bytes=int(limits.get("max_image_bytes", MAX_IMAGE_BYTES)),
        max_pixels=int(limits.get("max_pixels", MAX_PIXELS)),
        max_side=int(limits.get("max_side", MAX_SIDE)),
        queue_length=int(limits.get("queue_length", QUEUE_LENGTH)),
        felt_kind=str(data.get("felt_kind", "green")),
        detect_min_w=int(detect.get("min_w", 48)),
        detect_min_h=int(detect.get("min_h", 70)),
        detect_max_w=int(detect.get("max_w", 160)),
        detect_max_h=int(detect.get("max_h", 220)),
        detect_min_aspect=float(detect.get("min_aspect", 0.52)),
        detect_max_aspect=float(detect.get("max_aspect", 0.92)),
        detect_threshold=int(detect.get("threshold", 200)),
        split_wide_clusters=bool(detect.get("split_wide_clusters", False)),
        expected_card_w=int(detect.get("expected_card_w", 90)),
        source_frame_width=int(source_frame["width"]) if source_frame.get("width") else None,
        source_frame_height=int(source_frame["height"]) if source_frame.get("height") else None,
        source_crop=_box4(source_crop),
        felt_crop=_box4(felt_crop),
    )


def navy_layout() -> LayoutProfile:
    return load_layout_profile(NAVY_STYLE_PATH)


def validate_match_score(score: float) -> float:
    if type(score) is bool or not isinstance(score, (int, float)):
        raise ContractError("match_score 必须是数值")
    value = float(score)
    if not (-1.0 <= value <= 1.0):
        raise ContractError(f"非法 match_score: {value}")
    if value != value:  # NaN
        raise ContractError("match_score 不能是 NaN")
    return value


def validate_rank(rank: str) -> str:
    if rank in FORBIDDEN_VISION_RANKS:
        raise ContractError(f"视觉层不得输出账本派生值: {rank}")
    if rank not in RANKS_13:
        raise ContractError(f"非法原始牌面: {rank}")
    return rank


def validate_observation(obs: CardObservation) -> CardObservation:
    if not obs.observation_id or not obs.asset_sha256 or not obs.crop_sha256:
        raise ContractError("观察缺少身份哈希")
    if obs.face_state_candidate not in FACE_STATES:
        raise ContractError(f"非法 face_state_candidate: {obs.face_state_candidate}")
    if obs.recognition_schema_version != RECOGNITION_SCHEMA_VERSION:
        raise ContractError("recognition_schema_version 不匹配")
    bbox = obs.bbox
    for key in ("x", "y", "w", "h"):
        if int(bbox[key]) < 0 or (key in ("w", "h") and int(bbox[key]) == 0):
            raise ContractError(f"非法 bbox.{key}")
    for hyp in obs.rank_candidates:
        validate_rank(hyp.rank)
        validate_match_score(hyp.match_score)
        if hyp.template_id != hyp.rank:
            raise ContractError("template_id 必须等于原始牌面")
    if obs.accepted_rank() is not None:
        validate_rank(obs.accepted_rank())
    if obs.face_state_candidate == FACE_SHOWN and not obs.reject_reason:
        if not obs.rank_candidates:
            raise ContractError("已接受的牌面候选不能为空")
    return obs


def validate_result(result: RecognitionResult) -> RecognitionResult:
    if result.review_status != REVIEW_PENDING:
        raise ContractError("R1 结果必须保持“图像待核对”，不得当作已入账")
    seen = set()
    for obs in result.observations:
        validate_observation(obs)
        if obs.observation_id in seen:
            raise ContractError("observation_id 重复")
        seen.add(obs.observation_id)
        if obs.asset_sha256 != result.asset_sha256:
            raise ContractError("观察与任务的图片哈希不一致")
    for candidate in result.geometry_review:
        cid = candidate.get("candidate_id")
        if not isinstance(cid, str) or not cid or cid in seen:
            raise ContractError("几何待核身份缺失或重复")
        seen.add(cid)
        if (candidate.get("state") != "uncertain" or candidate.get("rank") is not None
                or any(candidate.get(k) is not False for k in
                       ("accepted", "classification_performed", "writes_ledger"))):
            raise ContractError("几何待核不能含已识别点数或自动写入声明")
        if (candidate.get("asset_sha256") != result.asset_sha256
                or candidate.get("model_id") != result.model_id
                or candidate.get("model_digest") != result.model_digest):
            raise ContractError("几何待核与当前图片或模型身份不一致")
        bbox = candidate.get("bbox", {})
        if any(type(bbox.get(k)) is not int or bbox[k] < (1 if k in ("w","h") else 0)
               or bbox[k] > MAX_SIDE for k in ("x","y","w","h")):
            raise ContractError("非法几何待核 bbox")
    return result


def result_from_dict(data: Mapping[str, Any]) -> RecognitionResult:
    observations = []
    for raw in data.get("observations", []):
        observations.append(CardObservation(
            observation_id=raw["observation_id"],
            asset_sha256=raw["asset_sha256"],
            crop_sha256=raw["crop_sha256"],
            bbox=dict(raw["bbox"]),
            region_id=raw["region_id"],
            layout_profile_id=raw["layout_profile_id"],
            model_id=raw["model_id"],
            model_digest=raw["model_digest"],
            recognition_schema_version=raw["recognition_schema_version"],
            rank_candidates=[
                RankHypothesis(c["rank"], float(c["match_score"]), c["template_id"])
                for c in raw.get("rank_candidates", [])
            ],
            reject_reason=raw.get("reject_reason"),
            face_state_candidate=raw["face_state_candidate"],
            source_declaration=raw["source_declaration"],
            seat_hint=raw.get("seat_hint"),
            hand_hint=raw.get("hand_hint"),
            captured_at=raw.get("captured_at"),
            relative_time_ms=raw.get("relative_time_ms"),
            frame_index=raw.get("frame_index"),
            crop_relpath=raw.get("crop_relpath"),
            notes=list(raw.get("notes") or []),
        ))
    result = RecognitionResult(
        asset_sha256=data["asset_sha256"],
        image_path=data["image_path"],
        layout_profile_id=data["layout_profile_id"],
        model_id=data["model_id"],
        model_digest=data["model_digest"],
        recognition_schema_version=data.get(
            "recognition_schema_version", RECOGNITION_SCHEMA_VERSION),
        review_status=data.get("review_status", REVIEW_PENDING),
        source_declaration=data.get("source_declaration", SOURCE_SYNTHETIC),
        platform_claim=data.get("platform_claim", "none"),
        captured_at=data.get("captured_at"),
        recognized_at=data.get("recognized_at"),
        clock_note=data.get("clock_note", ""),
        observations=observations,
        geometry_review=deepcopy(list(data.get("geometry_review") or [])),
        empty_regions=list(data.get("empty_regions") or []),
        warnings=list(data.get("warnings") or []),
        reject_reason=data.get("reject_reason"),
    )
    return validate_result(result)


# 确认记录由 ui.vision_bridge 执行；识别器仍不得直接写账本。
CONFIRMATION_FIELDS = (
    "observation_id", "confirmed_rank", "face_state", "seat", "hand_id",
    "operation",  # new / reveal / correction / reject
    "target_event_id", "request_id", "ledger_revision",
    "model_rank_unaltered", "evidence_ref",
)
