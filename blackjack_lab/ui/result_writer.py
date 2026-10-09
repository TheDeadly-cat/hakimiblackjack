"""Bounded FIFO result saves; accepted records drain on close, never latest-wins."""
from queue import Queue, Empty, Full, SimpleQueue
import threading


class ResultWriter:
    def __init__(self, write, capacity=16):
        self.write = write
        self.queue = Queue(capacity)
        self.results = SimpleQueue()
        self.closed = threading.Event()
        self.lock = threading.Lock()
        self.accepted = set()
        self.thread = None

    def submit(self, record):
        with self.lock:
            if self.closed.is_set() or id(record) in self.accepted:
                return False
            if self.thread is None:
                thread = threading.Thread(target=self._run, name='opening-result-writer', daemon=False)
                thread.start()
                self.thread = thread
            try:
                self.queue.put_nowait(record)
            except Full:
                return False
            self.accepted.add(id(record))
            return True

    def _run(self):
        while not (self.closed.is_set() and self.queue.empty()):
            try:
                record = self.queue.get(timeout=.05)
            except Empty:
                continue
            try:
                saved = self.write(record)
            except Exception as error:
                record['save_error'] = str(error)
                saved = None
            self.results.put((record, saved))
            with self.lock:
                self.accepted.discard(id(record))
            self.queue.task_done()

    def poll(self):
        values = []
        while True:
            try:
                values.append(self.results.get_nowait())
            except Empty:
                return values

    def close(self):
        self.closed.set()

    @property
    def busy(self):
        with self.lock:
            return bool(self.accepted)

    @property
    def stopped(self):
        return self.thread is None or not self.thread.is_alive()
