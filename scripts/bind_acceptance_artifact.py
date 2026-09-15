"""Bind a local file to one M4 checklist item. Never marks that item passed."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blackjack_lab.analysis.acceptance_pack import ITEMS, bind_item_artifact, build_acceptance_pack
from blackjack_lab.analysis.evidence import write_manifest


def main():
    parser = argparse.ArgumentParser(description="把本机文件挂到未勾选验收项；不能写成通过")
    parser.add_argument("--item", required=True, choices=[item_id for item_id, _label in ITEMS])
    parser.add_argument("--path", required=True, type=Path)
    parser.add_argument("--pack", type=Path, default=None, help="已有未勾选包；缺省则新建空包")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--notes", default="本机候选已哈希；人尚未核验，不能勾选")
    args = parser.parse_args()
    if args.pack is not None:
        pack = json.loads(args.pack.read_text(encoding="utf-8"))
    else:
        pack = build_acceptance_pack()
    pack = bind_item_artifact(pack, args.item, args.path, notes=args.notes)
    if pack.get("accepted") or pack.get("passed") or pack["items"][args.item]["passed"]:
        raise SystemExit("绑定不得把验收项写成通过")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_manifest(args.output, pack)
    print(json.dumps({
        "output": str(args.output),
        "item": args.item,
        "passed": pack["items"][args.item]["passed"],
        "item_evidence_level": pack["items"][args.item]["evidence_level"],
        "accepted": pack["accepted"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
