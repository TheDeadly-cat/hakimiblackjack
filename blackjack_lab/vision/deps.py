# -*- coding: utf-8 -*-
"""识牌可选依赖：延迟加载，未安装时不得影响手动录牌与数学分析。"""
from __future__ import annotations


class VisionDependencyError(RuntimeError):
    """缺少识牌依赖。原手动流程应继续可用。"""


class ImageRejected(ValueError):
    """图片或区域被受控拒绝，不是识别成功。"""


def cv2_available() -> bool:
    try:
        import cv2  # noqa: F401
        return True
    except ImportError:
        return False


def load_cv2():
    try:
        import cv2
        return cv2
    except ImportError as exc:
        raise VisionDependencyError(
            "未安装识牌可选依赖（OpenCV）。手动录牌、恢复和数学自检不受影响。"
            "若要运行识牌演示：pip install -r requirements-vision.txt"
        ) from exc


def load_numpy():
    try:
        import numpy
        return numpy
    except ImportError as exc:
        raise VisionDependencyError(
            "未安装识牌可选依赖（numpy，通常随 OpenCV 安装）。"
            "手动录牌与数学分析不受影响。"
        ) from exc
