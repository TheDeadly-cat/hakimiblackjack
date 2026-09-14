"""Fullscreen browser/hotkey acceptance. Empty evidence is not a pass."""
from __future__ import annotations

import ctypes
import os

from .overlay_exclusion import source_frame_excludes_overlay

ITEMS = (
    "f11_or_page_fullscreen",
    "hotkey_with_browser_focus",
    "ime_and_held_keys",
    "dpi_100_125_150",
    "drag_and_multi_monitor",
    "browser_minimized_or_dropped",
    "source_frame_excludes_overlay",
    "rebind_after_round_end",
)

SCHEMA = "hakimi-fullscreen-acceptance-v1"


def empty_evidence():
    return {item: {"passed": False, "evidence_path": None, "notes": "尚未在用户 Windows 上验收"}
            for item in ITEMS}


def accepted(evidence=None):
    evidence = evidence or empty_evidence()
    if set(ITEMS) - set(evidence):
        return False
    return all(
        item.get("passed") is True and item.get("evidence_path")
        for item in evidence.values()
    )


def probe_environment(widget=None):
    """Desktop geometry only. Never marks a fullscreen item passed."""
    payload = {"not_acceptance": True, "note": "环境探测不是 F11/源帧验收", "os": os.name}
    if widget is not None:
        payload.update(
            screen_width=int(widget.winfo_screenwidth()),
            screen_height=int(widget.winfo_screenheight()),
            pixels_per_inch=float(widget.winfo_fpixels("1i")),
        )
    if os.name == "nt":
        try:
            user32 = ctypes.WinDLL("user32")
            payload["primary_width"] = int(user32.GetSystemMetrics(0))
            payload["primary_height"] = int(user32.GetSystemMetrics(1))
            payload["virtual_screen_width"] = int(user32.GetSystemMetrics(78))
            payload["virtual_screen_height"] = int(user32.GetSystemMetrics(79))
            payload["monitor_count"] = int(user32.GetSystemMetrics(80))
            getter = getattr(user32, "GetDpiForSystem", None)
            if getter is not None:
                getter.restype = ctypes.c_uint
                payload["system_dpi"] = int(getter())
        except Exception as error:
            payload["probe_error"] = str(error)
    return payload


def attach_source_frame_probe(evidence, frame, *, felt_bgr, overlay_bgr, evidence_path=None,
                              source_is_browser_capture=False):
    """Pixel item only. A synthetic block or overlay screenshot cannot pass F11."""
    evidence = dict(evidence or empty_evidence())
    item = source_frame_excludes_overlay(frame, felt_bgr=felt_bgr, overlay_bgr=overlay_bgr)
    passed = bool(item["passed"] and evidence_path and source_is_browser_capture)
    evidence["source_frame_excludes_overlay"] = {
        "passed": passed,
        "evidence_path": evidence_path,
        "notes": item["note"],
        "overlay_fraction": item["overlay_fraction"],
        "felt_fraction": item["felt_fraction"],
        "not_fullscreen_acceptance": True,
        "source_is_browser_capture": bool(source_is_browser_capture),
    }
    return evidence


def report(evidence=None, environment=None):
    evidence = evidence or empty_evidence()
    return {
        "schema": SCHEMA,
        "accepted": accepted(evidence),
        "evidence": evidence,
        "environment": environment or {"not_acceptance": True},
        "note": "不能用浮层截图换签源帧；缺项即未通过",
    }
