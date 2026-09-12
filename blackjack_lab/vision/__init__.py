# -*- coding: utf-8 -*-
"""vision：V0.3a 离线识牌（固定样式模板匹配）。

本包在导入时不加载 OpenCV。未安装识牌依赖时，手动录牌与数学分析不受影响。
识别器只产生候选观察，不写账本、不扣牌、不调用 EV。
"""
from .contracts import (
    RECOGNITION_SCHEMA_VERSION, RANKS_13, REVIEW_PENDING, STYLE_ID,
)

__all__ = [
    "RECOGNITION_SCHEMA_VERSION",
    "RANKS_13",
    "REVIEW_PENDING",
    "STYLE_ID",
]
