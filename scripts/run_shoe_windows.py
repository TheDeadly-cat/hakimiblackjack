"""Run a synthetic pre-deal window study. Not a betting recommendation."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blackjack_lab.analysis.shoe_windows import (
    KIND_FULL_DEPLETE, KIND_FULL_RESHUFFLE, KIND_LATE_DEPLETE, KIND_LATE_RESHUFFLE,
    run_window_study,
)

KINDS = {
    "late-deplete": KIND_LATE_DEPLETE,
    "late-reshuffle": KIND_LATE_RESHUFFLE,
    "full-reshuffle": KIND_FULL_RESHUFFLE,
    "full-deplete": KIND_FULL_DEPLETE,
}


def main():
    parser = argparse.ArgumentParser(description="发牌前窗口合成实验；剩余>16记未支持，不把当前手牌EV改称开局优势")
    parser.add_argument("--kind", choices=sorted(KINDS), default="full-reshuffle")
    parser.add_argument("--decks", type=int, default=6, choices=(6, 7, 8))
    parser.add_argument("--remaining", type=int, default=8, help="late-* 采样剩余张数")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--margin", type=float, default=0.01)
    parser.add_argument("--max-rounds", type=int, default=40)
    args = parser.parse_args()
    report = run_window_study(kind=KINDS[args.kind], n_decks=args.decks, remaining=args.remaining,
                              seed=args.seed, margin=args.margin, max_rounds=args.max_rounds)
    summary = dict(report["summary"])
    body = {key: value for key, value in report.items() if key != "rounds"}
    body["summary"] = summary
    print(json.dumps(body, ensure_ascii=False, indent=2))
    print("rounds", summary["round_count"], "available", summary["predeal_available"],
          "unsupported", summary["predeal_unsupported"], "timeout", summary["predeal_timeout"],
          "zero_window", summary["zero_window"])


if __name__ == "__main__":
    main()
