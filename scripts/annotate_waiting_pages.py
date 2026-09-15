"""Relabel occupancy deals that are actually between-round leftover cards."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blackjack_lab.analysis.shoe_event_draft import write_draft
from blackjack_lab.analysis.shoe_round_pages import annotate_waiting_in_draft

DEFAULT_DRAFT = ROOT / ".local-evidence" / "dev-clip-12.58.11.02-event-draft.json"


def main(argv=None):
    parser = argparse.ArgumentParser(description="根据已有静帧标注局间等待；不重新哈希原片")
    parser.add_argument("--draft", type=Path, default=DEFAULT_DRAFT)
    args = parser.parse_args(argv)
    draft = json.loads(args.draft.read_text(encoding="utf-8"))
    annotate_waiting_in_draft(draft, repo_root=ROOT)
    write_draft(args.draft, draft)
    print(json.dumps({
        "accepted": draft["accepted"],
        "waiting_pages": draft.get("waiting_pages"),
        "deals": sum(1 for event in draft["events"] if event["kind"] == "deal"),
        "between_rounds": sum(1 for event in draft["events"] if event["kind"] == "between_rounds"),
        "placeholder_slots_added": draft.get("placeholder_slots_added"),
        "output": str(args.draft),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
