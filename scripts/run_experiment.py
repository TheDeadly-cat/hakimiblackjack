"""Run a synthetic 6/7/8-deck contrast or a frozen history-prefix replay."""
import argparse
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blackjack_lab.experiments.contracts import KIND_HISTORY, KIND_SYNTHETIC
from blackjack_lab.experiments.runner import ExperimentRunner
from blackjack_lab.experiments.scenarios import config_from_mapping


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("synthetic", "history"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--decks", default="6,7,8")
    parser.add_argument("--template", choices=("single", "split", "das"), default="single")
    parser.add_argument("--player", default="10,6")
    parser.add_argument("--up", default="10")
    parser.add_argument("--removed", default="")
    parser.add_argument("--seat", default="玩家1")
    parser.add_argument("--db", type=Path)
    parser.add_argument("--session")
    parser.add_argument("--through-seq", type=int)
    parser.add_argument("--config", type=Path, help="JSON 配置；命令行选项覆盖其中的字段")
    args = parser.parse_args(argv)
    data = {}
    if args.config:
        data.update(json.loads(args.config.read_text(encoding="utf-8")))
    data["kind"] = KIND_HISTORY if args.mode == "history" else KIND_SYNTHETIC
    data["n_decks"] = args.decks
    data["template"] = args.template
    data["player_ranks"] = args.player
    data["dealer_up"] = args.up
    data["extra_removed"] = args.removed
    data["seat"] = args.seat
    if args.db:
        data["db_path"] = str(args.db)
    if args.session:
        data["session_id"] = args.session
    if args.through_seq is not None:
        data["through_seq"] = args.through_seq
    config = config_from_mapping(data, uuid.uuid4().hex)
    saved = ExperimentRunner().run(config, args.output)
    print(json.dumps({"json": str(saved["json"]), "csv": str(saved["csv"]),
                      "items": len(saved["record"]["items"])}, ensure_ascii=False), flush=True)
    failed = [item for item in saved["record"]["items"] if item.get("status") not in ("available",)]
    return 0 if saved["record"]["items"] and not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
