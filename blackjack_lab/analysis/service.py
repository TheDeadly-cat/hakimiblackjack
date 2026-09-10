"""Request isolation, bounded workers, and content/identity-checked publication."""
import math
import multiprocessing as mp
from time import perf_counter, time
import uuid

from .actions import solve_counts
from .contracts import (AnalysisInput, ENGINE_VERSION, STRATEGY_VERSION, RESULT_SCHEMA,
                        AVAILABLE, INAPPLICABLE, UNSUPPORTED, PENDING, TIMEOUT, CANCELLED, STALE, FAILED, ACTION_ZH)
from .probability import CalculationStopped, InsufficientCards


def base_result(snapshot, request_id):
    return {"schema": RESULT_SCHEMA, "request_id": request_id,
            "input": snapshot.to_dict(), "input_digest": snapshot.input_digest,
            "rules_digest": snapshot.rules_digest, "engine_version": ENGINE_VERSION,
            "strategy_version": STRATEGY_VERSION, "created_at": time(),
            "status": "computing", "reason_code": "COMPUTING", "reason": "计算中",
            "actions": {}, "probabilities": None, "highest_ev_action": None,
            "partial_comparison": False, "elapsed_seconds": 0.0,
            "ev_unit": "相对原始1单位初始注的最终净收益（返还本金不是盈利）"}


def calculate(snapshot, request_id=None, budget_seconds=5.0):
    request_id = request_id or uuid.uuid4().hex
    result = base_result(snapshot, request_id)
    start = perf_counter()
    try:
        snapshot.validate()
        if snapshot.engine_version != ENGINE_VERSION or snapshot.strategy_version != STRATEGY_VERSION:
            raise ValueError("输入引擎/策略版本不匹配，历史结果需按原前缀建立新输入复算")
        if not math.isfinite(budget_seconds) or not 0 < budget_seconds <= 5.0:
            raise ValueError("计算预算必须在0到5秒之间")
        supported = tuple(a for a in snapshot.legal_actions if a != "split")
        numbers = solve_counts(snapshot.counts, snapshot.player, snapshot.dealer_up,
                               snapshot.peek_negative, supported, budget_seconds)
        for action, label in ACTION_ZH.items():
            if action in snapshot.uncertain_actions:
                item = {"status": PENDING, "reason_code": "LEGALITY_UNCERTAIN", "reason": "牌面未细分，动作合法性待核对"}
            elif action not in snapshot.legal_actions:
                item = {"status": INAPPLICABLE, "reason_code": "NOT_LEGAL", "reason": "当前状态无此合法动作"}
            elif action not in numbers["actions"]:
                item = {"status": UNSUPPORTED, "reason_code": "ACTION_NOT_IMPLEMENTED", "reason": "本版尚未计算此合法动作"}
            else:
                value = numbers["actions"][action]
                _validate_distribution(value["net_distribution"])
                item = {"status": AVAILABLE, "reason_code": "CALCULATED", "reason": "已计算", **value}
            result["actions"][action] = {"label": label, **item}
        _validate_distribution(numbers["next_draw"])
        _validate_distribution(numbers["dealer_distribution"])
        if not 0 <= numbers["hit_bust"] <= 1.0000000001:
            raise ArithmeticError("补牌爆牌概率越界")
        partial = bool(snapshot.uncertain_actions) or any(result["actions"][a]["status"] != AVAILABLE for a in snapshot.legal_actions)
        ordered = sorted(((v["ev"], a) for a, v in result["actions"].items() if v["status"] == AVAILABLE), reverse=True)
        highest = None
        if not partial and ordered and (len(ordered) == 1 or ordered[0][0] - ordered[1][0] > 1e-10):
            highest = ordered[0][1]
        result.update(status=AVAILABLE, reason_code="PARTIAL_ACTION_COMPARISON" if partial else "CALCULATED",
                      reason="部分动作比较：合法分牌未计算或合法性待核对，不能给出全局最优" if partial else "计算完成，仅适用于所声明模型与当时信息",
                      partial_comparison=partial, highest_ev_action=highest,
                      all_computed_ev_negative=bool(ordered) and ordered[0][0] < 0,
                      probabilities={"next_target_draw": numbers["next_draw"], "hit_bust": numbers["hit_bust"],
                                     "dealer_terminal_if_stand_now": numbers["dealer_distribution"]},
                      method=numbers["method"], approximation=numbers["approximation"],
                      numerical_tolerance=1e-10, nodes=numbers["nodes"])
        draw_status = AVAILABLE if any(a in snapshot.legal_actions for a in ("hit", "double")) else INAPPLICABLE
        result["probability_status"] = {"next_target_draw": draw_status, "hit_bust": draw_status,
                                        "dealer_terminal_if_stand_now": AVAILABLE}
    except CalculationStopped as error:
        result.update(status=TIMEOUT, reason_code=str(error), reason="预算到期，当前请求未完成；未使用旧结果")
    except InsufficientCards as error:
        result.update(status=UNSUPPORTED, reason_code="INSUFFICIENT_CARDS", reason=str(error))
    except Exception as error:
        result.update(status=FAILED, reason_code="CALCULATION_FAILED", reason=str(error))
    if result["status"] != AVAILABLE:
        result.update(actions={}, probabilities=None, highest_ev_action=None)
    result["elapsed_seconds"] = perf_counter() - start
    return result


def _validate_distribution(values):
    if any(not math.isfinite(p) or p < -1e-12 for p in values.values()) or abs(sum(values.values()) - 1) > 1e-10:
        raise ArithmeticError("计算结果分布不合法，禁止发布")


def _worker(connection, data, request_id, budget):
    try:
        result = calculate(AnalysisInput.from_dict(data), request_id, budget)
        import socket
        result["worker_network_guard_active"] = bool(getattr(socket, "_hakimi_offline_guard", False))
        result["worker_peak_working_set_bytes"] = _peak_memory()
        connection.send(result)
    finally:
        connection.close()


def _peak_memory():
    import os
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in ("peak", "working", "quota_peak_paged", "quota_paged",
                "quota_peak_nonpaged", "quota_nonpaged", "pagefile", "peak_pagefile")]
    data = Counters()
    data.cb = ctypes.sizeof(data)
    get_process = ctypes.windll.kernel32.GetCurrentProcess
    get_process.restype = wintypes.HANDLE
    query = ctypes.windll.psapi.GetProcessMemoryInfo
    query.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    return data.peak if query(get_process(), ctypes.byref(data), data.cb) else None


class AnalysisService:
    """One replaceable process per request: no Tk blocking, no stale worker output."""
    def __init__(self):
        self.active = None
        self.result = None

    def start(self, snapshot, budget_seconds=5.0):
        snapshot.validate()
        if not math.isfinite(budget_seconds) or not 0 < budget_seconds <= 5:
            raise ValueError("计算预算必须大于0且不超过5秒")
        self.cancel(STALE)
        request_id = uuid.uuid4().hex
        receive, send = mp.get_context("spawn").Pipe(duplex=False)
        process = mp.get_context("spawn").Process(target=_worker,
            args=(send, snapshot.to_dict(), request_id, budget_seconds), daemon=True)
        self.active = {"id": request_id, "snapshot": snapshot, "start": perf_counter(),
                       "budget": budget_seconds, "process": process, "pipe": receive}
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

    def accept_result(self, result):
        job = self.active
        if (not job or result.get("request_id") != job["id"]
                or result.get("input_digest") != job["snapshot"].input_digest):
            return False
        self.result = result
        return True

    def poll(self):
        job = self.active
        if job is None:
            return None
        if perf_counter() - job["start"] >= job["budget"]:
            result = base_result(job["snapshot"], job["id"])
            result.update(status=TIMEOUT, reason_code="WALL_TIME_BUDGET", reason=f"{job['budget']:g}秒内未完成请求（含进程启动）；可继续录入或重试",
                          elapsed_seconds=perf_counter() - job["start"])
            self.result = result
            self._release()
            return result
        if job["pipe"].poll():
            try:
                result = job["pipe"].recv()
            except EOFError:
                result = base_result(job["snapshot"], job["id"])
                result.update(status=FAILED, reason_code="WORKER_EXITED", reason="计算进程未返回结果")
            accepted = self.accept_result(result)
            self._release()
            return result if accepted else None
        if not job["process"].is_alive():
            result = base_result(job["snapshot"], job["id"])
            result.update(status=FAILED, reason_code="WORKER_EXITED", reason="计算进程已退出，当前请求没有结果")
            self.result = result
            self._release()
            return result
        return None

    def _release(self):
        if self.active:
            process = self.active["process"]
            if process.is_alive():
                process.terminate()
            process.join(timeout=0.25)
            self.active["pipe"].close()
            if not process.is_alive():
                process.close()
            self.active = None

    def cancel(self, status=CANCELLED):
        if self.active:
            result = base_result(self.active["snapshot"], self.active["id"])
            result.update(status=status, reason_code=status.upper(), reason="输入已变化，旧结果过期" if status == STALE else "用户取消计算")
            self.result = result
        self._release()

    def close(self):
        self.cancel()
