"""Immutable full-content identity shared only by the existing UI read frame."""
from dataclasses import dataclass
import hashlib
import json

from ..analysis.contracts import canonical, digest


@dataclass(frozen=True)
class PrefixSnapshot:
    session_id: str
    through_seq: int | None
    content: bytes
    prefix_digest: str

    @classmethod
    def capture(cls,ledger):
        # Serialization itself produces owned immutable bytes. Copying every
        # payload first adds no isolation: capture is synchronous on the owner.
        events=[event.to_dict() for event in ledger.events]
        content=canonical(events).encode('utf-8')
        return cls(ledger.session_id,events[-1]['seq'] if events else None,content,hashlib.sha256(content).hexdigest())

    def to_list(self):
        """Consumers receive a new mutable copy; the shared byte snapshot cannot change."""
        return json.loads(self.content)


def event_prefix_digest(prefix):
    """Owned immutable bytes need no repeated JSON serialization per seat."""
    return prefix.prefix_digest if isinstance(prefix, PrefixSnapshot) else digest(prefix)
