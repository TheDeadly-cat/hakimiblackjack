# -*- coding: utf-8 -*-
"""帧入口：采样、裁区、取得所有权、重复帧判定与有界队列。

这一层不碰 Windows API，可以脱离桌面单独测试。
采集后端只负责把原始缓冲交给 offer()，其余判断都在这里，
保证「丢帧」「冻结」「换代」三件事有唯一实现。
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

from .contracts import (
    DEFAULT_QUEUE_LENGTH, DEFAULT_STALL_MS, DEFAULT_TARGET_FPS,
    STATUS_BLACK, STATUS_DENIED, STATUS_FROZEN, STATUS_IDLE, STATUS_LIVE,
    STATUS_NO_NEW_FRAME, STATUS_SOURCE_LOST, STATUS_STARTING, STATUS_STOPPED,
    CaptureRejected, CaptureStats, FramePacket, GenerationToken,
    frame_signature, looks_black,
)


def _load_numpy():
    try:
        import numpy
    except ImportError as exc:  # pragma: no cover - 依赖缺失路径
        raise CaptureRejected(
            "实时捕获需要 numpy（随识牌依赖安装）。手动录牌与录像回放不受影响。"
        ) from exc
    return numpy


class FrameIntake:
    """线程安全的最新帧入口。采集线程调用 offer()，消费方调用 latest()。"""

    def __init__(self, source_id: str, *,
                 layout_version: int = 0,
                 queue_length: int = DEFAULT_QUEUE_LENGTH,
                 target_fps: float = DEFAULT_TARGET_FPS,
                 crop: Optional[Tuple[int, int, int, int]] = None,
                 stall_ms: float = DEFAULT_STALL_MS):
        if queue_length < 1:
            raise CaptureRejected("队列至少保留 1 帧")
        if target_fps <= 0:
            raise CaptureRejected("采样帧率必须为正")
        self.source_id = source_id
        self._lock = threading.Lock()
        self._queue: deque = deque(maxlen=int(queue_length))
        self._layout_version = int(layout_version)
        self._stream_epoch = 0
        self._crop = crop
        self._target_interval_ns = int(1_000_000_000 / float(target_fps))
        self._stall_ns = int(float(stall_ms) * 1_000_000)
        self._frame_seq = 0
        self._last_signature: Optional[str] = None
        self._last_source_size: Optional[Tuple[int, int]] = None
        self._pending_drops = 0
        self._started = False
        self._stopped = False
        self._denied_reason: Optional[str] = None
        self._source_lost = False
        self._epoch_reasons: List[Dict[str, Any]] = []
        self.stats = CaptureStats()

    # ---- 代号与生命周期 ----

    @property
    def stream_epoch(self) -> int:
        with self._lock:
            return self._stream_epoch

    @property
    def layout_version(self) -> int:
        with self._lock:
            return self._layout_version

    def token(self) -> GenerationToken:
        with self._lock:
            return GenerationToken(self.source_id, self._stream_epoch, self._layout_version)

    def mark_started(self, *, now_ns: Optional[int] = None) -> None:
        with self._lock:
            self._started = True
            self._stopped = False
            self.stats.started_monotonic_ns = (
                time.perf_counter_ns() if now_ns is None else now_ns)

    def mark_stopped(self) -> None:
        with self._lock:
            self._stopped = True
            self._queue.clear()

    def mark_denied(self, reason: str) -> None:
        with self._lock:
            self._denied_reason = reason
            self._queue.clear()

    def mark_source_lost(self) -> None:
        with self._lock:
            self._source_lost = True
            self._queue.clear()

    def new_epoch(self, reason: str) -> int:
        """切源、改区域、录像跳转等使旧推理结果作废。队列中的旧帧一并丢弃。"""
        with self._lock:
            return self._new_epoch_locked(reason)

    def _new_epoch_locked(self, reason: str) -> int:
        self._stream_epoch += 1
        self._queue.clear()
        self._last_signature = None
        self._pending_drops = 0
        self._epoch_reasons.append({"epoch": self._stream_epoch, "reason": reason})
        return self._stream_epoch

    def set_layout_version(self, version: int, *, reason: str = "布局变更") -> int:
        """区域/座位标定变化：布局版本前进，旧框不得投射到新画面。"""
        with self._lock:
            if int(version) == self._layout_version:
                return self._stream_epoch
            self._layout_version = int(version)
            return self._new_epoch_locked(reason)

    def set_crop(self, crop: Optional[Tuple[int, int, int, int]],
                 *, reason: str = "捕获区域变更") -> int:
        with self._lock:
            if crop == self._crop:
                return self._stream_epoch
            self._crop = crop
            return self._new_epoch_locked(reason)

    # ---- 采集线程入口 ----

    def offer(self, array, *,
              media_time_ns: Optional[int] = None,
              now_ns: Optional[int] = None,
              wall_time: Optional[float] = None) -> Optional[FramePacket]:
        """接收一帧原始 BGRA 缓冲。

        array 是采集后端的可复用内存视图；本方法负责复制出独占像素，
        调用方返回后可以随意覆盖该缓冲。
        """
        now = time.perf_counter_ns() if now_ns is None else now_ns
        np = _load_numpy()

        with self._lock:
            self.stats.arrived += 1
            self.stats.last_frame_monotonic_ns = now
            if self._stopped or self._denied_reason:
                return None

            height, width = int(array.shape[0]), int(array.shape[1])
            source_size = (width, height)
            if self._last_source_size is not None and self._last_source_size != source_size:
                # 窗口缩放/DPI 变化：旧坐标不能投到新画面，直接换代。
                self.stats.resize_events += 1
                self._new_epoch_locked(
                    f"来源尺寸由 {self._last_source_size} 变为 {source_size}")
            self._last_source_size = source_size

            last_accepted = self.stats.last_accepted_monotonic_ns
            if last_accepted is not None and (now - last_accepted) < self._target_interval_ns:
                self.stats.dropped_by_sampling += 1
                self._pending_drops += 1
                return None

            crop = self._crop
            layout_version = self._layout_version
            stream_epoch = self._stream_epoch
            pending = self._pending_drops
            self._pending_drops = 0

        if crop is None:
            origin = (0, 0)
            region = array
        else:
            cx, cy, cw, ch = crop
            if cx + cw > width or cy + ch > height:
                with self._lock:
                    self.stats.dropped_by_sampling += 1
                # 区域超出当前来源：不裁一块错的给识别器，等标定修正。
                return None
            origin = (cx, cy)
            region = array[cy:cy + ch, cx:cx + cw]

        # 独占像素：.copy() 一定复制，不能用 ascontiguousarray（已连续时会原样返回视图）。
        pixels = region[:, :, :3].copy() if region.shape[2] == 4 else region.copy()
        del region

        signature = frame_signature(pixels)
        is_black = looks_black(pixels)

        with self._lock:
            self._frame_seq += 1
            is_repeat = signature == self._last_signature
            self._last_signature = signature
            packet = FramePacket(
                source_id=self.source_id,
                stream_epoch=stream_epoch,
                frame_id=self._frame_seq,
                media_time_ns=media_time_ns,
                observed_at=time.time() if wall_time is None else wall_time,
                observed_monotonic_ns=now,
                image_size=(int(pixels.shape[1]), int(pixels.shape[0])),
                source_size=source_size,
                crop_origin=origin,
                layout_version=layout_version,
                frame_content_signature=signature,
                is_repeat=is_repeat,
                is_black=is_black,
                dropped_before=pending,
                pixels=pixels,
            )
            if len(self._queue) == self._queue.maxlen:
                self.stats.dropped_by_queue += 1
            self._queue.append(packet)
            self.stats.accepted += 1
            self.stats.last_accepted_monotonic_ns = now
            if is_repeat:
                self.stats.repeats += 1
            if is_black:
                self.stats.black_frames += 1
        return packet

    # ---- 消费方入口 ----

    def latest(self) -> Optional[FramePacket]:
        """取最新一帧并清空队列；被跳过的帧计入丢帧，不假装观察完整。"""
        with self._lock:
            if not self._queue:
                return None
            skipped = len(self._queue) - 1
            if skipped > 0:
                self.stats.dropped_by_queue += skipped
            packet = self._queue[-1]
            self._queue.clear()
            return packet

    def peek_depth(self) -> int:
        with self._lock:
            return len(self._queue)

    def status(self, *, now_ns: Optional[int] = None) -> str:
        now = time.perf_counter_ns() if now_ns is None else now_ns
        with self._lock:
            if self._denied_reason:
                return STATUS_DENIED
            if self._stopped:
                return STATUS_STOPPED
            if self._source_lost:
                return STATUS_SOURCE_LOST
            if not self._started:
                return STATUS_IDLE
            last = self.stats.last_frame_monotonic_ns
            if last is None:
                started = self.stats.started_monotonic_ns
                if started is not None and (now - started) > self._stall_ns:
                    return STATUS_NO_NEW_FRAME
                return STATUS_STARTING
            if (now - last) > self._stall_ns:
                return STATUS_NO_NEW_FRAME
            if self.stats.black_frames and self._queue and self._queue[-1].is_black:
                return STATUS_BLACK
            if self._last_signature is not None and self.stats.repeats:
                if self._queue and self._queue[-1].is_repeat:
                    return STATUS_FROZEN
            return STATUS_LIVE

    def report(self, *, now_ns: Optional[int] = None) -> Dict[str, Any]:
        now = time.perf_counter_ns() if now_ns is None else now_ns
        with self._lock:
            stats = self.stats.as_dict(now_ns=now)
            epochs = list(self._epoch_reasons)
            depth = len(self._queue)
            denied = self._denied_reason
        stats.update({
            "source_id": self.source_id,
            "status": self.status(now_ns=now),
            "stream_epoch": self.stream_epoch,
            "layout_version": self.layout_version,
            "queue_depth": depth,
            "epoch_changes": epochs,
            "denied_reason": denied,
        })
        return stats
