"""Fullscreen browser/hotkey acceptance. Empty evidence is not a pass."""
from __future__ import annotations

import ctypes
import hashlib
import os
from pathlib import Path

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


def files_linked(evidence=None):
    """True when every checklist item has an existing file and matching digest."""
    evidence = evidence or empty_evidence()
    if set(ITEMS) - set(evidence):
        return False
    for item in evidence.values():
        path = item.get("evidence_path")
        if not path:
            return False
        file = Path(path)
        if not file.is_file():
            return False
        expected = item.get("sha256")
        if expected:
            actual = hashlib.sha256(file.read_bytes()).hexdigest()
            if actual.lower() != str(expected).lower():
                return False
    return True


def accepted(evidence=None):
    """Software never attests F11. Linked files and checkboxes are not a browser trial."""
    return False


def evidence_level(evidence=None):
    evidence = evidence or empty_evidence()
    if not any((item or {}).get("evidence_path") for item in evidence.values()):
        return "missing"
    if files_linked(evidence):
        return "evidence-linked"
    return "declared"


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
    probe_passed = bool(item["passed"] and evidence_path and source_is_browser_capture)
    evidence["source_frame_excludes_overlay"] = {
        "passed": False,
        "probe_passed": probe_passed,
        "evidence_path": evidence_path,
        "notes": item["note"],
        "overlay_fraction": item["overlay_fraction"],
        "felt_fraction": item["felt_fraction"],
        "not_fullscreen_acceptance": True,
        "source_is_browser_capture": bool(source_is_browser_capture),
    }
    return evidence


def _sanitize_item(item):
    """Checkboxes are operator notes. Software output cannot keep passed=true."""
    row = dict(item or {})
    row["passed"] = False
    path = row.get("evidence_path")
    if not path:
        return row
    file = Path(path)
    if not file.is_file():
        return row
    actual = hashlib.sha256(file.read_bytes()).hexdigest()
    expected = row.get("sha256")
    if expected and str(expected).lower() != actual.lower():
        row["digest_mismatch"] = True
        row["actual_sha256"] = actual
        return row
    row["sha256"] = actual
    row["bytes"] = int(file.stat().st_size)
    return row


def report(evidence=None, environment=None):
    evidence = evidence or empty_evidence()
    sanitized = {key: _sanitize_item(item) for key, item in evidence.items()}
    return {
        "schema": SCHEMA,
        "accepted": accepted(sanitized),
        "evidence_level": evidence_level(sanitized),
        "evidence": sanitized,
        "identity_chain": [
            {
                "item_id": key,
                "evidence_path": item.get("evidence_path"),
                "sha256": item.get("sha256"),
                "actual_sha256": item.get("actual_sha256"),
                "digest_mismatch": item.get("digest_mismatch"),
            }
            for key, item in sanitized.items()
            if item.get("evidence_path")
        ],
        "environment": environment or {"not_acceptance": True},
        "note": "不能用浮层截图换签源帧；勾选和本地文件不能把 F11 写成通过；须在用户浏览器实测",
    }
