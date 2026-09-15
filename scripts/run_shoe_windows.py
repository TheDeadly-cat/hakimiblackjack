"""Run a synthetic pre-deal window study. Not a betting recommendation."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blackjack_lab.analysis.shoe_windows import (
    KIND_FULL_DEPLETE, KIND_FULL_RESHUFFLE, KIND_LATE_DEPLETE, KIND_LATE_RESHUFFLE,
    CONSUMPTION_BASIC, CONSUMPTION_LEGAL_UNSPLIT, CONSUMPTION_STAND,
    EVALUATION_EXACT_SMALL, EVALUATION_FIXED_POLICY_MC, run_window_study,
)

KINDS = {
    "late-deplete": KIND_LATE_DEPLETE,
    "late-reshuffle": KIND_LATE_RESHUFFLE,
    "full-reshuffle": KIND_FULL_RESHUFFLE,
    "full-deplete": KIND_FULL_DEPLETE,
}
EVAL_METHODS = {
    "exact-small": EVALUATION_EXACT_SMALL,
    "fixed-policy-mc": EVALUATION_FIXED_POLICY_MC,
}
EVAL_POLICIES = {
    "always-stand": CONSUMPTION_STAND,
    "toy-hard": CONSUMPTION_BASIC,
    "legal-unsplit": CONSUMPTION_LEGAL_UNSPLIT,
}


def main(argv=None):
    parser = argparse.ArgumentParser(description="发牌前窗口合成实验；剩余>16记未支持，不把当前手牌EV改称开局优势")
    parser.add_argument("--kind", choices=sorted(KINDS), default="full-reshuffle")
    parser.add_argument("--decks", type=int, default=6, choices=(6, 7, 8))
    parser.add_argument("--remaining", type=int, default=8, help="late-* 采样剩余张数")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--margin", type=float, default=0.01)
    parser.add_argument("--max-rounds", type=int, default=40)
    parser.add_argument("--cut-remaining", type=int, default=None,
                        help="切牌后剩余张数；整靴耗牌缺省 52")
    parser.add_argument("--surrender", choices=("none", "late"), required=True,
                        help="发牌前规则；不能省略成晚投降")
    parser.add_argument("--evaluation-method", choices=sorted(EVAL_METHODS), default="exact-small",
                        help="检查点评估方法；缺省仍是≤16精确穷举，不会把MC与最优并成一条曲线")
    parser.add_argument("--evaluation-policy", choices=sorted(EVAL_POLICIES), default="always-stand",
                        help="仅 fixed-policy-mc 使用的冻结策略")
    parser.add_argument("--mc-samples", type=int, default=64)
    parser.add_argument("--mc-seed", type=int, default=None)
    args = parser.parse_args(argv)
    from blackjack_lab.analysis.research_windows import parse_surrender_token
    eval_method = EVAL_METHODS[args.evaluation_method]
    eval_policy = EVAL_POLICIES[args.evaluation_policy] if eval_method == EVALUATION_FIXED_POLICY_MC else None
    report = run_window_study(kind=KINDS[args.kind], n_decks=args.decks, remaining=args.remaining,
                              seed=args.seed, margin=args.margin, max_rounds=args.max_rounds,
                              cut_remaining=args.cut_remaining,
                              surrender=parse_surrender_token(args.surrender),
                              evaluation_method=eval_method,
                              evaluation_policy_id=eval_policy,
                              mc_n_samples=args.mc_samples,
                              mc_seed=args.mc_seed)
    summary = dict(report["summary"])
    body = {key: value for key, value in report.items() if key != "rounds"}
    body["summary"] = summary
    print(json.dumps(body, ensure_ascii=False, indent=2))
    print("rounds", summary["round_count"], "available", summary["predeal_available"],
          "unsupported", summary["predeal_unsupported"], "timeout", summary["predeal_timeout"],
          "zero_window", summary["zero_window"],
          "incomplete_cannot_claim_zero_window", summary["incomplete_cannot_claim_zero_window"])


if __name__ == "__main__":
    main()
