"""List bound NVIDIA clips with suggested roles. Does not rehash."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blackjack_lab.analysis.material_roles import inventory_from_pack

DEFAULT_PACK = ROOT / ".local-evidence" / "m4-acceptance-pending.json"


def main(argv=None):
    parser = argparse.ArgumentParser(description="列出已绑定录像的建议用途；不重新哈希，不能代签")
    parser.add_argument("--pack", type=Path, default=DEFAULT_PACK)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    pack = json.loads(args.pack.read_text(encoding="utf-8"))
    inventory = inventory_from_pack(pack)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
    rows = [
        {
            "filename": row["filename"],
            "suggested_role": row["suggested_role"],
            "sha256": row["sha256"],
            "bytes": row["bytes"],
            "usage_note": row["usage_note"],
        }
        for row in inventory["rows"]
    ]
    print(json.dumps({
        "accepted": False,
        "verify_digest": False,
        "count": len(rows),
        "rows": rows,
        "note": inventory["note"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
