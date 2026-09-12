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


def _apply_flag(data, key, value, fallback=None, aliases=()):
    if value is not None:
        data[key] = value
        return
    if any(data.get(name) not in (None, "") for name in (key, *aliases)):
        return
    if fallback is not None:
        data[key] = fallback


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("synthetic", "history"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--decks")
    parser.add_argument("--template", choices=("single", "split", "das"))
    parser.add_argument("--player")
    parser.add_argument("--up")
    parser.add_argument("--removed")
    parser.add_argument("--seat")
    parser.add_argument("--db", type=Path)
    parser.add_argument("--session")
    parser.add_argument("--through-seq")
    parser.add_argument("--config", type=Path, help="JSON 配置；只有显式命令行选项才覆盖其中的字段")
    args = parser.parse_args(argv)
    data = {}
    if args.config:
        data.update(json.loads(args.config.read_text(encoding="utf-8")))
    data["kind"] = KIND_HISTORY if args.mode == "history" else KIND_SYNTHETIC
    synthetic = data["kind"] == KIND_SYNTHETIC
    _apply_flag(data, "n_decks", args.decks, "6,7,8", aliases=("decks",))
    _apply_flag(data, "template", args.template, "single" if synthetic else None)
    _apply_flag(data, "player_ranks", args.player, "10,6" if synthetic else None, aliases=("player",))
    _apply_flag(data, "dealer_up", args.up, "10" if synthetic else None, aliases=("up",))
    _apply_flag(data, "extra_removed", args.removed, aliases=("removed",))
    _apply_flag(data, "seat", args.seat, "玩家1")
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
