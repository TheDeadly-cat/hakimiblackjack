"""One background SQLite owner for a FIFO batch of explicit local recording commands.

No Tk APIs, dropped input, automatic retries or latest-request replacement.
The UI integration must publish receipts on its own thread and block unrelated
mutations while a batch has outstanding receipts.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
from queue import Empty, Queue, Full, SimpleQueue
import threading
from time import perf_counter, time
import uuid

from .controller import SessionController
from .read_snapshot import PrefixSnapshot

OPERATIONS = frozenset(('deal_shown', 'deal_hidden', 'reveal', 'player_action',
                        'peek_negative', 'undo_last', 'record_input'))


@dataclass(frozen=True)
class RecordingTask:
    request_id: str
    chain_id: str
    base: PrefixSnapshot
    operation: str
    arguments_json: str
    submitted_at: float


@dataclass(frozen=True)
class RecordingReceipt:
    request_id: str
    chain_id: str
    status: str
    before: PrefixSnapshot
    after: PrefixSnapshot
    ledger: object
    state: object
    entry_plan: object
    event_ids: tuple
    error: str | None = None
    outcome: object = None
    entry_warning: str = ''
    failure_path: str | None = None
    archive_error: str | None = None
    submitted_at: float | None = None
    started_at: float | None = None
    finished_at: float | None = None
    verified_token: object = None


class RecordingOwner:
    def __init__(self, db_path, recording_source, *, capacity=64, use_process=False):
        if type(capacity) is not int or not 1 <= capacity <= 256:
            raise ValueError('录牌队列容量必须为1–256')
        self.db_path, self.recording_source = db_path, recording_source
        self.queue = Queue(capacity)
        self.results = SimpleQueue()
        self.stop_requested = threading.Event()
        self.lock = threading.Lock()
        self.thread = None
        self.outstanding = 0
        self.chain_id = None
        self.chain_base = None
        self.seen = set()
        self.failed = False
        self.use_process = use_process
        self.bridge = None
        self.process_queue = self.process_results = None
        self.process_chain = None
        self.pending_tasks = {}
        self.stop_sender = None
        self.verification_token = object()

    def submit(self, base, operation, *args, request_id=None, **kwargs):
        if operation not in OPERATIONS or not isinstance(base, PrefixSnapshot):
            raise ValueError('后台录牌请求类型或基线无效')
        payload = json.dumps({'args': args, 'kwargs': kwargs}, ensure_ascii=False, allow_nan=False)
        request_id = request_id or uuid.uuid4().hex
        if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
            raise ValueError('录牌请求身份必须为有效文本')
        with self.lock:
            if self.stop_requested.is_set():
                raise RuntimeError('后台录牌服务正在关闭')
            if self.failed:
                raise RuntimeError('录牌请求失败；需核对已保存记录后重新建立服务，不能自动重试')
            if request_id in self.seen:
                raise ValueError('同一录牌请求不能重复入队')
            if self.outstanding >= self.queue.maxsize:
                raise RuntimeError('录牌队列已满，该输入未接收，请等待保存完成')
            if self.outstanding == 0:
                self.chain_id, self.chain_base = uuid.uuid4().hex, base
            elif (base.session_id, base.content) != (self.chain_base.session_id, self.chain_base.content):
                raise ValueError('等待保存的批次仍绑定原基线，不能混入另一上下文')
            task = RecordingTask(request_id, self.chain_id, self.chain_base, operation, payload, perf_counter())
            if self.thread is None:
                try:
                    self._start()
                except Exception:
                    raise  # No request has been accepted or placed in the queue.
            elif not self.thread.is_alive():
                raise RuntimeError('录牌服务已结束，该输入未接收；请核对后重新建立服务')
            try:
                if self.use_process:
                    self.process_queue.put_nowait((task.request_id, task.chain_id,
                        task.base if self.process_chain != task.chain_id else None,
                        task.operation, task.arguments_json, task.submitted_at))
                    self.process_chain = task.chain_id
                else:
                    self.queue.put_nowait(task)
            except Full as error:
                raise RuntimeError('录牌队列已满，该输入未接收，请等待保存完成') from error
            self.seen.add(request_id)
            if self.outstanding == 0:
                from .recording_scheduling import acquire
                acquire(self)
            self.outstanding += 1
            self.pending_tasks[request_id] = task
        return request_id

    def _start(self):
        if not self.use_process:
            self.thread = threading.Thread(target=self._run, name='blackjack-recording-owner', daemon=False)
            self.thread.start()
            return
        import multiprocessing as mp
        from .recording_process import run_recording_process
        context = mp.get_context('spawn')
        self.process_queue = context.Queue(self.queue.maxsize)
        self.process_results = context.Queue()
        self.thread = context.Process(target=run_recording_process,
            args=(self.db_path, self.recording_source, self.process_queue, self.process_results),
            name='blackjack-recording-owner', daemon=False)
        self.thread.start()
        self.bridge = threading.Thread(target=self._receive_process,
                                       name='blackjack-recording-receipts', daemon=False)
        try:
            self.bridge.start()
        except Exception:
            # No task has been accepted. Reap only this unused child, which
            # has not opened a recording session or performed a command.
            self.stop_requested.set()
            self.thread.terminate()
            raise

    def _receive_process(self):
        from .recording_transport import PrefixDeltaReceipt, ReceiptPrefixReceiver
        decoder = ReceiptPrefixReceiver()
        delivered = set()
        while True:
            try:
                receipt = self.process_results.get(timeout=.1)
            except Empty:
                if self.thread.is_alive():
                    continue
                self._unreceived_inputs(delivered)
                return
            if receipt is None:
                self._unreceived_inputs(delivered)
                return
            packet = receipt
            if isinstance(packet, PrefixDeltaReceipt):
                receipt = packet.receipt
            if not isinstance(receipt, RecordingReceipt):
                self._unreceived_inputs(delivered)
                return
            with self.lock:
                task = self.pending_tasks.get(receipt.request_id)
            if receipt.status == 'committed' or isinstance(packet, PrefixDeltaReceipt):
                # Verify complete transported content off Tk. The opaque token
                # is local to this parent owner and never crosses the pipe.
                try:
                    receipt = decoder.decode(packet, task)
                    if PrefixSnapshot.capture(receipt.ledger) != receipt.after:
                        raise ValueError('进程回执与完整账本内容不符')
                    decoder.accepted(receipt, task)
                    receipt = replace(receipt, verified_token=self.verification_token)
                except Exception as error:
                    if receipt.before is None or receipt.after is None:
                        # Reconstruction failed. Retain the input and the last
                        # independently known prefix; do not invent a new commit.
                        fallback = decoder.reference(task) if task else None
                        receipt = replace(receipt, before=fallback, after=fallback,
                            ledger=None, state=None)
                    path, archive_error = (self._archive_failure(task, 'unknown_commit_outcome',
                        receipt.before, receipt.after, str(error)) if task else (None, str(error)))
                    receipt = replace(receipt, status='unknown_commit_outcome', error=str(error),
                                      failure_path=path, archive_error=archive_error)
            if receipt.status != 'committed':
                decoder.accepted(receipt, task)
            delivered.add(receipt.request_id)
            if receipt.status != 'committed':
                with self.lock:
                    self.failed = True
            self.results.put(receipt)

    def _unreceived_inputs(self, delivered):
        with self.lock:
            missing = [task for key, task in self.pending_tasks.items() if key not in delivered]
            if missing:
                self.failed = True
        for task in missing:
            error = '录牌进程结束但缺少保存回执，结果未知；需读取数据库核对，不能重复录入'
            path, archive_error = self._archive_failure(task, 'unknown_commit_outcome', task.base, task.base, error)
            self.results.put(RecordingReceipt(task.request_id, task.chain_id, 'unknown_commit_outcome',
                task.base, task.base, None, None, None, (), error, failure_path=path,
                archive_error=archive_error, submitted_at=task.submitted_at))

    def _run(self):
        controller = None
        chain = None
        chain_failed = False
        try:
            while not (self.stop_requested.is_set() and self.queue.empty()):
                try:
                    task = self.queue.get(timeout=.05)
                except Empty:
                    continue
                before = task.base
                started_at = perf_counter()
                operation_started = False
                try:
                    new_chain = task.chain_id != chain
                    if new_chain:
                        if controller:
                            controller.close()
                        controller = SessionController.recover(self.db_path, task.base.session_id)
                        controller.recording_source = self.recording_source
                    with controller.read_frame():
                        # Share the owned, complete before-prefix only within
                        # this command. Independent replay and SQLite's full
                        # baseline comparison remain on their existing paths.
                        before = controller.read_prefix()
                        if new_chain:
                            if before.content != task.base.content:
                                raise ValueError('原数据库与录牌批次完整基线不符，未执行请求')
                            chain, chain_failed = task.chain_id, False
                        if chain_failed:
                            raise RuntimeError('前一请求失败；后续输入保留为未执行，需人工核对')
                        data = json.loads(task.arguments_json)
                        old_count = len(controller.ledger.events)
                        operation_started = True
                        if task.operation == 'record_input':
                            from .recording_commands import record_input
                            outcome = record_input(controller, *data['args'], **data['kwargs'])
                        else:
                            getattr(controller, task.operation)(*data['args'], **data['kwargs'])
                            outcome = None
                        state = controller.state()
                        after = controller.read_prefix()
                        receipt = RecordingReceipt(task.request_id, task.chain_id, 'committed', before, after,
                                                   controller.ledger, state, copy.deepcopy(controller.entry_plan),
                                                   tuple(e.event_id for e in controller.ledger.events[old_count:]),
                                                   outcome=outcome, entry_warning=getattr(controller, 'entry_warning', ''),
                                                   submitted_at=task.submitted_at, started_at=started_at, finished_at=perf_counter())
                except Exception as error:
                    chain_failed = True
                    with self.lock:
                        self.failed = True
                    # An exception alone cannot prove rollback. Preserve UNKNOWN
                    # if durable history changed or a readback is unavailable.
                    status = 'failed_before_commit' if operation_started else 'not_executed'
                    after, actual, state = before, None, None
                    try:
                        if controller is not None:
                            actual = controller.store.load_ledger(task.base.session_id)
                            state = actual.replay()
                            after = PrefixSnapshot.capture(actual)
                            if operation_started and after.content != before.content:
                                status = 'unknown_commit_outcome'
                        else:
                            status = 'unknown_commit_outcome' if operation_started else 'not_executed'
                    except Exception:
                        status = 'unknown_commit_outcome' if operation_started else 'not_executed'
                    failure_path, archive_error = self._archive_failure(task, status, before, after, str(error))
                    receipt = RecordingReceipt(task.request_id, task.chain_id, status, before, after,
                                               actual, state, None, (), str(error), failure_path=failure_path,
                                               archive_error=archive_error, submitted_at=task.submitted_at,
                                               started_at=started_at, finished_at=perf_counter())
                self.results.put(receipt)
                self.queue.task_done()
        finally:
            if controller:
                controller.close()

    def _archive_failure(self, task, status, before, after, error):
        """Retain the raw unexecuted input locally; never replay it automatically."""
        from ..storage.safe_files import atomic_write
        path = Path(str(Path(self.db_path).resolve()) + '.recording-failures') / (
            hashlib.sha256((task.chain_id + task.request_id).encode()).hexdigest() + '.json')
        data = dict(schema='blackjack-recording-failure-v1', session_id=task.base.session_id,
                    request_id=task.request_id, chain_id=task.chain_id, operation=task.operation,
                    arguments=json.loads(task.arguments_json), status=status, error=error,
                    before_seq=before.through_seq, before_sha256=before.prefix_digest,
                    after_seq=after.through_seq, after_sha256=after.prefix_digest,
                    submitted_at=task.submitted_at, archived_at=time(), automatic_retry=False)
        try:
            atomic_write(path, json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2).encode('utf-8'),
                         overwrite=False)
            return str(path), None
        except Exception as archive_error:
            return None, str(archive_error)

    def poll(self):
        output = []
        while True:
            try:
                receipt = self.results.get_nowait()
            except Empty:
                break
            output.append(receipt)
            with self.lock:
                self.pending_tasks.pop(receipt.request_id, None)
                self.outstanding -= 1
                if self.outstanding == 0:
                    from .recording_scheduling import release
                    release(self)
        return output

    @property
    def pending_count(self):
        with self.lock:
            return self.outstanding

    def close(self):
        """Cooperatively drain accepted input; never block a Tk caller on join."""
        if not self.stop_requested.is_set():
            self.stop_requested.set()
            if self.use_process and self.thread is not None:
                # The ordered sentinel drains every accepted task. Queue.empty
                # is never used as proof that interprocess input has arrived.
                def send_stop():
                    while self.thread.is_alive():
                        try:
                            self.process_queue.put(None, timeout=.1)
                            return
                        except Full:
                            continue
                self.stop_sender = threading.Thread(target=send_stop, name='recording-drain-signal', daemon=False)
                self.stop_sender.start()

    @property
    def stopped(self):
        return ((self.thread is None or not self.thread.is_alive())
                and (self.bridge is None or not self.bridge.is_alive())
                and (self.stop_sender is None or not self.stop_sender.is_alive()))

    def release_resources(self):
        if not self.stopped or self.pending_count:
            raise RuntimeError('录牌服务仍有未完成输入或回执')
        if self.use_process and self.thread is not None:
            if self.thread.pid is not None:
                self.thread.join(timeout=0)
            self.thread.close()
            self.thread = None
        if self.use_process:
            for queue in (self.process_queue, self.process_results):
                if queue is not None:
                    queue.cancel_join_thread(); queue.close()
