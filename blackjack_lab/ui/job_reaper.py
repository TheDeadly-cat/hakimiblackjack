"""Release only this application's detached analysis jobs off the Tk thread."""
from queue import Queue, Empty
import threading


class JobReaper:
    def __init__(self):
        self.queue = Queue()
        self.closed = threading.Event()
        self.thread = threading.Thread(target=self._run, name='opening-process-reaper', daemon=False)
        self.thread.start()

    def submit(self, job):
        if self.closed.is_set():
            raise RuntimeError('计算进程回收服务已关闭')
        # Signal cancellation immediately; joining/releasing stays off Tk.
        if job['process'].is_alive():
            job['process'].terminate()
        self.queue.put_nowait(job)

    def _run(self):
        while not (self.closed.is_set() and self.queue.empty()):
            try:
                job = self.queue.get(timeout=.05)
            except Empty:
                continue
            process = job['process']
            process.join(timeout=.25)
            while process.is_alive():
                process.kill(); process.join(timeout=.25)
            job['pipe'].close(); process.close()
            self.queue.task_done()

    def close(self):
        self.closed.set()

    @property
    def stopped(self):
        return not self.thread.is_alive()
