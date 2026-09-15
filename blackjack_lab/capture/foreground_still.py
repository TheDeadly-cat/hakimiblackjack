"""Save one still of the focused window. Never marks F11 accepted."""
from __future__ import annotations

import hashlib
from pathlib import Path
from time import time

NOTE = (
    "这是向导步骤当时的前台窗口静帧，不是 F11 已通过。"
    "捕获失败只记原因，不编造画面，也不把步骤写成验收通过。"
)


def grab_foreground_still(dest_dir, *, hwnd=None, bbox=None, grabber=None, step_id="step"):
    """Write a PNG if a grabber can see the window. Software cannot pass F11."""
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / f"{step_id}-{int(time())}.png"
    payload = {
        "accepted": False,
        "passed": False,
        "not_acceptance": True,
        "path": None,
        "sha256": None,
        "bytes": None,
        "hwnd": hwnd,
        "note": NOTE,
        "capture_ok": False,
    }
    try:
        image = _grab(hwnd=hwnd, bbox=bbox, grabber=grabber)
        image.save(path)
    except Exception as error:
        payload["error"] = str(error)
        return payload
    raw = path.read_bytes()
    payload.update({
        "path": str(path),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "capture_ok": True,
    })
    return payload


def _grab(*, hwnd=None, bbox=None, grabber=None):
    if grabber is not None:
        return grabber(hwnd=hwnd, bbox=bbox)
    from PIL import ImageGrab
    if hwnd is not None:
        try:
            return ImageGrab.grab(window=int(hwnd))
        except TypeError:
            pass
    if bbox is not None:
        return ImageGrab.grab(bbox=tuple(bbox))
    return ImageGrab.grab()
