# -*- coding: utf-8 -*-
"""本地录像只读。不改原文件，不绕过受保护内容，不抓屏。"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path

from .deps import ImageRejected, cv2_available, load_cv2
from .image_io import LoadedImage, sha256_bytes, validate_local_image_path
from .video_contracts import MAX_VIDEO_BYTES, MAX_VIDEO_SIDE, VIDEO_SUFFIXES


class VideoRejected(ImageRejected):
    """录像被受控拒绝，不是识别成功。"""


@dataclass
class VideoAsset:
    path: Path
    sha256: str
    byte_size: int
    width: int
    height: int
    fps: float
    frame_count: int
    original_preserved: bool = True

    def time_ms(self, frame_index: int) -> int:
        if self.fps <= 0:
            return frame_index * 40
        return int(round(1000.0 * frame_index / self.fps))


def validate_local_video_path(path: Path | str) -> Path:
    try:
        resolved = validate_local_image_path(path)
    except ImageRejected as exc:
        raise VideoRejected(str(exc)) from exc
    suffix = resolved.suffix.lower()
    if suffix not in VIDEO_SUFFIXES:
        raise VideoRejected(f"首版只打开本地录像 {', '.join(VIDEO_SUFFIXES)}")
    size = resolved.stat().st_size
    if size <= 0:
        raise VideoRejected("空录像")
    if size > MAX_VIDEO_BYTES:
        raise VideoRejected(f"录像超过 {MAX_VIDEO_BYTES} 字节上限")
    return resolved


class VideoReader:
    def __init__(self, path: Path | str):
        if not cv2_available():
            raise VideoRejected("解码录像需要识牌依赖：pip install -r requirements-vision.txt")
        self.path = validate_local_video_path(path)
        size = self.path.stat().st_size
        # 全内容流式摘要：相同前缀和长度的不同录像不能共享来源身份。
        with self.path.open("rb") as handle:
            self.asset_sha256 = hashlib.file_digest(handle, "sha256").hexdigest()
        self._cv2 = load_cv2()
        self._cap = self._cv2.VideoCapture(str(self.path))
        if not self._cap.isOpened():
            self.close()
            raise VideoRejected("无法打开录像。若录屏工具因受保护内容停录，当作输入限制，不做绕过")
        width = int(self._cap.get(self._cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(self._cap.get(self._cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        fps = float(self._cap.get(self._cv2.CAP_PROP_FPS) or 0.0)
        count = int(self._cap.get(self._cv2.CAP_PROP_FRAME_COUNT) or 0)
        if width <= 0 or height <= 0:
            self.close()
            raise VideoRejected("录像没有有效画面尺寸")
        if width > MAX_VIDEO_SIDE or height > MAX_VIDEO_SIDE:
            self.close()
            raise VideoRejected(f"边长超过上限 {MAX_VIDEO_SIDE}")
        self.asset = VideoAsset(
            path=self.path, sha256=self.asset_sha256, byte_size=size,
            width=width, height=height, fps=fps if fps > 0 else 0.0,
            frame_count=max(count, 0), original_preserved=True,
        )
        self._index = -1

    def close(self) -> None:
        cap = getattr(self, "_cap", None)
        if cap is not None:
            cap.release()
            self._cap = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def seek(self, frame_index: int) -> LoadedImage:
        if self._cap is None:
            raise VideoRejected("录像已关闭")
        if frame_index < 0:
            raise VideoRejected("帧号非法")
        if self.asset.frame_count and frame_index >= self.asset.frame_count:
            raise VideoRejected("超过录像末尾")
        if frame_index != self._index + 1:
            self._cap.set(self._cv2.CAP_PROP_POS_FRAMES, frame_index)
        ok, bgr = self._cap.read()
        if not ok or bgr is None:
            raise VideoRejected("读帧失败。空帧或受保护内容按输入限制处理，不重试绕过")
        self._index = frame_index
        height, width = bgr.shape[:2]
        rgb = self._cv2.cvtColor(bgr, self._cv2.COLOR_BGR2RGB).tobytes()
        frame_digest = sha256_bytes(rgb)
        return LoadedImage(
            path=self.path, width=int(width), height=int(height),
            sha256=frame_digest, rgb=rgb, byte_size=len(rgb), format="video-frame",
        )
