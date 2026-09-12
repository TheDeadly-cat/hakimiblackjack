# -*- coding: utf-8 -*-
"""实时捕获契约：帧身份、时间与失效代号。

捕获层只产生帧，不识别牌面、不写账本。
「没有新帧」「画面冻结」「全黑」「来源消失」「被拒绝」必须分别报告，
不能笼统说成一个捕获失败，也不能当成识别成功。
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

SOURCE_WINDOW = "window"
SOURCE_MONITOR = "monitor"
SOURCE_KINDS = (SOURCE_WINDOW, SOURCE_MONITOR)

# 捕获状态。Windows Graphics Capture 是变化驱动的：画面不动就没有新帧，
# 这与捕获失败是两回事，必须分开显示。
STATUS_IDLE = "idle"
STATUS_STARTING = "starting"
STATUS_LIVE = "live"
STATUS_NO_NEW_FRAME = "no_new_frame"
STATUS_FROZEN = "frozen"
STATUS_BLACK = "black"
STATUS_SOURCE_LOST = "source_lost"
STATUS_DENIED = "denied"
STATUS_STOPPED = "stopped"

STATUS_LABELS = {
    STATUS_IDLE: "未开始",
    STATUS_STARTING: "正在启动捕获",
    STATUS_LIVE: "实时画面正常",
    STATUS_NO_NEW_FRAME: "暂无新帧（画面未变化或视频已暂停）",
    STATUS_FROZEN: "画面冻结：连续帧内容相同，不计为新证据",
    STATUS_BLACK: "画面全黑：可能是受保护内容或尚未渲染，不绕过",
    STATUS_SOURCE_LOST: "来源消失：窗口已关闭或最小化",
    STATUS_DENIED: "捕获被系统或平台拒绝，已停止，不绕过限制",
    STATUS_STOPPED: "已停止",
}

# 有界最新帧队列。保留 1–2 帧；丢帧要计数，不假装观察完整。
DEFAULT_QUEUE_LENGTH = 2
# 识别用采样率。不是捕获上限，也不是承诺的 FPS。
DEFAULT_TARGET_FPS = 10.0
# 超过该时长没有新帧就报 no_new_frame，而不是继续显示 live。
DEFAULT_STALL_MS = 1200.0
# 逐行采样做内容签名的行间隔。签名用于识别重复帧，不是密码学证明。
SIGNATURE_ROW_STEP = 16
# 采样亮度低于该值视为全黑。
BLACK_MEAN_MAX = 3.0


class CaptureRejected(RuntimeError):
    """捕获被受控拒绝。这不是识别失败，也不得当作识别成功。"""


class CaptureUnavailable(RuntimeError):
    """缺少实时捕获依赖。录像回放与手动录牌必须仍然可用。"""


@dataclass(frozen=True)
class SourceSpec:
    """用户明确选定的捕获来源。只捕获这一个来源，不扫描其他窗口。"""

    kind: str
    window_hwnd: Optional[int] = None
    monitor_index: Optional[int] = None
    title_snapshot: str = ""
    # 只处理选定区域，避免录到余额、聊天和其他窗口。None 表示整个来源。
    crop: Optional[Tuple[int, int, int, int]] = None

    def __post_init__(self) -> None:
        if self.kind not in SOURCE_KINDS:
            raise CaptureRejected(f"未知来源类型: {self.kind}")
        if self.kind == SOURCE_WINDOW and not self.window_hwnd:
            raise CaptureRejected("窗口捕获必须给出窗口句柄")
        if self.kind == SOURCE_MONITOR and self.monitor_index is None:
            raise CaptureRejected("显示器捕获必须给出显示器序号")
        if self.crop is not None:
            x, y, w, h = self.crop
            if w <= 0 or h <= 0:
                raise CaptureRejected("捕获区域尺寸非法")
            if x < 0 or y < 0:
                raise CaptureRejected("捕获区域坐标不能为负")

    @property
    def source_id(self) -> str:
        if self.kind == SOURCE_WINDOW:
            return f"window:{self.window_hwnd}"
        return f"monitor:{self.monitor_index}"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "source_id": self.source_id,
            "window_hwnd": self.window_hwnd,
            "monitor_index": self.monitor_index,
            "title_snapshot": self.title_snapshot,
            "crop": list(self.crop) if self.crop else None,
        }


@dataclass(frozen=True)
class GenerationToken:
    """切源、改区域、改样式后旧结果作废的依据。

    后台结果返回前必须重新核对本代号；UI 是否刷新成功与结果失效无关。
    """

    source_id: str
    stream_epoch: int
    layout_version: int

    def matches(self, other: "GenerationToken") -> bool:
        return (self.source_id == other.source_id
                and self.stream_epoch == other.stream_epoch
                and self.layout_version == other.layout_version)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "source_id": self.source_id,
            "stream_epoch": self.stream_epoch,
            "layout_version": self.layout_version,
        }


@dataclass
class FramePacket:
    """一帧的完整身份与时间。像素由本包独占，不是采集后端的可复用缓冲。"""

    source_id: str
    stream_epoch: int
    frame_id: int
    # 采集后端给出的媒体时间戳；无可信时钟时为 None，不用本地时间冒充。
    media_time_ns: Optional[int]
    # 本进程何时拿到该帧（墙钟）。
    observed_at: float
    # 单调时钟，用于算延迟；不受系统改时间影响。
    observed_monotonic_ns: int
    image_size: Tuple[int, int]
    source_size: Tuple[int, int]
    crop_origin: Tuple[int, int]
    layout_version: int
    frame_content_signature: str
    # 与上一帧内容相同：连续读取同一张冻结画面不得增加独立支持帧数量。
    is_repeat: bool = False
    is_black: bool = False
    # 采集后端已到达但被采样率丢弃的帧数，以及队列挤掉的帧数。
    dropped_before: int = 0
    pixels: Any = field(default=None, repr=False, compare=False)

    @property
    def width(self) -> int:
        return self.image_size[0]

    @property
    def height(self) -> int:
        return self.image_size[1]

    def token(self) -> GenerationToken:
        return GenerationToken(self.source_id, self.stream_epoch, self.layout_version)

    def age_ms(self, *, now_ns: Optional[int] = None) -> float:
        now = time.perf_counter_ns() if now_ns is None else now_ns
        return (now - self.observed_monotonic_ns) / 1_000_000.0

    def as_dict(self) -> Dict[str, Any]:
        """只含元数据。像素不进日志、不进回执。"""
        return {
            "source_id": self.source_id,
            "stream_epoch": self.stream_epoch,
            "frame_id": self.frame_id,
            "media_time_ns": self.media_time_ns,
            "observed_at": self.observed_at,
            "image_size": list(self.image_size),
            "source_size": list(self.source_size),
            "crop_origin": list(self.crop_origin),
            "layout_version": self.layout_version,
            "frame_content_signature": self.frame_content_signature,
            "is_repeat": self.is_repeat,
            "is_black": self.is_black,
            "dropped_before": self.dropped_before,
            "has_pixels": self.pixels is not None,
        }


@dataclass
class CaptureStats:
    """真实运行统计。只报一个 FPS 不够。"""

    arrived: int = 0
    accepted: int = 0
    dropped_by_sampling: int = 0
    dropped_by_queue: int = 0
    repeats: int = 0
    black_frames: int = 0
    # None = 尚未发生。不要用 0 当哨兵：注入的测试时钟可以合法地等于 0。
    started_monotonic_ns: Optional[int] = None
    last_frame_monotonic_ns: Optional[int] = None
    last_accepted_monotonic_ns: Optional[int] = None
    resize_events: int = 0

    def elapsed_s(self, *, now_ns: Optional[int] = None) -> float:
        if self.started_monotonic_ns is None:
            return 0.0
        now = time.perf_counter_ns() if now_ns is None else now_ns
        return (now - self.started_monotonic_ns) / 1_000_000_000.0

    def arrival_fps(self, *, now_ns: Optional[int] = None) -> float:
        elapsed = self.elapsed_s(now_ns=now_ns)
        return self.arrived / elapsed if elapsed > 0 else 0.0

    def accepted_fps(self, *, now_ns: Optional[int] = None) -> float:
        elapsed = self.elapsed_s(now_ns=now_ns)
        return self.accepted / elapsed if elapsed > 0 else 0.0

    def as_dict(self, *, now_ns: Optional[int] = None) -> Dict[str, Any]:
        return {
            "arrived": self.arrived,
            "accepted": self.accepted,
            "dropped_by_sampling": self.dropped_by_sampling,
            "dropped_by_queue": self.dropped_by_queue,
            "repeats": self.repeats,
            "black_frames": self.black_frames,
            "resize_events": self.resize_events,
            "elapsed_s": round(self.elapsed_s(now_ns=now_ns), 3),
            "arrival_fps": round(self.arrival_fps(now_ns=now_ns), 2),
            "accepted_fps": round(self.accepted_fps(now_ns=now_ns), 2),
            "fps_note": "到达帧率由画面变化驱动，不是保证速率；采样帧率才是识别输入。",
        }


def frame_signature(array, *, row_step: int = SIGNATURE_ROW_STEP) -> str:
    """按行采样的内容签名，用于判断「这帧和上一帧一模一样」。

    这是重复帧判据，不是密码学完整性证明，也不是牌面正确性证据。
    """
    digest = hashlib.blake2b(digest_size=16)
    digest.update(str(getattr(array, "shape", "")).encode("ascii"))
    step = max(1, int(row_step))
    digest.update(memoryview(array[::step].tobytes()))
    return digest.hexdigest()


def looks_black(array, *, row_step: int = SIGNATURE_ROW_STEP,
                threshold: float = BLACK_MEAN_MAX) -> bool:
    """采样判断全黑。受保护内容常表现为全黑，按输入限制处理，不做绕过。"""
    step = max(1, int(row_step))
    sampled = array[::step]
    if getattr(sampled, "size", 0) == 0:
        return True
    return float(sampled.mean()) <= threshold
