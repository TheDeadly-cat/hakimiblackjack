# -*- coding: utf-8 -*-
"""写出 synthetic-felt-v1 模板与冒烟图。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blackjack_lab.vision.holdout import write_bundle, write_holdout_bundle


def main() -> int:
    parser = argparse.ArgumentParser(description="生成自建识牌样式素材")
    parser.add_argument("--out", type=Path, default=ROOT / "fixtures" / "vision" / "synthetic-v1")
    parser.add_argument("--holdout", action="store_true", help="同时生成独立 holdout 画面")
    args = parser.parse_args()
    write_bundle(args.out)
    if args.holdout:
        write_holdout_bundle(args.out)
    print(f"已写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
