"""Record a named human review on one M4 item. Never marks the pack accepted."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blackjack_lab.analysis.acceptance_pack import ITEMS, record_named_review
from blackjack_lab.analysis.evidence import write_manifest


def main():
    parser = argparse.ArgumentParser(description="记录具名人工核验；不能把验收包写成通过")
    parser.add_argument("--item", required=True, choices=[item_id for item_id, _label in ITEMS])
    parser.add_argument("--attested-by", required=True, help="核验人姓名；空字符串拒绝")
    parser.add_argument("--pack", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--notes", default="具名核验已记录；软件仍不得勾选 accepted")
    parser.add_argument("--recorded-by", default="software-recorder")
    parser.add_argument("--review-scope", default="artifact-identity-and-stated-range")
    args = parser.parse_args()
    pack = json.loads(args.pack.read_text(encoding="utf-8"))
    pack = record_named_review(
        pack, args.item, attested_by=args.attested_by, notes=args.notes,
        recorded_by=args.recorded_by, review_scope=args.review_scope)
    item = pack["items"][args.item]
    if pack.get("accepted") or pack.get("passed") or item.get("passed"):
        raise SystemExit("核验记录不得把验收项写成通过")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_manifest(args.output, pack)
    print(json.dumps({
        "output": str(args.output),
        "item": args.item,
        "attested_by": item.get("attested_by"),
        "item_evidence_level": item.get("evidence_level"),
        "passed": item.get("passed"),
        "accepted": pack["accepted"],
        "human_blockers": len(pack["human_blockers"]),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
