"""Policy consumption contrast. Realized paths are not shared counterfactuals."""
import argparse
import json
import sys
from pathlib import Path
from random import Random

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blackjack_lab.analysis.shoe_windows import (
    CONSUMPTION_BASIC, CONSUMPTION_PI, CONSUMPTION_STAND, run_policy_contrast, sample_pack,
)

POLICIES = {
    "stand": CONSUMPTION_STAND,
    "basic": CONSUMPTION_BASIC,
    "composition": CONSUMPTION_PI,
}


def main():
    parser = argparse.ArgumentParser(description="同一洗牌起点、各自耗牌；不共享实现路径")
    parser.add_argument("--decks", type=int, default=6, choices=(6, 7, 8))
    parser.add_argument("--remaining", type=int, default=8)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--max-rounds", type=int, default=3)
    parser.add_argument("--policies", nargs="+", default=["stand", "basic"], choices=sorted(POLICIES))
    args = parser.parse_args()
    pack = sample_pack(args.decks, args.remaining, Random(args.seed))
    report = run_policy_contrast(
        pack=pack, seed=args.seed, max_rounds=args.max_rounds,
        policies=tuple(POLICIES[name] for name in args.policies))
    print(json.dumps({key: value for key, value in report.items() if key != "arms"},
                     ensure_ascii=False, indent=2))
    print("shared_realized_path", report["shared_realized_path"],
          "remaining_paths_equal", report["remaining_paths_equal"])


if __name__ == "__main__":
    main()
