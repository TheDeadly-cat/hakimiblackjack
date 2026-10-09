"""Spawned SQLite owner; parent receives IPC off Tk, preserving FIFO input."""
from queue import Empty
import threading


class _InputBridge:
    def __init__(self, queue, stopped):
        self.queue, self.stopped = queue, stopped
        self.bases = {}

    def get(self, timeout):
        from .recording_owner import RecordingTask
        values = self.queue.get(timeout=timeout)
        if values is None:
            self.stopped.set()
            raise Empty
        request_id, chain_id, base, operation, arguments, submitted_at = values
        if base is not None:
            self.bases = {chain_id: base}
        return RecordingTask(request_id, chain_id, self.bases[chain_id], operation, arguments, submitted_at)

    def empty(self):
        return self.stopped.is_set()

    def task_done(self):
        pass


def run_recording_process(db_path, source, input_queue, result_queue):
    from .recording_owner import RecordingOwner
    owner = RecordingOwner(db_path, source)
    owner.stop_requested = threading.Event()
    owner.queue = _InputBridge(input_queue, owner.stop_requested)
    owner.results = result_queue
    try:
        owner._run()
    finally:
        result_queue.put(None)
