"""Run a synthetic 6/7/8-deck contrast or a frozen history-prefix replay."""
import argparse
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blackjack_lab.experiments.contracts import KIND_HISTORY, KIND_SYNTHETIC, ExperimentError
from blackjack_lab.experiments.runner import ExperimentRunner
from blackjack_lab.experiments.scenarios import config_from_mapping
from blackjack_lab.storage.safe_files import atomic_write


DEFAULTS = dict(kind=KIND_SYNTHETIC, n_decks='6,7,8', template='single',
                player_ranks='10,6', dealer_up='10', extra_removed='', seat='玩家1')
ALIASES = dict(decks='n_decks', player='player_ranks', up='dealer_up', removed='extra_removed')
FIELDS = set(DEFAULTS) | {'peek_negative', 'db_path', 'session_id', 'through_seq', 'note'}
CLI_FIELDS = dict(decks='n_decks', template='template', player='player_ranks', up='dealer_up',
                  removed='extra_removed', seat='seat', db='db_path', session='session_id',
                  through_seq='through_seq')


def merge_config(file_data, explicit):
    """Only present CLI options override JSON; aliases never refill an empty override."""
    if type(file_data) is not dict:
        raise ExperimentError('ILLEGAL_CONFIG', '配置JSON根必须为对象')
    unknown = set(file_data) - FIELDS - set(ALIASES)
    if unknown:
        raise ExperimentError('UNKNOWN_FIELD', '未知配置字段：' + ', '.join(sorted(unknown)))
    normalized = dict(file_data)
    for alias, field in ALIASES.items():
        if alias in normalized:
            if field in normalized:
                raise ExperimentError('DUPLICATE_FIELD', f'不要同时提供 {field} 和别名 {alias}')
            normalized[field] = normalized.pop(alias)
    data = {**DEFAULTS, **normalized, **explicit}
    # Reject containers/bools that previous string coercion could mistake for conditions.
    for field in ('kind', 'template', 'seat', 'note'):
        if field in data and type(data[field]) is not str:
            raise ExperimentError('ILLEGAL_TYPE', f'{field} 必须为字符串')
    for field in ('session_id', 'db_path'):
        if data.get(field) is not None and type(data[field]) is not str:
            raise ExperimentError('ILLEGAL_TYPE', f'{field} 必须为字符串或null')
    for field in ('player_ranks', 'extra_removed'):
        value = data[field]
        if (type(value) not in (str, list, tuple)
                or isinstance(value, (list, tuple)) and any(type(v) not in (str, int) for v in value)):
            raise ExperimentError('ILLEGAL_TYPE', f'{field} 必须为牌面字符串或牌面数组')
    if type(data['dealer_up']) not in (str, int):
        raise ExperimentError('ILLEGAL_TYPE', 'dealer_up 必须为牌面字符串或整数')
    if not data['seat'].strip():
        raise ExperimentError('ILLEGAL_SEAT', 'seat 不能为空')
    return data


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("synthetic", "history"), default=argparse.SUPPRESS)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--decks", default=argparse.SUPPRESS)
    parser.add_argument("--template", choices=("single", "split", "das"), default=argparse.SUPPRESS)
    parser.add_argument("--player", default=argparse.SUPPRESS)
    parser.add_argument("--up", default=argparse.SUPPRESS)
    parser.add_argument("--removed", default=argparse.SUPPRESS, help='显式空字符串清除配置中的额外移除')
    parser.add_argument("--seat", default=argparse.SUPPRESS)
    parser.add_argument("--db", default=argparse.SUPPRESS)
    parser.add_argument("--session", default=argparse.SUPPRESS)
    parser.add_argument("--through-seq", type=int, default=argparse.SUPPRESS)
    parser.add_argument("--config", type=Path, help="JSON 配置；命令行选项覆盖其中的字段")
    args = parser.parse_args(argv)
    explicit = {field: getattr(args, flag) for flag, field in CLI_FIELDS.items() if hasattr(args, flag)}
    if hasattr(args, 'mode'):
        explicit['kind'] = KIND_HISTORY if args.mode == 'history' else KIND_SYNTHETIC
    try:
        file_data = json.loads(args.config.read_text(encoding='utf-8-sig')) if args.config else {}
        config = config_from_mapping(merge_config(file_data, explicit), uuid.uuid4().hex)
    except (ExperimentError, OSError, UnicodeError, json.JSONDecodeError) as error:
        parser.error(str(error))
    saved = ExperimentRunner().run(config, args.output)
    effective = {key: value for key, value in config.to_dict().items() if key in FIELDS}
    effective_path = args.output / 'effective_config.json'
    atomic_write(effective_path, json.dumps(effective, ensure_ascii=False, indent=2,
                                          allow_nan=False).encode('utf-8'), overwrite=False)
    print(json.dumps({"json": str(saved["json"]), "csv": str(saved["csv"]),
                      "experiment_id": config.experiment_id,
                      "effective_config": effective, "effective_config_path": str(effective_path),
                      "items": len(saved["record"]["items"])}, ensure_ascii=False), flush=True)
    failed = [item for item in saved["record"]["items"] if item.get("status") not in ("available",)]
    return 0 if saved["record"]["items"] and not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
