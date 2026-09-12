# -*- coding: utf-8 -*-
"""capture：授权窗口/显示器实时捕获适配层（V0.3c）。

边界（大纲 1/4-C）：只捕获用户明确授权的来源；被系统或平台阻止时
明确停止，不绕过任何屏幕捕获限制；默认不启用任何实盘平台适配。

本层只产生帧，不识别牌面、不写账本，也不导入 vision / ledger。
Win32 与后端依赖延迟加载：未安装实时捕获依赖时，手动录牌、录像回放
与数学自检必须仍然可用。
"""
from __future__ import annotations

from .contracts import (
    DEFAULT_QUEUE_LENGTH, DEFAULT_STALL_MS, DEFAULT_TARGET_FPS,
    SOURCE_KINDS, SOURCE_MONITOR, SOURCE_WINDOW,
    STATUS_BLACK, STATUS_DENIED, STATUS_FROZEN, STATUS_IDLE, STATUS_LABELS,
    STATUS_LIVE, STATUS_NO_NEW_FRAME, STATUS_SOURCE_LOST, STATUS_STARTING,
    STATUS_STOPPED,
    CaptureRejected, CaptureStats, CaptureUnavailable,
    FramePacket, GenerationToken, SourceSpec,
)
from .frame_intake import FrameIntake

__all__ = [
    "DEFAULT_QUEUE_LENGTH", "DEFAULT_STALL_MS", "DEFAULT_TARGET_FPS",
    "SOURCE_KINDS", "SOURCE_MONITOR", "SOURCE_WINDOW",
    "STATUS_BLACK", "STATUS_DENIED", "STATUS_FROZEN", "STATUS_IDLE",
    "STATUS_LABELS", "STATUS_LIVE", "STATUS_NO_NEW_FRAME",
    "STATUS_SOURCE_LOST", "STATUS_STARTING", "STATUS_STOPPED",
    "CaptureRejected", "CaptureStats", "CaptureUnavailable",
    "FramePacket", "FrameIntake", "GenerationToken", "SourceSpec",
    "WgcLiveSource", "list_capturable_windows", "describe_window",
    "find_window_by_title", "open_monitor_source", "open_window_source",
    "wgc_available",
]

_LAZY = {
    "WgcLiveSource": ".wgc_source",
    "open_window_source": ".wgc_source",
    "open_monitor_source": ".wgc_source",
    "wgc_available": ".wgc_source",
    "list_capturable_windows": ".window_list",
    "describe_window": ".window_list",
    "find_window_by_title": ".window_list",
}


def __getattr__(name: str):
    """Win32 / 后端只在真正要用时加载。"""
    module_name = _LAZY.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module
    module = import_module(module_name, __name__)
    return getattr(module, name)
