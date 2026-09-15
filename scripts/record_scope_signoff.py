"""Record a bounded human sign-off. Software cannot invent the confirmation."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blackjack_lab.analysis.acceptance_pack import (
    HUMAN_CONFIRMATION_PHRASE, SCOPES, freeze_status, record_scope_signoff,
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
    args = parser.parse_args()
    pack = json.loads(args.pack.read_text(encoding="utf-8"))
    pack = record_scope_signoff(
        pack, scope=args.scope, attested_by=args.attested_by,
        code_commit=args.code_commit, criteria=args.criteria, result=args.result,
        confirmation_phrase=args.confirmation_phrase, recorded_by=args.recorded_by)
    if pack.get("accepted") or pack.get("passed"):
        raise SystemExit("范围内签收不得把全局验收包写成通过")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_manifest(args.output, pack)
    status = freeze_status(
        code_commit=args.code_commit, m4_pack=pack, scope=args.scope)
    print(json.dumps({
        "output": str(args.output),
        "scope": args.scope,
        "attested_by": args.attested_by,
        "result": args.result,
        "accepted_global": pack["accepted"],
        "scope_ready_if_tests_bound": status["scope_accepted"],
        "freeze_blockers_without_technical_flags": status["blockers"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
