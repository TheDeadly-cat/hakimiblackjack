"""Build a table-rules difference list from a felt observation. Never accepts."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blackjack_lab.core.table_rules_diff import from_felt_observation, load_observation, write_diff


def main(argv=None):
    parser = argparse.ArgumentParser(description="从绒面观察生成规则差异表；不能写成已验收")
    parser.add_argument("--observation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    body = from_felt_observation(load_observation(args.observation))
    write_diff(args.output, body)
    print(json.dumps({
        "accepted": body["accepted"],
        "applies_to_live_table": body["applies_to_live_table"],
        "n_decks": body["n_decks"],
        "public_help_candidate_n_decks": body.get("public_help_candidate_n_decks"),
        "public_help_peek_conflict": body.get("public_help_peek_conflict"),
        "not_evolution": body["not_evolution"],
        "uncertain": body["uncertain"],
        "output": str(args.output),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
