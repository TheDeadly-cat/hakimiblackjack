"""Temporary CPython thread fairness while a GUI recording batch is outstanding.

Tk releases the GIL for each widget call. A CPU-heavy replay can otherwise take
the default timeslice after every such call. This is not a latency guarantee;
measure the actual heartbeat. Restore the prior setting when all batches drain.
"""
import sys
import threading

_lock = threading.Lock()
_owners = {}
_previous = _applied = None


def acquire(owner):
    global _previous, _applied
    with _lock:
        if owner in _owners:
            _owners[owner] += 1
            return
        if not _owners:
            _previous = sys.getswitchinterval()
            _applied = min(_previous, .001)
            sys.setswitchinterval(_applied)
        _owners[owner] = 1


def release(owner):
    global _previous, _applied
    with _lock:
        if owner in _owners:
            _owners[owner] -= 1
            if _owners[owner] == 0:
                del _owners[owner]
        if not _owners and _previous is not None:
            if sys.getswitchinterval() == _applied:
                sys.setswitchinterval(_previous)
            _previous = _applied = None
