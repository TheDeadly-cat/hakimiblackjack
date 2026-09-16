"""Record a bounded human sign-off. Software cannot invent the confirmation."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blackjack_lab.analysis.acceptance_pack import (
    HUMAN_CONFIRMATION_PHRASE, MODEL_NONE_DECLARED, SCOPES, TESTS_RECEIPT_NOT_ATTACHED,
    freeze_status, record_scope_signoff,
)
from blackjack_lab.analysis.evidence import write_manifest


def main():
    parser = argparse.ArgumentParser(description="记录范围内人工签收；软件不能代签")
    parser.add_argument("--pack", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--scope", required=True, choices=list(SCOPES))
    parser.add_argument("--attested-by", required=True)
    parser.add_argument("--code-commit", required=True)
    parser.add_argument("--criteria", required=True)
    parser.add_argument("--result", choices=("accepted", "rejected", "deferred"), default="accepted")
    parser.add_argument("--confirmation-phrase", required=True,
                        help=f"必须抄写：{HUMAN_CONFIRMATION_PHRASE}")
    parser.add_argument("--recorded-by", default="software-recorder")
    parser.add_argument("--rules-digest", help="规则摘要；离线/桌面/发布范围必填")
    parser.add_argument("--strategy-digest", help="策略摘要；离线与发布范围必填")
    parser.add_argument("--model-digest", default=MODEL_NONE_DECLARED,
                        help=f"模型摘要，或不涉及模型时用 {MODEL_NONE_DECLARED}")
    parser.add_argument("--tests-receipt-digest", default=TESTS_RECEIPT_NOT_ATTACHED)
    parser.add_argument("--materials-digest", help="覆盖从验收包算出的材料摘要")
    args = parser.parse_args()
    pack = json.loads(args.pack.read_text(encoding="utf-8"))
    pack = record_scope_signoff(
        pack, scope=args.scope, attested_by=args.attested_by,
        code_commit=args.code_commit, criteria=args.criteria, result=args.result,
        confirmation_phrase=args.confirmation_phrase, recorded_by=args.recorded_by,
        materials_digest=args.materials_digest, rules_digest=args.rules_digest,
        strategy_digest=args.strategy_digest, model_digest=args.model_digest,
        tests_receipt_digest=args.tests_receipt_digest)
    if pack.get("accepted") or pack.get("passed"):
        raise SystemExit("范围内签收不得把全局验收包写成通过")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_manifest(args.output, pack)
    status = freeze_status(
        code_commit=args.code_commit, m4_pack=pack, scope=args.scope, expire_stale=True)
    print(json.dumps({
        "output": str(args.output),
        "scope": args.scope,
        "attested_by": args.attested_by,
        "result": args.result,
        "accepted_global": pack["accepted"],
        "scope_ready_if_tests_bound": status["scope_accepted"],
        "identity_ok": status["identity_ok"],
        "identity_stale": status["identity_stale"],
        "current_identity": status["current_identity"],
        "freeze_blockers_without_technical_flags": status["blockers"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
