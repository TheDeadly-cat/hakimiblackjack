"""Cancellable opening simulations in their own worker and owned native job."""
import hashlib
import json
import math
import multiprocessing as mp
import subprocess
from time import perf_counter, time
import uuid

from .contracts import STALE, canonical
from .native_backend import build_native, _run_owned_process, source_digest
from .opening import (ENGINE, STRATEGY, SCHEMA, SAMPLES, NOTE, OpeningInput,
                      summarize_histogram, validate_counts)
from .service import AnalysisService


def estimate_counts(counts, *, seats=1, focal=0, das=True, surrender=True,
                    peek_ten=True, both_initial=False, samples=SAMPLES, seed=20260923, budget_seconds=18):
    validate_counts(counts, seats, focal)
    if (type(samples) is not int or not 2 <= samples <= 4_000_000 or type(seed) is not int or not 0 <= seed <= 0x7fffffff
            or any(type(v) is not bool for v in (das, surrender, peek_ten, both_initial)) or type(budget_seconds) not in (int, float)
            or not math.isfinite(budget_seconds) or not 0 < budget_seconds <= 30):
        raise ValueError('无效的开局模拟参数')
    started = perf_counter()
    executable = build_native(timeout=budget_seconds)
    remaining = budget_seconds - (perf_counter() - started)
    if remaining <= 0:
        raise TimeoutError('TIMEOUT')
    payload = dict(kind='opening-monte-carlo-v3', counts=counts, seats=seats, focal=focal,
                   das=das, surrender=surrender, peek_ten=peek_ten, both_initial=both_initial,
                   samples=samples, seed=seed, budget_seconds=remaining)
    code, stdout, stderr = _run_owned_process([str(executable)], remaining, json.dumps(payload))
    result = json.loads(stdout)
    if code or result.get('status') != 'available':
        raise RuntimeError(result.get('error', stderr or '开局模拟失败'))
    if (result.get('kind') != payload['kind'] or result.get('samples') != samples or result.get('seed') != seed):
        raise ValueError('开局模拟返回了错误的样本身份')
    numbers = summarize_histogram(result['histogram'], samples)
    return dict(**numbers, histogram=result['histogram'], samples=samples, seed=seed,
                native_source_digest=source_digest(), native_binary_digest=hashlib.sha256(executable.read_bytes()).hexdigest(),
                elapsed_seconds=perf_counter() - started)


def calculate_opening(snapshot, request_id, samples=SAMPLES, budget_seconds=18):
    snapshot.validate()
    result = dict(schema=SCHEMA, status='computing', request_id=request_id, input=snapshot.to_dict(),
                  input_digest=snapshot.input_digest, rules_digest=snapshot.rules_digest,
                  engine_version=ENGINE, strategy_version=STRATEGY, created_at=time(), note=NOTE)
    try:
        rules = json.loads(snapshot.rules_json)
        numbers = estimate_counts(snapshot.counts, seats=len(snapshot.participants), focal=snapshot.focal,
                                  das=rules['double_after_split'], surrender=rules['surrender'] == 'late',
                                  peek_ten=rules['check_bj_when'] == 'before_player_actions_A_T',
                                  both_initial=rules['split_deal_order'] == 'both_second_cards_first',
                                  samples=samples, seed=snapshot.seed, budget_seconds=budget_seconds)
        result.update(status='available', **numbers)
    except (TimeoutError, subprocess.TimeoutExpired):
        result.update(status='timeout', reason='计算超时，未使用旧结果')
    except Exception as error:
        reason = str(error)
        result.update(status='failed', reason='剩余牌不足以完成模拟' if 'INSUFFICIENT_CARDS' in reason else
                      '计算超时，未使用旧结果' if 'TIMEOUT' in reason else '开局计算失败：' + reason)
    return result


def validate_opening_result(result, snapshot):
    if (not isinstance(result, dict) or result.get('schema') != SCHEMA or result.get('status') != 'available'
            or result.get('input_digest') != snapshot.input_digest or result.get('rules_digest') != snapshot.rules_digest
            or result.get('engine_version') != ENGINE or result.get('strategy_version') != STRATEGY
            or result.get('seed') != snapshot.seed or result.get('samples') != SAMPLES
            or canonical(result.get('input')) != canonical(snapshot.to_dict())
            or result.get('native_source_digest') != source_digest()):
        raise ValueError('开局结果与当前牌盒身份不符')
    derived = summarize_histogram(result['histogram'], result['samples'])
    for key, expected in derived.items():
        actual = result.get(key)
        if actual != expected:
            raise ValueError('开局结果数值或误差范围不一致：' + key)
    return result


def _opening_worker(connection, data, request_id, samples, budget):
    try:
        data['counts'] = tuple(data['counts'])
        data['participants'] = tuple(data['participants'])
        snapshot = OpeningInput(**data)
        connection.send(calculate_opening(snapshot, request_id, samples, budget))
    finally:
        connection.close()


class OpeningService(AnalysisService):
    """Reuse the established pipe, cancellation, timeout, and process release logic."""
    def start(self, snapshot, budget_seconds=20.0):
        snapshot.validate()
        if not math.isfinite(budget_seconds) or not 2 < budget_seconds <= 30:
            raise ValueError('开局请求预算必须大于2秒且不超过30秒')
        self.cancel(STALE)
        request_id = uuid.uuid4().hex
        context = mp.get_context('spawn')
        receive, send = context.Pipe(duplex=False)
        process = context.Process(target=_opening_worker,
                                  args=(send, snapshot.to_dict(), request_id, SAMPLES, budget_seconds - 2), daemon=True)
        self.active = dict(id=request_id, snapshot=snapshot, start=perf_counter(), budget=budget_seconds,
                           process=process, pipe=receive)
        self.result = None
        try:
            process.start()
        except Exception:
            self.active = None
            receive.close()
            raise
        finally:
            send.close()
        return request_id
