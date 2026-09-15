"""Compare attested unused-holdout remaining packs. Refuses development materials."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blackjack_lab.analysis.unused_holdout import compare_holdout


def main(argv=None):
    parser = argparse.ArgumentParser(description="未使用留出发牌前窗口对照；缺声明即拒绝")
    parser.add_argument("package")
    parser.add_argument("--surrender", choices=("none", "late"), required=True,
                        help="发牌前规则；不能省略成晚投降")
    args = parser.parse_args(argv)
    from blackjack_lab.analysis.research_windows import parse_surrender_token
    report = compare_holdout(args.package, surrender=parse_surrender_token(args.surrender))
    body = {key: value for key, value in report.items() if key != "rounds"}
    print(json.dumps(body, ensure_ascii=False, indent=2))
    print("independent_video", report["independent_video"],
          "declared_unused_video", report.get("declared_unused_video"),
          "evidence_level", report.get("evidence_level"),
          "identity_chain", len(report.get("identity_chain") or []),
          "truth_positive", report["summary"]["truth_positive"],
          "observer_fn", report["summary"]["observer"]["false_negative"],
          "incomplete_cannot_claim_zero_window",
          report["summary"]["incomplete_cannot_claim_zero_window"])


if __name__ == "__main__":
    main()
