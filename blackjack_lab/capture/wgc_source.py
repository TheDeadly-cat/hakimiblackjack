# -*- coding: utf-8 -*-
"""Windows Graphics Capture 实时来源（唯一主采集后端）。

只捕获用户明确选定的窗口或显示器；被系统或平台阻止时明确停止，不绕过限制。

本机实测行为（Windows 11 build 26200，见 docs/vision/V0.3c-实时捕获.md）：
- WGC 是变化驱动的：静止窗口只给一帧就没有后续，这不是故障。
- 阻塞式 start() 在没有新帧时不会返回，因此这里只用 start_free_threaded()。
- 回调在采集线程执行，交给我们的缓冲随后会被复用，必须复制后再使用。
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

from .contracts import (
    DEFAULT_QUEUE_LENGTH, DEFAULT_STALL_MS, DEFAULT_TARGET_FPS,
    SOURCE_MONITOR, SOURCE_WINDOW, STATUS_SOURCE_LOST,
    CaptureRejected, CaptureUnavailable, FramePacket, GenerationToken, SourceSpec,
)
from .frame_intake import FrameIntake

# WinRT TimeSpan 以 100 纳秒为单位。
TIMESPAN_TICK_NS = 100

INSTALL_HINT = (
    "未安装实时捕获依赖（windows-capture）。"
    "录像回放、本地图识牌与手动录牌不受影响。"
    "若要实时捕获：pip install -r requirements-capture.txt"
)


def wgc_available() -> bool:
    try:
        import windows_capture  # noqa: F401
        return True
    except ImportError:
        return False


def load_wgc():
    try:
        from windows_capture import WindowsCapture
        return WindowsCapture
    except ImportError as exc:
        raise CaptureUnavailable(INSTALL_HINT) from exc


class WgcLiveSource:
    """一个来源 = 一个采集线程 + 一个有界最新帧入口。"""

    def __init__(self, spec: SourceSpec, *,
                 target_fps: float = DEFAULT_TARGET_FPS,
                 queue_length: int = DEFAULT_QUEUE_LENGTH,
                 layout_version: int = 0,
                 stall_ms: float = DEFAULT_STALL_MS,
                 cursor_capture: bool = False,
                 draw_border: bool = False):
        self.spec = spec
        self.intake = FrameIntake(
            spec.source_id,
            layout_version=layout_version,
            queue_length=queue_length,
            target_fps=target_fps,
            crop=spec.crop,
            stall_ms=stall_ms,
        )
        self._cursor_capture = cursor_capture
        self._draw_border = draw_border
        self._control = None
        self._capture = None
        self._callback_error: Optional[str] = None
        self._lock = threading.Lock()
        self._running = False

    # ---- 生命周期 ----

    def start(self) -> GenerationToken:
        with self._lock:
            if self._running:
                raise CaptureRejected("该来源已在捕获中")
        WindowsCapture = load_wgc()
        self._verify_source_alive()

        kwargs: Dict[str, Any] = {
            "cursor_capture": self._cursor_capture,
            "draw_border": self._draw_border,
        }
        if self.spec.kind == SOURCE_WINDOW:
            kwargs["window_hwnd"] = int(self.spec.window_hwnd)
        else:
            # windows-capture 的显示器序号从 1 开始，0 会被拒绝。
            kwargs["monitor_index"] = int(self.spec.monitor_index)

        try:
            capture = WindowsCapture(**kwargs)
        except Exception as exc:  # noqa: BLE001 - 后端抛的是通用异常
            raise CaptureRejected(f"无法创建捕获会话：{exc}") from exc

        intake = self.intake

        @capture.event
        def on_frame_arrived(frame, capture_control):  # noqa: ANN001
            try:
                buffer = frame.frame_buffer
                timespan = getattr(frame, "timespan", None)
                media_ns = int(timespan) * TIMESPAN_TICK_NS if timespan else None
                intake.offer(buffer, media_time_ns=media_ns)
            except Exception as exc:  # noqa: BLE001 - 回调里抛出会让采集线程静默死掉
                self._note_callback_error(repr(exc))
                capture_control.stop()

        @capture.event
        def on_closed():
            intake.mark_source_lost()

        self._capture = capture
        intake.mark_started()
        try:
            self._control = capture.start_free_threaded()
        except Exception as exc:  # noqa: BLE001
            intake.mark_denied(str(exc))
            raise CaptureRejected(f"捕获被拒绝或启动失败：{exc}") from exc
        with self._lock:
            self._running = True
        return intake.token()

    def stop(self, *, timeout: float = 2.0) -> None:
        """只停本来源。不杀其他进程，不动其他捕获会话。"""
        control = self._control
        with self._lock:
            self._running = False
        if control is not None:
            try:
                control.stop()
            except Exception:  # noqa: BLE001 - 已结束的会话再停不算错误
                pass
            deadline = time.perf_counter() + timeout
            while time.perf_counter() < deadline:
                try:
                    if control.is_finished():
                        break
                except Exception:  # noqa: BLE001
                    break
                time.sleep(0.02)
        self._control = None
        self._capture = None
        self.intake.mark_stopped()

    def __enter__(self) -> "WgcLiveSource":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.stop()
        return False

    # ---- 状态 ----

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    def latest(self) -> Optional[FramePacket]:
        return self.intake.latest()

    def token(self) -> GenerationToken:
        return self.intake.token()

    def status(self) -> str:
        if self.spec.kind == SOURCE_WINDOW and self._running:
            note = self._window_note()
            if note is not None:
                self.intake.mark_source_lost()
                return STATUS_SOURCE_LOST
        return self.intake.status()

    def report(self) -> Dict[str, Any]:
        data = self.intake.report()
        data["source"] = self.spec.as_dict()
        data["backend"] = "windows-graphics-capture"
        data["running"] = self.running
        data["callback_error"] = self._callback_error
        if self.spec.kind == SOURCE_WINDOW:
            data["source_note"] = self._window_note()
        return data

    # ---- 内部 ----

    def _note_callback_error(self, message: str) -> None:
        with self._lock:
            self._callback_error = message
        self.intake.mark_denied(f"采集回调异常：{message}")

    def _window_note(self) -> Optional[str]:
        """返回 None 表示窗口正常；否则是停止捕获的原因。"""
        from .window_list import describe_window
        try:
            info = describe_window(int(self.spec.window_hwnd))
        except CaptureRejected:
            return "窗口已关闭"
        if info.minimized:
            return "窗口已最小化，画面不再更新"
        return None

    def _verify_source_alive(self) -> None:
        if self.spec.kind != SOURCE_WINDOW:
            return
        note = self._window_note()
        if note is not None:
            raise CaptureRejected(f"无法开始捕获：{note}")


def open_window_source(hwnd: int, **kwargs) -> WgcLiveSource:
    from .window_list import describe_window
    info = describe_window(int(hwnd))
    spec = SourceSpec(
        kind=SOURCE_WINDOW,
        window_hwnd=int(hwnd),
        title_snapshot=info.title,
        crop=kwargs.pop("crop", None),
    )
    return WgcLiveSource(spec, **kwargs)


def open_monitor_source(monitor_index: int, **kwargs) -> WgcLiveSource:
    if int(monitor_index) < 1:
        raise CaptureRejected("显示器序号从 1 开始")
    spec = SourceSpec(
        kind=SOURCE_MONITOR,
        monitor_index=int(monitor_index),
        title_snapshot=f"显示器 {monitor_index}",
        crop=kwargs.pop("crop", None),
    )
    return WgcLiveSource(spec, **kwargs)
