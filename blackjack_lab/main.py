# -*- coding: utf-8 -*-
"""程序入口：启动 V0.2b1 中文手动录牌与分析工作台。

用法：
    python -m blackjack_lab.main                    # 启动图形界面
    python -m blackjack_lab.main --check            # 不弹窗，仅做导入与初始化自检
    python -m blackjack_lab.main --check-environment  # 检查本机分牌依赖，不安装软件
"""
from __future__ import annotations

import sys
import argparse
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Hakimi Blackjack Lab 本地手动录牌工作台")
    parser.add_argument("--check", action="store_true", help="离线核心自检，不打开窗口")
    parser.add_argument("--check-environment", action="store_true",
                        help="检查本机 Python/Tk/.NET 分牌依赖，不安装软件、不提权")
    parser.add_argument("--prepare-split", action="store_true", help="离线准备当前源码对应的 Windows 分牌数值程序")
    parser.add_argument("--db", type=Path, help="指定 SQLite 文件（省略则使用项目 data 目录）")
    parser.add_argument("--quick", action="store_true", help="同时打开置顶辅助记牌条；不自动开靴或录牌")
    parser.add_argument(
        "--usage-session", action="store_true",
        help="用临时库启动向导实测，不读写用户默认账本，也不把向导写成通过")
    args = parser.parse_args()
    if args.usage_session and args.db:
        parser.error("--usage-session 与 --db 不能同时使用")
    if args.check_environment:
        from .analysis.environment import format_report, inspect_environment
        report = inspect_environment()
        print(format_report(report))
        return 0 if report["ready"] else 1
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

    from .ui.app import BlackjackLabApp, DEFAULT_DB, usage_session_paths
    if args.usage_session:
        session = usage_session_paths()
        db_path = session["db"]
        print("USAGE_SESSION_DB=" + str(db_path), flush=True)
        print(session["manifest"]["note"], flush=True)
        print("菜单：悬浮记牌、全屏验收向导、配对操作向导。完成后仍须人工签收。", flush=True)
    else:
        db_path = args.db or DEFAULT_DB
    app = BlackjackLabApp(db_path)
    if args.usage_session:
        app.usage_session = session
    if args.quick or args.usage_session:
        app.after(100, app.act_quick_record)
    if args.usage_session:
        app.after(200, app.act_fullscreen_wizard)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
