"""Owned tool window styles and one combination hotkey; no keyboard hooks."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import queue
import threading


HOTKEYS = {"Ctrl+Alt+Space": (0x0003, 0x20), "Ctrl+Shift+F8": (0x0006, 0x77),
           "Alt+Shift+F9": (0x0005, 0x78)}


def user32():
    dll = ctypes.WinDLL("user32", use_last_error=True)
    dll.GetParent.argtypes, dll.GetParent.restype = [wintypes.HWND], wintypes.HWND
    dll.GetWindowLongW.argtypes, dll.GetWindowLongW.restype = [wintypes.HWND, ctypes.c_int], ctypes.c_long
    dll.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    dll.SetWindowLongW.restype = ctypes.c_long
    dll.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]
    dll.SetWindowPos.restype = wintypes.BOOL
    dll.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
    dll.SetWindowDisplayAffinity.restype = wintypes.BOOL
    dll.SetForegroundWindow.argtypes, dll.SetForegroundWindow.restype = [wintypes.HWND], wintypes.BOOL
    return dll


def style_owned_window(window, *, editing=False):
    if os.name != "nt":
        return {"supported": False}
    window.update_idletasks()
    dll = user32()
    hwnd = dll.GetParent(window.winfo_id()) or window.winfo_id()
    style = dll.GetWindowLongW(hwnd, -20) | 0x00000080  # WS_EX_TOOLWINDOW
    style = style & ~0x08000000 if editing else style | 0x08000000
    # Never use WS_EX_TRANSPARENT: clicks must not reach the underlying page.
    style &= ~0x00000020
    dll.SetWindowLongW(hwnd, -20, style)
    dll.SetWindowPos(hwnd, wintypes.HWND(-1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010 | 0x0020)
    excluded = bool(dll.SetWindowDisplayAffinity(hwnd, 0x11))
    if editing:
        dll.SetForegroundWindow(hwnd)
    return {"supported": True, "hwnd": int(hwnd), "ex_style": dll.GetWindowLongW(hwnd, -20),
            "capture_exclusion_requested": excluded,
            "capture_exclusion_error": 0 if excluded else ctypes.get_last_error()}


class CombinationHotkey:
    def __init__(self, name="Ctrl+Alt+Space"):
        if name not in HOTKEYS:
            raise ValueError("请选择列表中的组合键")
        self.name = name
        self.events = queue.Queue()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name="blackjack-panel-hotkey", daemon=True)
        self.thread.start()

    def _run(self):
        if os.name != "nt":
            self.events.put(("error", "此系统未启用全局组合键，请点击悬浮条展开"))
            return
        dll = user32()
        dll.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        dll.RegisterHotKey.restype = wintypes.BOOL
        dll.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        dll.UnregisterHotKey.restype = wintypes.BOOL
        dll.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                    wintypes.UINT, wintypes.UINT, wintypes.UINT]
        dll.PeekMessageW.restype = wintypes.BOOL
        modifiers, key = HOTKEYS[self.name]
        if not dll.RegisterHotKey(None, 0x4842, modifiers | 0x4000, key):
            self.events.put(("error", f"{self.name} 注册失败/可能被占用（{ctypes.get_last_error()}）；可换组合键"))
            return
        self.events.put(("ready", self.name + " 呼出/收起；普通牌级键仅在编辑区生效"))
        try:
            msg = wintypes.MSG()
            while not self.stop.is_set():
                while dll.PeekMessageW(ctypes.byref(msg), None, 0x0312, 0x0312, 1):
                    self.events.put(("toggle", None))
                self.stop.wait(.02)
        finally:
            dll.UnregisterHotKey(None, 0x4842)

    def close(self):
        self.stop.set()
        self.thread.join(timeout=.5)
