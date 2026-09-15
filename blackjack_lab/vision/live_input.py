# -*- coding: utf-8 -*-
"""实时帧接入识别管线。

vision 不导入 capture，也不碰 Win32：帧按结构约定传入（见 FrameLike），
因此识别器仍可在没有桌面、没有采集后端的机器上导入与测试。

坐标一律以归一化比例保存。navy-live-felt-v1 把裁区写成 2560×1440 下的绝对像素，
窗口一挪一缩放就全错；实时样式改为按当前帧宽高换算，并用 layout_version
让旧推理结果作废，不把旧框投到新画面上。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Protocol, Tuple

from .contracts import (
    LayoutProfile, RecognitionResult, layout_from_dict,
)
from .deps import ImageRejected, load_numpy
from .image_io import LoadedImage


class FrameLike(Protocol):
    """capture.FramePacket 的结构约定。只读取，不反向依赖捕获层。"""

    source_id: str
    stream_epoch: int
    frame_id: int
    observed_at: float
    layout_version: int
    frame_content_signature: str
    is_repeat: bool
    is_black: bool
    pixels: Any

    @property
    def width(self) -> int: ...

    @property
    def height(self) -> int: ...

LIVE_SCHEMA_VERSION = "0.3c-live-1"
SOURCE_LIVE_CAPTURE = "授权窗口实时捕获"
LIVE_STYLE_DIR = Path(__file__).resolve().parent / "styles"


def frame_to_loaded(packet: FrameLike) -> LoadedImage:
    """实时帧 → LoadedImage，供现有 recognize_loaded 使用。

    不落盘。资产哈希绑定来源、代号、帧号与内容签名，
    使同一帧重复识别得到同一身份，不同帧不会撞车。
    """
    pixels = packet.pixels
    if pixels is None:
        raise ImageRejected("帧不含像素，无法识别")
    np = load_numpy()
    if pixels.ndim != 3 or pixels.shape[2] != 3:
        raise ImageRejected(f"实时帧应为三通道 BGR，实际 {getattr(pixels, 'shape', None)}")
    rgb = np.ascontiguousarray(pixels[:, :, ::-1]).tobytes()
    digest = hashlib.sha256(
        f"{packet.source_id}:{packet.stream_epoch}:{packet.frame_id}:"
        f"{packet.frame_content_signature}".encode("utf-8")
    ).hexdigest()
    pseudo = Path("live") / packet.source_id.replace(":", "-") / f"frame-{packet.frame_id:09d}"
    return LoadedImage(
        path=pseudo,
        width=packet.width,
        height=packet.height,
        sha256=digest,
        rgb=rgb,
        byte_size=len(rgb),
        format="live-frame",
    )


@dataclass(frozen=True)
class NormalizedBox:
    """比例坐标，取值 0–1，相对当前帧宽高。"""

    x: float
    y: float
    w: float
    h: float
    seat_hint: Optional[str] = None

    def __post_init__(self) -> None:
        for name, value in (("x", self.x), ("y", self.y), ("w", self.w), ("h", self.h)):
            if not 0.0 <= float(value) <= 1.0:
                raise ImageRejected(f"归一化坐标 {name}={value} 越界")
        if self.w <= 0 or self.h <= 0:
            raise ImageRejected("归一化区域尺寸必须为正")
        if self.x + self.w > 1.0001 or self.y + self.h > 1.0001:
            raise ImageRejected("归一化区域超出画面")

    def to_pixels(self, width: int, height: int) -> Dict[str, Any]:
        px = int(round(self.x * width))
        py = int(round(self.y * height))
        pw = max(1, int(round(self.w * width)))
        ph = max(1, int(round(self.h * height)))
        pw = min(pw, width - px)
        ph = min(ph, height - py)
        box: Dict[str, Any] = {"x": px, "y": py, "w": pw, "h": ph}
        if self.seat_hint:
            box["seat_hint"] = self.seat_hint
        return box

    def as_dict(self) -> Dict[str, Any]:
        data = {"x": self.x, "y": self.y, "w": self.w, "h": self.h}
        if self.seat_hint:
            data["seat_hint"] = self.seat_hint
        return data


@dataclass
class LiveStyle:
    """一种牌桌样式的实时标定结果。首版只支持一种样式、一个区域。"""

    style_id: str
    regions: Dict[str, NormalizedBox]
    felt_kind: str = "navy"
    # 捕获区域（相对整个窗口/显示器）。None 表示整幅。
    capture_crop: Optional[NormalizedBox] = None
    detect: Dict[str, Any] = field(default_factory=dict)
    source_kind: str = "window"
    title_fragment: str = ""
    platform_claim: str = "none"
    note: str = "本地人工标定，仅覆盖标定时的这一种样式，不是通用平台适配。"
    schema_version: str = LIVE_SCHEMA_VERSION

    def fingerprint(self) -> str:
        payload = json.dumps(self.as_dict(), sort_keys=True, ensure_ascii=False)
        return hashlib.blake2b(payload.encode("utf-8"), digest_size=8).hexdigest()

    def layout_version(self, width: int, height: int) -> int:
        """标定内容或画面尺寸一变，版本就变，旧结果随之作废。"""
        seed = f"{self.fingerprint()}:{width}x{height}"
        return int(hashlib.blake2b(seed.encode("ascii"), digest_size=4).hexdigest(), 16)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "style_id": self.style_id,
            "felt_kind": self.felt_kind,
            "normalized": True,
            "source_kind": self.source_kind,
            "title_fragment": self.title_fragment,
            "platform_claim": self.platform_claim,
            "note": self.note,
            "capture_crop": self.capture_crop.as_dict() if self.capture_crop else None,
            "detect": dict(self.detect),
            "regions": {name: box.as_dict() for name, box in self.regions.items()},
        }

    def save(self, path: Path | str) -> Path:
        """原子写入。不覆盖别的样式文件内容，失败时保留旧文件。"""
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        tmp.write_text(
            json.dumps(self.as_dict(), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8")
        tmp.replace(dest)
        return dest

    @classmethod
    def load(cls, path: Path | str) -> "LiveStyle":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LiveStyle":
        if not data.get("normalized"):
            raise ImageRejected("实时样式必须是归一化坐标；绝对像素样式请重新标定")
        regions = {
            name: NormalizedBox(
                x=float(raw["x"]), y=float(raw["y"]),
                w=float(raw["w"]), h=float(raw["h"]),
                seat_hint=raw.get("seat_hint"))
            for name, raw in data["regions"].items()
        }
        if not regions:
            raise ImageRejected("样式至少要标定一个牌区")
        crop_raw = data.get("capture_crop")
        crop = None
        if crop_raw:
            crop = NormalizedBox(
                x=float(crop_raw["x"]), y=float(crop_raw["y"]),
                w=float(crop_raw["w"]), h=float(crop_raw["h"]))
        return cls(
            style_id=str(data["style_id"]),
            regions=regions,
            felt_kind=str(data.get("felt_kind", "navy")),
            capture_crop=crop,
            detect=dict(data.get("detect") or {}),
            source_kind=str(data.get("source_kind", "window")),
            title_fragment=str(data.get("title_fragment", "")),
            platform_claim=str(data.get("platform_claim", "none")),
            note=str(data.get("note", "")),
            schema_version=str(data.get("schema_version", LIVE_SCHEMA_VERSION)),
        )


def layout_for_frame(style: LiveStyle, width: int, height: int) -> LayoutProfile:
    """按当前帧尺寸把归一化样式换算成像素 LayoutProfile。"""
    if width <= 0 or height <= 0:
        raise ImageRejected("画面尺寸非法")
    data: Dict[str, Any] = {
        "layout_profile_id": style.style_id,
        "style_id": style.style_id,
        "style_claim": "local-live-capture",
        "platform_claim": style.platform_claim,
        "canvas": {"width": int(width), "height": int(height)},
        "felt_kind": style.felt_kind,
        "detect": dict(style.detect),
        "regions": {name: box.to_pixels(width, height)
                    for name, box in style.regions.items()},
    }
    return layout_from_dict(data)


def capture_crop_pixels(style: LiveStyle, source_width: int,
                        source_height: int) -> Optional[Tuple[int, int, int, int]]:
    """把归一化捕获区域换算成来源像素坐标，交给捕获层裁剪。"""
    if style.capture_crop is None:
        return None
    box = style.capture_crop.to_pixels(source_width, source_height)
    return (box["x"], box["y"], box["w"], box["h"])


def recognize_frame(packet: FrameLike, style: LiveStyle, *,
                    templates_dir: Optional[Path] = None, adapter=None,
                    runtime=None) -> Optional[RecognitionResult]:
    """识别一帧实时画面。仍然只产生候选，不写账本。

    冻结帧照样返回结果供预览，但会标出来，由上层决定不重复计为新证据。
    """
    from .pipeline import recognize_loaded  # 局部导入：避免与 pipeline 形成环

    loaded = frame_to_loaded(packet)
    layout = layout_for_frame(style, packet.width, packet.height)
    if runtime is not None:
        if adapter is not None:
            raise ImageRejected("runtime 已负责模型选择，不得另传 adapter")
        result = runtime.recognize_loaded(
            loaded, layout=layout, templates_dir=templates_dir,
            source_key=f"{packet.source_id}:{packet.stream_epoch}:{packet.layout_version}",
            source_declaration=SOURCE_LIVE_CAPTURE)
        if result is None:
            return None  # Source/ROI/model changed while this frame ran.
    else:
        result = recognize_loaded(
            loaded, layout=layout, templates_dir=templates_dir, adapter=adapter,
            source_declaration=SOURCE_LIVE_CAPTURE,
        )
    # 实时来源有真实采集时钟，不必用模型运行时间冒充。
    result.captured_at = packet.observed_at
    for obs in result.observations:
        obs.captured_at = packet.observed_at
    result.clock_note = "captured_at 来自捕获时刻；media_time_ns 由采集后端给出。"
    warnings = list(result.warnings)
    warnings.append(
        f"实时候选：stream_epoch={packet.stream_epoch} layout_version={packet.layout_version}；"
        "切源或重新标定后本结果作废。")
    if packet.is_repeat:
        warnings.append("本帧与上一帧内容相同，不得计为新的独立支持帧。")
    if packet.is_black:
        warnings.append("画面全黑：可能是受保护内容或尚未渲染，不作为识别依据。")
    result.warnings = warnings
    return result
