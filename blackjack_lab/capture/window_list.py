# -*- coding: utf-8 -*-
"""可选窗口清单（ctypes，不引入新依赖）。

只列出用户可见的顶层窗口供人工选择；不注入、不读取窗口内存、不操控目标程序。
本进程自己的窗口会被排除，避免预览画面被再次捕获形成反馈循环。
"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .contracts import CaptureRejected

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
try:
    _dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
except OSError:  # pragma: no cover - dwmapi 在桌面版 Windows 上总是存在
    _dwmapi = None

_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

_user32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
_user32.EnumWindows.restype = wintypes.BOOL
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.IsWindowVisible.restype = wintypes.BOOL
_user32.IsWindow.argtypes = [wintypes.HWND]
_user32.IsWindow.restype = wintypes.BOOL
_user32.IsIconic.argtypes = [wintypes.HWND]
_user32.IsIconic.restype = wintypes.BOOL
_user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
_user32.GetWindowTextLengthW.restype = ctypes.c_int
_user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetWindowTextW.restype = ctypes.c_int
_user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetClassNameW.restype = ctypes.c_int
_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.GetWindowRect.restype = wintypes.BOOL
_user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.GetClientRect.restype = wintypes.BOOL
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD
_user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.GetWindowLongW.restype = wintypes.LONG

_GWL_EXSTYLE = -20
_WS_EX_TOOLWINDOW = 0x00000080
_DWMWA_CLOAKED = 14
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# 太小的窗口不可能是牌桌，列出来只会干扰选择。
MIN_LISTED_WIDTH = 240
MIN_LISTED_HEIGHT = 180


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    class_name: str
    process_id: int
    process_name: str
    x: int
    y: int
    width: int
    height: int
    client_width: int
    client_height: int
    dpi: int
    minimized: bool

    @property
    def scale(self) -> float:
        return self.dpi / 96.0 if self.dpi else 1.0

    def label(self) -> str:
        return f"{self.title}  [{self.process_name} {self.width}x{self.height}]"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "hwnd": self.hwnd,
            "title": self.title,
            "class_name": self.class_name,
            "process_id": self.process_id,
            "process_name": self.process_name,
            "rect": [self.x, self.y, self.width, self.height],
            "client_size": [self.client_width, self.client_height],
            "dpi": self.dpi,
            "scale": self.scale,
            "minimized": self.minimized,
        }


def _window_dpi(hwnd: int) -> int:
    getter = getattr(_user32, "GetDpiForWindow", None)
    if getter is None:  # pragma: no cover - Win10 1607 之前
        return 96
    getter.argtypes = [wintypes.HWND]
    getter.restype = wintypes.UINT
    try:
        return int(getter(hwnd)) or 96
    except OSError:  # pragma: no cover
        return 96


def _is_cloaked(hwnd: int) -> bool:
    """UWP 的隐藏壳窗口会「可见」但实际被 DWM 隐藏，不应列出。"""
    if _dwmapi is None:  # pragma: no cover
        return False
    value = ctypes.c_int(0)
    try:
        hresult = _dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd), ctypes.c_uint(_DWMWA_CLOAKED),
            ctypes.byref(value), ctypes.sizeof(value))
    except OSError:  # pragma: no cover
        return False
    return hresult == 0 and value.value != 0


def _process_name(pid: int) -> str:
    if not pid:
        return ""
    handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(260)
        buf = ctypes.create_unicode_buffer(size.value)
        query = getattr(_kernel32, "QueryFullProcessImageNameW", None)
        if query is None:  # pragma: no cover
            return ""
        if not query(handle, 0, buf, ctypes.byref(size)):
            return ""
        return os.path.basename(buf.value)
    finally:
        _kernel32.CloseHandle(handle)


def _window_text(hwnd: int) -> str:
    length = _user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    _user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def _class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    _user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def describe_window(hwnd: int) -> WindowInfo:
    """读取单个窗口的当前几何。窗口已销毁时受控拒绝。"""
    if not _user32.IsWindow(hwnd):
        raise CaptureRejected("窗口已关闭或句柄无效")
    rect = wintypes.RECT()
    _user32.GetWindowRect(hwnd, ctypes.byref(rect))
    client = wintypes.RECT()
    _user32.GetClientRect(hwnd, ctypes.byref(client))
    pid = wintypes.DWORD(0)
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return WindowInfo(
        hwnd=int(hwnd),
        title=_window_text(hwnd),
        class_name=_class_name(hwnd),
        process_id=int(pid.value),
        process_name=_process_name(int(pid.value)),
        x=int(rect.left), y=int(rect.top),
        width=int(rect.right - rect.left),
        height=int(rect.bottom - rect.top),
        client_width=int(client.right - client.left),
        client_height=int(client.bottom - client.top),
        dpi=_window_dpi(hwnd),
        minimized=bool(_user32.IsIconic(hwnd)),
    )


def list_capturable_windows(*, exclude_self: bool = True,
                            min_width: int = MIN_LISTED_WIDTH,
                            min_height: int = MIN_LISTED_HEIGHT) -> List[WindowInfo]:
    """列出可选的顶层窗口，按面积从大到小。

    排除本进程窗口，避免把自己的预览再捕一遍形成反馈循环。
    """
    own_pid = os.getpid() if exclude_self else -1
    found: List[WindowInfo] = []

    def _callback(hwnd, _lparam):
        if not _user32.IsWindowVisible(hwnd):
            return True
        if _user32.GetWindowLongW(hwnd, _GWL_EXSTYLE) & _WS_EX_TOOLWINDOW:
            return True
        if not _window_text(hwnd):
            return True
        if _is_cloaked(hwnd):
            return True
        try:
            info = describe_window(hwnd)
        except CaptureRejected:  # pragma: no cover - 枚举过程中窗口被关闭
            return True
        if info.process_id == own_pid:
            return True
        if info.width < min_width or info.height < min_height:
            return True
        found.append(info)
        return True

    if not _user32.EnumWindows(_WNDENUMPROC(_callback), 0):
        error = ctypes.get_last_error()
        # EnumWindows 在回调提前结束时也会返回 0；只有真实错误码才算失败。
        if error:
            raise CaptureRejected(f"枚举窗口失败，错误码 {error}")
    found.sort(key=lambda w: w.width * w.height, reverse=True)
    return found


def find_window_by_title(fragment: str) -> Optional[WindowInfo]:
    """按标题片段查找；匹配到多个时返回最大的那个，不自动操控窗口。"""
    needle = fragment.strip().lower()
    if not needle:
        return None
    for info in list_capturable_windows():
        if needle in info.title.lower():
            return info
    return None
