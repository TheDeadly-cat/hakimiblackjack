"""Unknown-composition interval research. A mean shoe is not a window."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blackjack_lab.analysis.composition_interval import (
    evaluate_common_policy_interval, evaluate_feasible_interval,
    evaluate_infoset_first_action_maxmin, evaluate_interval,
    evaluate_listed_policy_maxmin, refuse_mean_shoe,
)
from blackjack_lab.analysis.research_windows import parse_remaining_tokens


def _pack(text):
    return list(parse_remaining_tokens(text))


def main(argv=None):
    parser = argparse.ArgumentParser(description="候选剩余组成区间；不公布平均牌靴为发牌前EV")
    parser.add_argument("--candidates", nargs="*", help="每组逗号分隔点值；缺省则拒绝平均牌靴")
    parser.add_argument("--common-policy", choices=("always-stand", "toy-hard"), default=None,
                        help="同一冻结π评估所列候选；缺省为分别优化的诊断包络")
    parser.add_argument("--listed-maxmin", action="store_true",
                        help="在冻结停牌与玩具硬规则上计算 maxmin；不是组成最优包络")
    parser.add_argument("--infoset-maxmin", action="store_true",
                        help="当前手可见信息第一动作 maxmin；不是发牌前开局优势")
    parser.add_argument("--infoset-player", default=None, help="可见玩家牌，逗号分隔点值")
    parser.add_argument("--infoset-up", type=int, default=None, help="庄家明牌点值 1-10")
    parser.add_argument("--infoset-peek", action="store_true",
                        help="庄家已检查非 BJ；缺省为未检查")
    parser.add_argument("--infoset-hit-continuation",
                        choices=("composition-optimal", "always-stand", "toy-hard"),
                        default="composition-optimal",
                        help="补牌后续：组成最优仍不是跨组成的信息可行π")
    parser.add_argument("--origin", default=None, help="起源点值包，用于枚举当时知识下的可行剩余")
    parser.add_argument("--remaining-total", type=int, default=None, help="声明剩余张数；与 --origin 一起枚举")
    parser.add_argument("--max-candidates", type=int, default=256)
    parser.add_argument("--surrender", choices=("none", "late"), required=True,
                        help="发牌前规则；none=无投降，不能省略成晚投降")
    args = parser.parse_args(argv)
    from blackjack_lab.analysis.research_windows import parse_surrender_token
    surrender = parse_surrender_token(args.surrender)
    policy_name = args.common_policy
    policy = None
    if policy_name:
        from blackjack_lab.analysis.shoe_windows import CONSUMPTION_BASIC, CONSUMPTION_STAND
        policy = CONSUMPTION_STAND if policy_name == "always-stand" else CONSUMPTION_BASIC
    if args.infoset_maxmin:
        if args.origin is not None or args.remaining_total is not None:
            raise SystemExit("可见信息 maxmin 不能与可行枚举混用")
        if policy is not None or args.listed_maxmin:
            raise SystemExit("可见信息 maxmin 不能与冻结π区间混用")
        if not args.candidates:
            raise SystemExit("可见信息 maxmin 必须给出候选剩余组成")
        if args.infoset_player is None or args.infoset_up is None:
            raise SystemExit("可见信息 maxmin 需要 --infoset-player 与 --infoset-up")
        player = tuple(_pack(args.infoset_player))
        report = evaluate_infoset_first_action_maxmin(
            [_pack(item) for item in args.candidates], player, args.infoset_up,
            bool(args.infoset_peek), surrender=surrender,
            hit_continuation=args.infoset_hit_continuation)
    elif args.origin is not None or args.remaining_total is not None:
        if args.candidates:
            raise SystemExit("可行组成枚举不能与调用方候选列表混用")
        if args.origin is None or args.remaining_total is None:
            raise SystemExit("可行组成枚举需要同时给出 --origin 与 --remaining-total")
        if args.listed_maxmin:
            raise SystemExit("所列π maxmin 目前只接受调用方候选列表，不能与可行枚举混用")
        report = evaluate_feasible_interval(
            remaining_total=args.remaining_total, origin_pack=_pack(args.origin),
            max_candidates=args.max_candidates, common_policy=policy, surrender=surrender)
    elif not args.candidates:
        report = refuse_mean_shoe()
    elif args.listed_maxmin:
        report = evaluate_listed_policy_maxmin(
            [_pack(item) for item in args.candidates], surrender=surrender)
    elif policy is not None:
        report = evaluate_common_policy_interval([_pack(item) for item in args.candidates],
                                                 policy=policy, surrender=surrender)
    else:
        report = evaluate_interval([_pack(item) for item in args.candidates], surrender=surrender)
    body = {key: value for key, value in report.items()
            if key not in ("candidates", "arm_reports", "rows")}
    print(json.dumps(body, ensure_ascii=False, indent=2))
    summary = report.get("summary") or {}
    print("ev_min", summary.get("ev_min"), "ev_max", summary.get("ev_max"),
          "listed_maxmin_ev", summary.get("listed_maxmin_ev", report.get("listed_maxmin_ev")),
          "infoset_maxmin_ev", summary.get("infoset_maxmin_ev", report.get("infoset_maxmin_ev")),
          "window", report.get("window"),
          "published_point_ev", summary.get("published_point_ev", report.get("published_point_ev")))


if __name__ == "__main__":
    main()
