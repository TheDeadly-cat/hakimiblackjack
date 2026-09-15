"""Synthetic recording-error contrast for pre-deal windows. Not a betting tool."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blackjack_lab.analysis.observation_error import run_observation_error_study
from blackjack_lab.analysis.shoe_windows import sample_pack


def main(argv=None):
    parser = argparse.ArgumentParser(description="发牌前窗口的合成录牌误差对照；不是未使用真实录像")
    parser.add_argument("--remaining", type=int, default=8)
    parser.add_argument("--decks", type=int, default=6, choices=(6, 7, 8))
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--lag", type=int, default=1)
    parser.add_argument("--max-rounds", type=int, default=8)
    parser.add_argument("--surrender", choices=("none", "late"), required=True,
                        help="发牌前规则；不能省略成晚投降")
    args = parser.parse_args(argv)
    from blackjack_lab.analysis.research_windows import parse_surrender_token
    pack = sample_pack(args.decks, args.remaining, __import__("random").Random(args.seed))
    report = run_observation_error_study(pack=pack, seed=args.seed, lag_rounds=args.lag,
                                         max_rounds=args.max_rounds,
                                         surrender=parse_surrender_token(args.surrender))
    body = {key: value for key, value in report.items() if key != "rounds"}
    print(json.dumps(body, ensure_ascii=False, indent=2))
    print("truth_positive", report["summary"]["truth_positive"],
          "zero_window_truth", report["summary"]["zero_window_truth"],
          "incomplete_cannot_claim_zero_window",
          report["summary"]["incomplete_cannot_claim_zero_window"],
          "delay_fn", report["summary"]["delay"]["false_negative"],
          "rank_fp", report["summary"]["rank_error"]["false_positive"],
          "event_miss_fp", report["summary"]["event_miss"]["false_positive"])


if __name__ == "__main__":
    main()
