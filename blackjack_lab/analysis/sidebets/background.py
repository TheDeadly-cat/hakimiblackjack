"""One worker and at most one waiting request per named UI channel."""
from collections import OrderedDict
from queue import SimpleQueue, Empty
import threading
import uuid


class LatestWorker:
    def __init__(self):
        self.condition=threading.Condition()
        self.pending=OrderedDict()
        self.completed=SimpleQueue()
        self.closed=threading.Event()
        self.active=None
        self.active_channel=None
        self.thread=None

    def submit(self,channel,function,request_id=None):
        if channel not in ('forecast','observed','history','history_load','history_verify','history_recompute','retry'):
            raise ValueError('未知后台通道')
        request_id=request_id or uuid.uuid4().hex
        with self.condition:
            if self.closed.is_set():raise RuntimeError('后台服务已关闭')
            if channel=='history_recompute' and channel in self.pending:
                raise RuntimeError('已有明确的历史复算请求等待执行')
            replaced=self.pending.get(channel)
            if replaced and channel.startswith('history_'):
                self.completed.put(dict(request_id=replaced[0],channel=channel,cancelled=True,
                                        error='等待中的显示请求已被较新请求替代'))
            self.pending[channel]=(request_id,function)
            if self.thread is None:
                self.thread=threading.Thread(target=self._run,daemon=True,name='sidebet-worker')
                self.thread.start()
            self.condition.notify()
        return request_id

    def _run(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda:self.pending or self.closed.is_set())
                if self.closed.is_set():return
                channel,(request_id,function)=self.pending.popitem(last=False)
                self.active=request_id
                self.active_channel=channel
            try:value=dict(request_id=request_id,channel=channel,result=function())
            except Exception as error:value=dict(request_id=request_id,channel=channel,error=str(error))
            finally:self.active=None;self.active_channel=None
            if not self.closed.is_set():self.completed.put(value)

    def poll(self):
        values=[]
        while True:
            try:values.append(self.completed.get_nowait())
            except Empty:return values

    def close(self):
        """Request cooperative stop; a Tk caller must poll stopped, never join."""
        self.closed.set()
        with self.condition:
            self.pending.clear();self.condition.notify()

    @property
    def stopped(self):
        return self.thread is None or not self.thread.is_alive()
