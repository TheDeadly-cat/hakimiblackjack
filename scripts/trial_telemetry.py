"""Acceptance instrumentation only; nested wall samples are not additive CPU time."""
from collections import defaultdict
from contextlib import ExitStack
import threading
from time import perf_counter
from unittest.mock import patch


def quantiles(values):
    import math
    values=sorted(values)
    if not values:return dict(count=0)
    return dict(count=len(values),p50=values[math.ceil(.50*len(values))-1],
                p95=values[math.ceil(.95*len(values))-1],p99=values[math.ceil(.99*len(values))-1],maximum=values[-1])


class TrialTelemetry:
    def __init__(self):
        self.samples=defaultdict(list);self.stack=ExitStack();self.main=threading.get_ident()
        self.submitted=0;self.executed=0;self.max_pending=0;self.max_threads=0

    def wrap(self,owner,name,label):
        original=getattr(owner,name)
        def measured(*args,**kwargs):
            start=perf_counter()
            try:return original(*args,**kwargs)
            finally:self.samples[label+('.main' if threading.get_ident()==self.main else '.worker')].append(perf_counter()-start)
        self.stack.enter_context(patch.object(owner,name,measured))

    def install(self):
        from blackjack_lab.analysis.sidebets.background import LatestWorker
        from blackjack_lab.analysis.sidebets import information
        from blackjack_lab.ui import sidebet_view
        from blackjack_lab.storage.sidebet_snapshots import SidebetSnapshots
        from blackjack_lab.storage.opening_snapshots import OpeningSnapshots
        from blackjack_lab.storage.analysis_snapshots import AnalysisSnapshots
        from blackjack_lab.storage.database import LocalStore
        from blackjack_lab.ui.controller import SessionController
        from blackjack_lab.ledger.ledger import EventLedger
        for owner,name,label in [(information,'project_prepared','projection'),(sidebet_view,'execute','sidebet_compute'),
                (SidebetSnapshots,'save','sidebet_save_validate'),(OpeningSnapshots,'save','opening_save_validate'),
                (AnalysisSnapshots,'save','main_save_validate'),(LocalStore,'save_event','event_commit'),
                (LocalStore,'save_ledger','ledger_commit'),(SessionController,'save_entry_plan','plan_save'),
                (EventLedger,'replay','replay')]:self.wrap(owner,name,label)
        original=LatestWorker.submit
        def submit(worker,channel,function,request_id=None):
            queued=perf_counter();self.submitted+=1
            def work():
                self.executed+=1;self.samples['queue_wait.'+channel].append(perf_counter()-queued)
                start=perf_counter()
                try:return function()
                finally:self.samples['work_wall.'+channel].append(perf_counter()-start)
            result=original(worker,channel,work,request_id)
            self.max_pending=max(self.max_pending,len(worker.pending))
            return result
        self.stack.enter_context(patch.object(LatestWorker,'submit',submit))
        from blackjack_lab.analysis.service import AnalysisService
        from blackjack_lab.analysis.opening_service import OpeningService
        for cls,label in [(AnalysisService,'main_numeric'),(OpeningService,'opening_numeric')]:
            self.wrap(cls,'start',label+'_start')
        original_poll=AnalysisService.poll
        def poll(service):
            job=service.active
            result=original_poll(service)
            if result is not None and job is not None:
                label='opening_numeric' if isinstance(service,OpeningService) else 'main_numeric'
                self.samples[label+'.'+result.get('status','unknown')].append(perf_counter()-job['start'])
            return result
        self.stack.enter_context(patch.object(AnalysisService,'poll',poll))

    def sample(self):self.max_threads=max(self.max_threads,threading.active_count())

    def summary(self):
        return dict(wall_seconds={k:quantiles(v) for k,v in self.samples.items()},
                    submitted=self.submitted,executed=self.executed,max_pending_per_worker=self.max_pending,
                    peak_python_threads=self.max_threads,note='Instrumented nested wall times; not additive, and instrumented runs are separate from unprofiled scale latency.')

    def close(self):self.stack.close()
