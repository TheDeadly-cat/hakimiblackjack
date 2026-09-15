"""Offline frozen-policy Monte Carlo. Not exact small-shoe optimal EV."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blackjack_lab.analysis.fixed_policy_mc import (
    POLICY_ALWAYS_STAND, POLICY_TOY_HARD, evaluate_fixed_policy,
)
from blackjack_lab.analysis.research_windows import parse_remaining_tokens


POLICIES = {
    "always-stand": POLICY_ALWAYS_STAND,
    "toy-hard": POLICY_TOY_HARD,
}


def main(argv=None):
    parser = argparse.ArgumentParser(description="6/7/8副冻结策略离线抽样；不是精确最优开局EV")
    parser.add_argument("--decks", type=int, default=6, choices=(6, 7, 8))
    parser.add_argument("--policy", choices=sorted(POLICIES), default="always-stand")
    parser.add_argument("--samples", type=int, default=1024)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--remaining", type=int, default=None,
                        help="中途剩余张数；缺省为整靴开局")
    parser.add_argument("--pack", default=None, help="显式点值/牌级列表，例如 10,10,9,9,8,7")
    parser.add_argument("--surrender", choices=("none", "late"), required=True,
                        help="发牌前规则；不能省略成晚投降")
    args = parser.parse_args(argv)
    from blackjack_lab.analysis.research_windows import parse_surrender_token
    pack = parse_remaining_tokens(args.pack) if args.pack else None
    report = evaluate_fixed_policy(
        n_decks=args.decks, policy=POLICIES[args.policy], n_samples=args.samples,
        seed=args.seed, remaining=args.remaining, pack=pack,
        surrender=parse_surrender_token(args.surrender))
    body = {key: value for key, value in report.items() if key != "failures"}
    print(json.dumps(body, ensure_ascii=False, indent=2))
    print("ev", report.get("ev"), "se", report.get("standard_error"),
          "sign", report.get("sign_status"), "window_state", report.get("window_state"),
          "samples_per_second", report.get("samples_per_second"),
          "not_exact_optimal", report.get("not_exact_optimal"))
    if report.get("window_claim_allowed") or report.get("window_state") != "indeterminate":
        raise SystemExit("冻结策略MC不得允许窗口声称")


if __name__ == "__main__":
    main()
