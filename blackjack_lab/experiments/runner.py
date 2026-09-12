"""Run declared experiments sequentially on the existing analysis service."""
import threading
import uuid
from time import time

from ..analysis.service import calculate
from .contracts import KIND_HISTORY, KIND_SYNTHETIC, ExperimentError
from .scenarios import snapshot_for_deck, snapshot_from_history
from .results import experiment_record, write_experiment


class ExperimentRunner:
    def __init__(self):
        self._cancel = threading.Event()
        self.progress = []

    def cancel(self):
        self._cancel.set()

    def run(self, config, output_dir, budget_seconds=5.0):
        self._cancel.clear()
        self.progress = []
        started = time()
        items = []
        if config.kind == KIND_HISTORY:
            try:
                snapshot, ledger, later = snapshot_from_history(config)
                item = self._calculate(config, snapshot, ledger, later=later, budget_seconds=budget_seconds)
            except ExperimentError as error:
                item = self._failed(config, None, error)
            items.append(item)
            self.progress.append(item)
        elif config.kind == KIND_SYNTHETIC:
            for n_decks in config.n_decks:
                if self._cancel.is_set():
                    items.append(self._cancelled(config, n_decks))
                    continue
                try:
                    snapshot, ledger = snapshot_for_deck(config, n_decks)
                    item = self._calculate(config, snapshot, ledger, n_decks=n_decks,
                                           budget_seconds=budget_seconds)
                except ExperimentError as error:
                    item = self._failed(config, n_decks, error)
                items.append(item)
                self.progress.append(item)
        else:
            raise ExperimentError("ILLEGAL_KIND", "输入类型必须是合成场景或历史前缀回放")
        record = experiment_record(config, items, elapsed=time() - started)
        return write_experiment(output_dir, record)

    def _calculate(self, config, snapshot, ledger, *, n_decks=None, later=None, budget_seconds=5.0):
        if self._cancel.is_set():
            return self._cancelled(config, n_decks or snapshot.n_decks)
        result = calculate(snapshot, request_id=uuid.uuid4().hex, budget_seconds=budget_seconds)
        return {
            "n_decks": n_decks or snapshot.n_decks,
            "status": result.get("status"),
            "reason": result.get("reason"),
            "reason_code": result.get("reason_code"),
            "engine_version": result.get("engine_version"),
            "strategy_version": result.get("strategy_version"),
            "input_digest": result.get("input_digest"),
            "rules_digest": result.get("rules_digest"),
            "through_seq": snapshot.through_seq,
            "prefix_digest": snapshot.prefix_digest,
            "session_id": snapshot.session_id,
            "counts": list(snapshot.counts),
            "physical_remaining": snapshot.physical_remaining,
            "player_ranks": list(getattr(snapshot, "player_ranks", ()) or (
                snapshot.hands[0].ranks if getattr(snapshot, "hands", None) else ())),
            "dealer_up": snapshot.dealer_up,
            "legal_actions": list(snapshot.legal_actions),
            "later_event_count_ignored": 0 if later is None else len(later),
            "highest_ev_action": result.get("highest_ev_action"),
            "partial_comparison": result.get("partial_comparison"),
            "elapsed_seconds": result.get("elapsed_seconds"),
            "actions": result.get("actions") or {},
            "probabilities": result.get("probabilities"),
            "not_a_round_simulation": True,
        }

    def _failed(self, config, n_decks, error):
        return {
            "n_decks": n_decks,
            "status": "failed",
            "reason": error.reason,
            "reason_code": error.code,
            "actions": {},
            "not_a_round_simulation": True,
        }

    def _cancelled(self, config, n_decks):
        return {
            "n_decks": n_decks,
            "status": "cancelled",
            "reason": "实验已取消；已完成项保留",
            "reason_code": "CANCELLED",
            "actions": {},
            "not_a_round_simulation": True,
        }
