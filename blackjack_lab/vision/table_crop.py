# -*- coding: utf-8 -*-
"""把整桌面帧裁成冻结牌桌画布。不改原录像。"""
from __future__ import annotations

from .contracts import LayoutProfile
from .image_io import LoadedImage, crop_rgb, sha256_bytes


def _crop_loaded(loaded: LoadedImage, box) -> LoadedImage:
    x, y, w, h = box
    rgb = crop_rgb(loaded, x, y, w, h)
    return LoadedImage(
        path=loaded.path, width=w, height=h,
        sha256=sha256_bytes(rgb), rgb=rgb, byte_size=len(rgb),
        format=loaded.format,
    )


def apply_layout_crops(loaded: LoadedImage, layout: LayoutProfile) -> LoadedImage:
    current = loaded
    if (layout.source_crop and layout.source_frame_width
            and layout.source_frame_height
            and current.width == layout.source_frame_width
            and current.height == layout.source_frame_height):
        current = _crop_loaded(current, layout.source_crop)
    if layout.felt_crop and (
            current.width, current.height) != (layout.canvas_width, layout.canvas_height):
        x, y, w, h = layout.felt_crop
        if x + w <= current.width and y + h <= current.height:
            current = _crop_loaded(current, layout.felt_crop)
    return current
