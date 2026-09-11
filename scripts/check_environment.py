"""Print whether this machine can compile/run the split accelerator. No installs."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from blackjack_lab.analysis.environment import format_report, inspect_environment


def main():
    parser = argparse.ArgumentParser(description="Hakimi Blackjack Lab 分牌依赖预检（不安装软件、不提权）")
    parser.add_argument("--json", action="store_true", help="只输出 JSON")
    parser.add_argument("--prepare", action="store_true", help="若环境允许则实际准备当前源码产物并计时")
    args = parser.parse_args()
    report = inspect_environment(prepare=args.prepare)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_report(report))
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
