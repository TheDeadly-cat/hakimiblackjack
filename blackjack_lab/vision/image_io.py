# -*- coding: utf-8 -*-
"""有界本地图片读取。不下载远程 URL，不执行脚本，超限受控拒绝。"""
from __future__ import annotations

import hashlib
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .contracts import MAX_IMAGE_BYTES, MAX_PIXELS, MAX_SIDE, LayoutProfile, default_layout
from .deps import ImageRejected, cv2_available, load_cv2, load_numpy

PNG_SIG = b"\x89PNG\r\n\x1a\n"
_REMOTE_PREFIXES = ("http://", "https://", "ftp://", "sftp://", "file:")


@dataclass
class LoadedImage:
    path: Path
    width: int
    height: int
    sha256: str
    rgb: bytes
    byte_size: int
    format: str

    @property
    def pixels(self) -> int:
        return self.width * self.height


def _reject_remote(path: Path) -> None:
    text = str(path).strip()
    lowered = text.replace("\\", "/").lower()
    if any(lowered.startswith(p) or f":{p}" in lowered for p in _REMOTE_PREFIXES):
        raise ImageRejected("拒绝远程或 URI 路径，只读取本地文件")
    if lowered.startswith("//"):
        raise ImageRejected("拒绝网络共享路径")


def validate_local_image_path(path: Path | str) -> Path:
    text = str(path).strip()
    lowered = text.replace("\\", "/").lower()
    if any(lowered.startswith(p) for p in _REMOTE_PREFIXES):
        raise ImageRejected("拒绝远程或 URI 路径，只读取本地文件")
    if "://" in lowered:
        raise ImageRejected("拒绝远程或 URI 路径，只读取本地文件")
    raw = Path(path)
    _reject_remote(raw)
    try:
        resolved = raw.expanduser().resolve(strict=False)
    except OSError as exc:
        raise ImageRejected(f"无法解析路径: {path}") from exc
    _reject_remote(resolved)
    if not resolved.is_file():
        raise ImageRejected("路径不是本地文件")
    return resolved


def _check_dimensions(width: int, height: int, limits: LayoutProfile) -> None:
    if width <= 0 or height <= 0:
        raise ImageRejected("非法图像尺寸")
    if width > limits.max_side or height > limits.max_side:
        raise ImageRejected(f"边长超过上限 {limits.max_side}")
    if width * height > limits.max_pixels:
        raise ImageRejected(f"像素数超过上限 {limits.max_pixels}")


def write_png_rgb(path: Path | str, width: int, height: int, rgb: bytes) -> None:
    if len(rgb) != width * height * 3:
        raise ValueError("RGB 字节长度与尺寸不匹配")
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    raw = bytearray()
    stride = width * 3
    for y in range(height):
        raw.append(0)
        raw.extend(rgb[y * stride:(y + 1) * stride])
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    dest.write_bytes(
        PNG_SIG
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def read_png_rgb(data: bytes, limits: Optional[LayoutProfile] = None) -> tuple[int, int, bytes]:
    limits = limits or default_layout()
    if len(data) < 24 or not data.startswith(PNG_SIG):
        raise ImageRejected("不是有效 PNG")
    pos = 8
    width = height = None
    idat = bytearray()
    bit = color = inter = None
    while pos + 12 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        tag = data[pos + 4:pos + 8]
        start = pos + 8
        end = start + length
        if end + 4 > len(data):
            raise ImageRejected("PNG 截断")
        chunk = data[start:end]
        crc_got = struct.unpack(">I", data[end:end + 4])[0]
        if (zlib.crc32(tag + chunk) & 0xFFFFFFFF) != crc_got:
            raise ImageRejected("PNG 校验失败")
        pos = end + 4
        if tag == b"IHDR":
            if length != 13:
                raise ImageRejected("PNG IHDR 损坏")
            width, height, bit, color, comp, filt, inter = struct.unpack(">IIBBBBB", chunk)
            _check_dimensions(width, height, limits)
            if (bit, color, comp, filt, inter) != (8, 2, 0, 0, 0):
                raise ImageRejected("仅支持 8 位非隔行 RGB PNG；JPEG/其他编码需要识牌依赖")
        elif tag == b"IDAT":
            idat.extend(chunk)
        elif tag == b"IEND":
            break
    if width is None or height is None:
        raise ImageRejected("PNG 缺少 IHDR")
    try:
        raw = zlib.decompress(bytes(idat))
    except zlib.error as exc:
        raise ImageRejected("PNG 压缩数据损坏") from exc
    stride = width * 3
    expected = (stride + 1) * height
    if len(raw) != expected:
        raise ImageRejected("PNG 像素数据长度不匹配")
    out = bytearray(width * height * 3)
    for y in range(height):
        row_start = y * (stride + 1)
        if raw[row_start] != 0:
            raise ImageRejected("不支持的 PNG 过滤类型")
        out[y * stride:(y + 1) * stride] = raw[row_start + 1:row_start + 1 + stride]
    return width, height, bytes(out)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_image(path: Path | str, limits: Optional[LayoutProfile] = None) -> LoadedImage:
    limits = limits or default_layout()
    resolved = validate_local_image_path(path)
    size = resolved.stat().st_size
    if size <= 0:
        raise ImageRejected("空文件")
    if size > limits.max_image_bytes:
        raise ImageRejected(f"文件超过 {limits.max_image_bytes} 字节上限")
    data = resolved.read_bytes()
    digest = sha256_bytes(data)
    suffix = resolved.suffix.lower()
    if data.startswith(PNG_SIG) and not cv2_available():
        width, height, rgb = read_png_rgb(data, limits)
        return LoadedImage(resolved, width, height, digest, rgb, size, "png")
    if suffix in {".jpg", ".jpeg"} and not cv2_available():
        raise ImageRejected("解码 JPEG 需要识牌依赖：pip install -r requirements-vision.txt")
    if cv2_available():
        cv2 = load_cv2()
        np = load_numpy()
        arr = np.frombuffer(data, dtype=np.uint8)
        bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if bgr is None:
            raise ImageRejected("无法解码为图像")
        height, width = bgr.shape[:2]
        _check_dimensions(int(width), int(height), limits)
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB).tobytes()
        fmt = "png" if data.startswith(PNG_SIG) else ("jpeg" if suffix in {".jpg", ".jpeg"} else "image")
        return LoadedImage(resolved, int(width), int(height), digest, rgb, size, fmt)
    if data.startswith(PNG_SIG):
        width, height, rgb = read_png_rgb(data, limits)
        return LoadedImage(resolved, width, height, digest, rgb, size, "png")
    raise ImageRejected("无法解码该图像；安装识牌依赖后可读取 JPEG")


def rgb_to_bgr(loaded: LoadedImage):
    np = load_numpy()
    arr = np.frombuffer(loaded.rgb, dtype=np.uint8).reshape(loaded.height, loaded.width, 3)
    return arr[:, :, ::-1].copy()


def crop_rgb(loaded: LoadedImage, x: int, y: int, w: int, h: int) -> bytes:
    if w <= 0 or h <= 0:
        raise ImageRejected("裁片尺寸非法")
    if x < 0 or y < 0 or x + w > loaded.width or y + h > loaded.height:
        raise ImageRejected("牌区或裁片越界")
    out = bytearray(w * h * 3)
    src = loaded.rgb
    src_stride = loaded.width * 3
    dst_stride = w * 3
    for row in range(h):
        s = (y + row) * src_stride + x * 3
        d = row * dst_stride
        out[d:d + dst_stride] = src[s:s + dst_stride]
    return bytes(out)
