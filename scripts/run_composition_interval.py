"""Unknown-composition interval research. A mean shoe is not a window."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blackjack_lab.analysis.composition_interval import evaluate_interval, refuse_mean_shoe


def _pack(text):
    return [int(part.strip()) for part in text.replace("，", ",").split(",") if part.strip()]


def main():
    parser = argparse.ArgumentParser(description="候选剩余组成区间；不公布平均牌靴为发牌前EV")
    parser.add_argument("--candidates", nargs="*", help="每组逗号分隔点值；缺省则拒绝平均牌靴")
    args = parser.parse_args()
    if not args.candidates:
        report = refuse_mean_shoe()
    else:
        report = evaluate_interval([_pack(item) for item in args.candidates])
    body = {key: value for key, value in report.items() if key != "candidates"}
    print(json.dumps(body, ensure_ascii=False, indent=2))
    summary = report.get("summary") or {}
    print("ev_min", summary.get("ev_min"), "ev_max", summary.get("ev_max"),
          "published_point_ev", summary.get("published_point_ev", report.get("published_point_ev")))


if __name__ == "__main__":
    main()
