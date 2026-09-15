"""Play 3/6 rounds on 6/7/8-deck shoes. Not multi-player EV or a betting tool."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blackjack_lab.analysis.round_windows import run_round_window_study
from blackjack_lab.analysis.shoe_windows import CONSUMPTION_BASIC, CONSUMPTION_PI, CONSUMPTION_STAND

POLICIES = {
    "stand": CONSUMPTION_STAND,
    "basic": CONSUMPTION_BASIC,
    "composition": CONSUMPTION_PI,
}


def main(argv=None):
    parser = argparse.ArgumentParser(description="前三/六轮消耗对照；其他座位不是独立样本")
    parser.add_argument("--decks", type=int, default=6, choices=(6, 7, 8))
    parser.add_argument("--players", type=int, default=1, choices=range(1, 8))
    parser.add_argument("--after", default="3,6", help="快照轮次，逗号分隔")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--target-policy", choices=sorted(POLICIES), default="basic")
    parser.add_argument("--other-policy", choices=sorted(POLICIES), default="stand")
    parser.add_argument("--surrender", choices=("none", "late"), required=True,
                        help="发牌前规则；不能省略成晚投降")
    args = parser.parse_args(argv)
    from blackjack_lab.analysis.research_windows import parse_surrender_token
    after = []
    for part in args.after.replace("，", ",").split(","):
        item = part.strip()
        if not item:
            continue
        if not item.isdigit() or int(item) < 1:
            raise SystemExit("对照轮数必须为正整数")
        after.append(int(item))
    report = run_round_window_study(
        n_decks=args.decks, n_players=args.players, after_rounds=tuple(after), seed=args.seed,
        target_policy=POLICIES[args.target_policy], other_policy=POLICIES[args.other_policy],
        surrender=parse_surrender_token(args.surrender))
    body = {key: value for key, value in report.items() if key != "rounds"}
    print(json.dumps(body, ensure_ascii=False, indent=2))
    for snap in report["snapshots"]:
        predeal = snap["predeal"]
        print("after", snap["after_rounds"], "remaining", snap["physical_remaining"],
              "predeal", predeal.get("status"), "ev", predeal.get("ev"))
    summary = report["summary"]
    print("snapshots", summary["snapshot_count"],
          "zero_window", summary["zero_window"],
          "incomplete_cannot_claim_zero_window", summary["incomplete_cannot_claim_zero_window"])


if __name__ == "__main__":
    main()
