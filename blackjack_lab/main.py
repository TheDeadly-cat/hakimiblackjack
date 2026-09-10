# -*- coding: utf-8 -*-
"""程序入口：启动 V0.2b1 中文手动录牌与分析工作台。

用法：
    python -m blackjack_lab.main          # 启动图形界面
    python -m blackjack_lab.main --check  # 不弹窗，仅做导入与初始化自检
"""
from __future__ import annotations

import sys
import argparse
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Hakimi Blackjack Lab 本地手动录牌工作台")
    parser.add_argument("--check", action="store_true", help="离线核心自检，不打开窗口")
    parser.add_argument("--prepare-split", action="store_true", help="离线准备当前源码对应的 Windows 分牌数值程序")
    parser.add_argument("--db", type=Path, help="指定 SQLite 文件（省略则使用项目 data 目录）")
    args = parser.parse_args()
    if args.prepare_split:
        from .analysis.native_backend import build_native
        print(build_native())
        return 0
    if args.check:
        from .core.cards import hand_total, is_natural_blackjack
        from .core.rules import RuleProfile
        from .core.shoe import ShoeState
        from .ledger.ledger import EventLedger
        for n in (6, 7, 8):
            shoe = ShoeState(n)
            ok, note = shoe.conservation_check()
            assert ok, note
            print(f"{n} 副初始化：总 {shoe.total_cards}，{note}")
        t, soft = hand_total(["A", "8"])
        assert (t, soft) == (19, True)
        assert is_natural_blackjack(["A", "K"])
        assert not is_natural_blackjack(["A", "5", "5"])
        EventLedger("selftest")
        print("自检通过：core/ledger 可正常导入与初始化")
        return 0

    from .ui.app import BlackjackLabApp, DEFAULT_DB
    app = BlackjackLabApp(args.db or DEFAULT_DB)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
