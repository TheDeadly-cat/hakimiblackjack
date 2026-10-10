"""Spawned SQLite owner; parent receives IPC off Tk, preserving FIFO input."""
from queue import Empty
import threading


class _InputBridge:
    def __init__(self, queue, stopped):
        self.queue, self.stopped = queue, stopped
        self.bases = {}
        self.last_task = None

    def get(self, timeout):
        from .recording_owner import RecordingTask
        values = self.queue.get(timeout=timeout)
        if values is None:
            self.stopped.set()
            raise Empty
        request_id, chain_id, base, operation, arguments, submitted_at = values
        if base is not None:
            self.bases = {chain_id: base}
        self.last_task = RecordingTask(request_id, chain_id, self.bases[chain_id], operation, arguments, submitted_at)
        return self.last_task

    def empty(self):
        return self.stopped.is_set()

    def task_done(self):
        pass


class _ResultBridge:
    def __init__(self, queue, inputs):
        from .recording_transport import ReceiptPrefixSender
        self.queue, self.inputs = queue, inputs
        self.encoder = ReceiptPrefixSender()

    def put(self, receipt):
        self.queue.put(self.encoder.encode(receipt, self.inputs.last_task))


def run_recording_process(db_path, source, input_queue, result_queue):
    from .recording_owner import RecordingOwner
    owner = RecordingOwner(db_path, source)
    owner.stop_requested = threading.Event()
    owner.queue = _InputBridge(input_queue, owner.stop_requested)
    owner.results = _ResultBridge(result_queue, owner.queue)
    try:
        owner._run()
    finally:
        result_queue.put(None)
