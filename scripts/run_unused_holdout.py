"""Compare attested unused-holdout remaining packs. Refuses development materials."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blackjack_lab.analysis.unused_holdout import compare_holdout


def main():
    parser = argparse.ArgumentParser(description="未使用留出发牌前窗口对照；缺声明即拒绝")
    parser.add_argument("package")
    args = parser.parse_args()
    report = compare_holdout(args.package)
    body = {key: value for key, value in report.items() if key != "rounds"}
    print(json.dumps(body, ensure_ascii=False, indent=2))
    print("independent_video", report["independent_video"],
          "truth_positive", report["summary"]["truth_positive"],
          "observer_fn", report["summary"]["observer"]["false_negative"])


if __name__ == "__main__":
    main()
