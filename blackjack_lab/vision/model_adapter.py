# -*- coding: utf-8 -*-
"""Explicit, local model selection and generation-scoped candidate publication.

This module does not import the ledger or capture backends. Loading a model does
not train it, promote it, or authorize automatic confirmation.
"""
from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from .contracts import (
    FACE_SHOWN, FACE_UNREADABLE, RANKS_13, RECOGNITION_SCHEMA_VERSION,
    SOURCE_OBSERVER_VIDEO, CardObservation, LayoutProfile, RankHypothesis,
    RecognitionResult, validate_result,
)
from .deps import ImageRejected
from .image_io import LoadedImage, crop_rgb, rgb_to_bgr, sha256_bytes


def load_style(path: Path | str):
    """Read an explicit static layout or normalized capture/replay style."""
    from .contracts import layout_from_dict
    from .live_input import LiveStyle
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return LiveStyle.from_dict(raw) if raw.get("normalized") else layout_from_dict(raw)


def prepare_style_image(loaded: LoadedImage, style=None):
    """Apply the same selected capture crop and ROI to local stills and replay."""
    from .live_input import LiveStyle, capture_crop_pixels, layout_for_frame
    from .pipeline import infer_layout
    from .table_crop import apply_layout_crops, _crop_loaded
    if isinstance(style, LiveStyle):
        crop = capture_crop_pixels(style, loaded.width, loaded.height)
        if crop is not None:
            loaded = _crop_loaded(loaded, crop)
        layout = layout_for_frame(style, loaded.width, loaded.height)
    else:
        layout = style or infer_layout(loaded)
    return layout, apply_layout_crops(loaded, layout)


class TrainedModelAdapter:
    """One immutable, verified model artifact, bound to one explicit style."""

    def __init__(self, directory: Path | str, *, style_id: str, corner_policy_version=None):
        from .rank_classifier import RankClassifier
        from .real_cards import EXTRACTION_VERSION

        self.directory = Path(directory).resolve()
        try:
            manifest = (self.directory / "manifest.json").read_bytes()
            blob = (self.directory / "model.npz").read_bytes()
        except OSError as exc:
            raise ImageRejected(f"无法读取指定模型：{self.directory}") from exc
        self.model = RankClassifier.load(self.directory)
        if (manifest != (self.directory / "manifest.json").read_bytes()
                or blob != (self.directory / "model.npz").read_bytes()):
            raise ImageRejected("模型文件在加载过程中变化，请固定模型后重新选择")
        if not style_id or self.model.style_id != style_id:
            raise ImageRejected(
                f"模型样式 {self.model.style_id!r} 与选定样式 {style_id!r} 不一致")
        self.style_id = style_id
        self.model_id = self.model.model_id
        self.feature_version = self.model.feature_version
        self.training_digest = self.model.training_digest
        self.extraction_version = EXTRACTION_VERSION
        self.corner_policy_version = None
        if self.model.orientation_policy == "upright_upper":
            from .corner_policy import UPPER_CORNER_POLICY, CORNER_POLICY_VERSIONS
            self.corner_policy_version = corner_policy_version or UPPER_CORNER_POLICY
            if self.corner_policy_version not in CORNER_POLICY_VERSIONS:
                raise ImageRejected("未知上角几何策略")
            self.extraction_version += "+" + self.corner_policy_version
        elif corner_policy_version is not None:
            raise ImageRejected("旧全方向模型不能声明上角策略")
        # The manifest includes thresholds and style as well as blob identity.
        self.digest = hashlib.sha256(
            manifest + b"\0" + blob + b"\0" + self.extraction_version.encode("ascii")).hexdigest()

    @property
    def identity_text(self) -> str:
        return (f"开发模型 / 仅候选  {self.model_id}  digest={self.digest[:16]}  "
                f"style={self.style_id}  feature={self.feature_version}  "
                f"extractor={self.extraction_version}  "
                f"training={self.training_digest[:16]}；未通过独立人工真值验收")

    def recognize(self, loaded: LoadedImage, layout: LayoutProfile,
                  source_declaration: str) -> RecognitionResult:
        from .real_cards import extract_glyphs

        if layout.style_id != self.style_id:
            raise ImageRejected(
                f"模型要求样式 {self.style_id!r}，当前为 {layout.style_id!r}；请明确切换模型或样式")
        observations = []
        occupied = set()
        bgr = rgb_to_bgr(loaded)
        glyphs = extract_glyphs(bgr)
        excluded_corners = 0
        geometry_review = []
        if self.model.orientation_policy == "upright_upper":
            from .corner_policy import UpperCornerSelector, corner_key
            selector = UpperCornerSelector(bgr, version=self.corner_policy_version)
            retained = []
            for glyph in glyphs:
                decision = selector.assess(list(glyph.bbox))
                if decision["keep"]:
                    retained.append(glyph)
                elif decision.get("state") == "uncertain":
                    geometry_review.append({
                        "candidate_id": corner_key("frame", loaded.sha256, glyph.bbox),
                        "asset_sha256": loaded.sha256,
                        "bbox": dict(zip(("x","y","w","h"), glyph.bbox)),
                        "model_id": self.model_id, "model_digest": self.digest,
                        "corner_policy": self.corner_policy_version,
                        "state": "uncertain", "reason": decision["reason"],
                        "selection": decision, "rank": None, "accepted": False,
                        "classification_performed": False, "writes_ledger": False})
            excluded_corners = len(glyphs)-len(retained)
            glyphs = retained
        # Shared raw extraction plus the model's explicit corner policy, using
        # full image context. Never resize whole-card boxes into glyph inputs.
        for glyph in glyphs:
            x, y, w, h = glyph.bbox
            cx, cy = x + w / 2.0, y + h / 2.0
            regions = [(name, region) for name, region in layout.regions.items()
                       if region.x <= cx < region.x + region.w
                       and region.y <= cy < region.y + region.h]
            if not regions:
                # Retain detector output for review, including missed ROI
                # ownership; dropping it would hide false card outputs.
                region_id, seat_hint = "unassigned", None
            else:
                regions.sort(key=lambda pair: (pair[1].w * pair[1].h, pair[0]))
                region_id = regions[0][0]
                seat_hint = regions[0][1].seat_hint if len(regions) == 1 else None
                occupied.update(name for name, _ in regions)
            rgb = crop_rgb(loaded, x, y, w, h)
            crop_digest = sha256_bytes(rgb)
            bbox = dict(x=x, y=y, w=w, h=h)
            payload = json.dumps([loaded.sha256, region_id, bbox, crop_digest], sort_keys=True)
            # Identity is spatial, never rank-based. Two eights remain separate.
            observation_id = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
            guess = self.model.predict_mask(glyph.mask)
            rank = guess.raw_label if guess.raw_label in RANKS_13 else None
            accepted = bool(guess.accepted and guess.rank in RANKS_13)
            notes = [
                self.identity_text,
                "角标候选不等于独立物理牌；同牌两角、花色和移动归属须人工核对，不能按点数去重。",
                f"ink={glyph.ink}; margin={guess.margin:.4f}; angle={guess.angle}",
            ]
            if len(regions) != 1:
                notes.append("座位归属不确定，请人工指定；候选仍保留供复核。")
            observations.append(CardObservation(
                observation_id=observation_id, asset_sha256=loaded.sha256,
                crop_sha256=crop_digest, bbox=bbox, region_id=region_id,
                layout_profile_id=layout.layout_profile_id,
                model_id=self.model_id, model_digest=self.digest,
                recognition_schema_version=RECOGNITION_SCHEMA_VERSION,
                rank_candidates=[RankHypothesis(rank, float(guess.score), rank)] if rank else [],
                reject_reason=None if accepted else "分类器拒识／负例，等待人工核对",
                face_state_candidate=FACE_SHOWN if accepted else FACE_UNREADABLE,
                source_declaration=source_declaration, seat_hint=seat_hint, notes=notes,
            ))
        result = RecognitionResult(
            asset_sha256=loaded.sha256, image_path=str(loaded.path),
            layout_profile_id=layout.layout_profile_id, model_id=self.model_id,
            model_digest=self.digest, source_declaration=source_declaration,
            platform_claim=layout.platform_claim, observations=observations,
            geometry_review=geometry_review,
            empty_regions=[name for name in layout.regions if name not in occupied],
            warnings=[self.identity_text, "匹配度不是正确概率；全部候选等待人工确认，自动入账关闭。",
                      "当前输出单位是角标，不保证物理牌完整检出、身份或归属正确。"],
        )
        if self.model.orientation_policy == "upright_upper":
            result.warnings.append(f"正向上角策略：{excluded_corners-len(geometry_review)} 个排除，"
                                   f"{len(geometry_review)} 个几何待核；待核项未分类，不翻转下角补识别。")
        return validate_result(result)


class RecognitionRuntime:
    """Publish only the latest request in the current model/source/ROI generation.

    Call ``invalidate`` immediately at a UI source/ROI change, before another
    frame arrives. In-flight work keeps its old snapshot and cannot republish.
    Results and model objects are never automatically written to a ledger.
    """

    def __init__(self, adapter: Optional[TrainedModelAdapter] = None):
        self._lock = threading.RLock()
        self._adapter = adapter
        self._context = None
        self._generation = 0
        self._request = 0
        self._result = None

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    @property
    def adapter(self):
        with self._lock:
            return self._adapter

    @property
    def current_result(self):
        with self._lock:
            return self._result

    def _invalidate_locked(self):
        self._generation += 1
        self._result = None

    def invalidate(self, reason: str = "来源或区域已改变") -> int:
        with self._lock:
            self._invalidate_locked()
            self._context = None
            return self._generation

    def select_model(self, adapter: Optional[TrainedModelAdapter]) -> int:
        with self._lock:
            # Even selecting the same artifact is an explicit review reset.
            self._adapter = adapter
            self._invalidate_locked()
            return self._generation

    def recognize_loaded(self, loaded: LoadedImage, *, layout: LayoutProfile,
                         source_key: str,
                         source_declaration: str = SOURCE_OBSERVER_VIDEO,
                         templates_dir=None) -> Optional[RecognitionResult]:
        from .pipeline import recognize_loaded

        context = json.dumps([source_key, asdict(layout), loaded.width, loaded.height],
                             ensure_ascii=False, sort_keys=True)
        with self._lock:
            if context != self._context:
                self._invalidate_locked()
                self._context = context
            generation = self._generation
            self._request += 1
            request = self._request
            adapter = self._adapter
            self._result = None
        result = recognize_loaded(loaded, layout=layout, adapter=adapter,
                                  templates_dir=templates_dir,
                                  source_declaration=source_declaration)
        with self._lock:
            if generation != self._generation or request != self._request:
                return None
            result.warnings.append(f"recognition_generation={generation}；来源、区域或模型改变后撤回。")
            self._result = result
            return result
