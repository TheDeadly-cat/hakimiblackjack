"""Private, isolated preparation for one synchronous recording command.

Public EventLedger append/import/replay keep their independent validation.
This candidate is discarded on failure and never published as a live ledger.
"""
import copy

from .ledger import EventLedger, LedgerError
from .events import Event


_ATOMIC_TYPES = frozenset((str, int, float, bool, type(None)))


class _GenericCopyRequired(Exception):
    pass


def _copy_command_state(ledger):
    """Copy ordinary local data without generic object reconstruction overhead.

    Own all mutable containers and retain shared references within the copy.
    Extensions and custom copy protocols use the original deepcopy operation.
    This performs no validation; every independent replay remains unchanged.
    """
    if any(name in Event.__dict__ for name in
           ('__deepcopy__', '__reduce__', '__reduce_ex__', '__getstate__', '__setstate__',
            '__getnewargs__', '__getnewargs_ex__')):
        return copy.deepcopy(ledger.__dict__)
    memo = {}

    def clone(value):
        kind = type(value)
        if kind in _ATOMIC_TYPES:
            return value
        if id(value) in memo:
            return memo[id(value)]
        if kind is dict:
            result = {}
            memo[id(value)] = result
            for key, item in value.items():
                if type(key) not in _ATOMIC_TYPES:
                    raise _GenericCopyRequired
                result[key] = clone(item)
            return result
        if kind is list:
            result = []
            memo[id(value)] = result
            result.extend(clone(item) for item in value)
            return result
        if kind is tuple and all(type(item) in _ATOMIC_TYPES for item in value):
            return value  # deepcopy also retains an entirely atomic tuple.
        if kind is set and all(type(item) in _ATOMIC_TYPES for item in value):
            result = value.copy()
            memo[id(value)] = result
            return result
        if kind is Event:
            result = object.__new__(Event)
            memo[id(value)] = result
            result.__dict__.update(clone(value.__dict__))
            return result
        raise _GenericCopyRequired

    try:
        return clone(ledger.__dict__)
    except (_GenericCopyRequired, RecursionError):
        # The speculative built-in copies touched no source object and invoked
        # no custom protocol. Fall back with a fresh memo and original ordering.
        return copy.deepcopy(ledger.__dict__)


class CommandCandidate(EventLedger):
    def __init__(self, ledger, baseline):
        self.__dict__.update(_copy_command_state(ledger))
        self._baseline = baseline
        self._baseline_events = baseline.to_list()
        if [e.to_dict() for e in self.events] != self._baseline_events:
            raise LedgerError('命令基线内容已变化，请重新核对')
        self._validated_suffix = []
        self._failed = False
        self._prepared = EventLedger.replay(self)
        self._sync_context(self._prepared)

    def _copy_for_append(self):
        # This entire object is already private to the command. The production
        # append method still validates every intermediate prefix in order.
        if self._failed:
            raise LedgerError('失败的录牌候选不能再次使用')
        return self

    @staticmethod
    def _semantic_event(event):
        return {k: v for k, v in event.to_dict().items() if k not in ('source', 'evidence')}

    def _validate_append(self):
        self._prepared = None
        self._prepared = EventLedger.replay(self)
        self._sync_context(self._prepared)
        self._validated_suffix.append(copy.deepcopy(self._semantic_event(self.events[-1])))

    def append(self, event):
        if self._failed:
            raise LedgerError('失败的录牌候选不能再次使用')
        try:
            return super().append(event)
        except Exception:
            self._failed = True
            self._prepared = None
            raise

    def replay(self, through_seq=None):
        if self._failed:
            raise LedgerError('失败的录牌候选不能再次使用')
        if through_seq is not None:
            # Do not inherit a full-prefix cache when asking for past state.
            return EventLedger.from_list(self.session_id, [e.to_dict() for e in self.events
                if e.seq <= through_seq], self.rule_version).replay()
        return self._prepared if self._prepared is not None else EventLedger.replay(self)

    def validated_suffix(self):
        if self._failed or self._prepared is None:
            raise LedgerError('录牌候选未通过完整预演')
        count = len(self._baseline_events)
        if [e.to_dict() for e in self.events[:count]] != self._baseline_events:
            raise LedgerError('录牌命令不能改写既有事件')
        suffix = self.events[count:]
        if [self._semantic_event(e) for e in suffix] != self._validated_suffix:
            raise LedgerError('录牌候选在预演后发生内容变化')
        return suffix

    def published_ledger(self):
        # No preparation cache or baseline survives on the published ledger.
        result = EventLedger(self.session_id, self.rule_version)
        for name in result.__dict__:
            setattr(result, name, getattr(self, name))
        return result
