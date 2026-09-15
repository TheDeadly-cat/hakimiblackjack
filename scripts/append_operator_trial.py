"""Append one operator-study trial. Never marks the export or M4 pack accepted."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blackjack_lab.observation.operator_study import append_export, optional_identity_fields, trial


def main():
    parser = argparse.ArgumentParser(description="向操作者对照导出追加一段试验；不能写成通过")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--condition", required=True, choices=("manual", "assisted"))
    parser.add_argument("--elapsed-seconds", type=float, required=True)
    parser.add_argument("--keystrokes", type=int, required=True)
    parser.add_argument("--clicks", type=int, required=True)
    parser.add_argument("--backlog-peak", type=int, required=True)
    parser.add_argument("--missed-cards", type=int, required=True)
    parser.add_argument("--duplicates", type=int, required=True)
    parser.add_argument("--repair-seconds", type=int, required=True)
    parser.add_argument("--human-run", action="store_true",
                        help="仅当这是真人试验时加上；缺省视为非真人")
    parser.add_argument("--operator-id", default=None)
    parser.add_argument("--video-id", default=None)
    parser.add_argument("--pair-id", default=None)
    args = parser.parse_args()
    recorded = trial(
        args.condition, human_run=bool(args.human_run),
        elapsed_seconds=args.elapsed_seconds, keystrokes=args.keystrokes,
        clicks=args.clicks, backlog_peak=args.backlog_peak,
        missed_cards=args.missed_cards, duplicates=args.duplicates,
        repair_seconds=args.repair_seconds, pause_reconcile_not_realtime=True,
        **optional_identity_fields(
            operator_id=args.operator_id, video_id=args.video_id, pair_id=args.pair_id))
    body = append_export(args.output, recorded)
    if body.get("accepted") or body.get("paired") or body.get("auto_prompt_default"):
        raise SystemExit("对照导出不得把 paired/accepted/auto_prompt_default 写成通过")
    print(json.dumps({
        "output": str(args.output),
        "trials": len(body.get("trials") or []),
        "reason_code": body.get("reason_code"),
        "declared_pair_ids": body.get("declared_pair_ids"),
        "accepted": body["accepted"],
        "paired": body["paired"],
        "auto_prompt_default": body["auto_prompt_default"],
        "note": "同一文件追加 manual 与 assisted；哈希不是验收",
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
