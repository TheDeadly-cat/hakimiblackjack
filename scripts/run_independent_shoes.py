"""Independent-shoe ensemble. Rounds inside one shoe are not independent samples."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blackjack_lab.analysis.shoe_windows import (
    KIND_FULL_DEPLETE, KIND_FULL_RESHUFFLE, KIND_LATE_DEPLETE, KIND_LATE_RESHUFFLE,
    run_independent_shoes,
)

KINDS = {
    "late-deplete": KIND_LATE_DEPLETE,
    "late-reshuffle": KIND_LATE_RESHUFFLE,
    "full-reshuffle": KIND_FULL_RESHUFFLE,
    "full-deplete": KIND_FULL_DEPLETE,
}


def main(argv=None):
    parser = argparse.ArgumentParser(description="按独立牌靴汇总发牌前窗口；同靴各轮相关")
    parser.add_argument("--kind", choices=sorted(KINDS), default="late-deplete")
    parser.add_argument("--decks", type=int, default=6, choices=(6, 7, 8))
    parser.add_argument("--remaining", type=int, default=8)
    parser.add_argument("--shoes", type=int, default=3)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--max-rounds", type=int, default=4)
    parser.add_argument("--surrender", choices=("none", "late"), required=True,
                        help="发牌前规则；不能省略成晚投降")
    args = parser.parse_args(argv)
    from blackjack_lab.analysis.research_windows import parse_surrender_token
    report = run_independent_shoes(
        kind=KINDS[args.kind], n_decks=args.decks, remaining=args.remaining,
        n_shoes=args.shoes, base_seed=args.seed, max_rounds=args.max_rounds,
        surrender=parse_surrender_token(args.surrender))
    print(json.dumps({key: value for key, value in report.items() if key != "shoes"},
                     ensure_ascii=False, indent=2))
    summary = report["summary"]
    print("shoes", report["n_shoes"], "zero_window_rate", summary["zero_window_rate"],
          "incomplete_shoe_rate", summary["incomplete_shoe_rate"],
          "incomplete_cannot_claim_zero_window", summary["incomplete_cannot_claim_zero_window"],
          "positive_ev_shoes", summary["positive_ev_shoes"],
          "coverage", summary["mean_signal_coverage"])


if __name__ == "__main__":
    main()
