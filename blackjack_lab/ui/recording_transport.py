"""Lossless references to owned prefix bytes across one FIFO recording chain.

All ledger/state objects are still transported in full. The parent independently
captures the full ledger before accepting a reconstructed prefix for later use.
"""
from dataclasses import dataclass, replace
import hashlib

from .read_snapshot import PrefixSnapshot


def _identity(prefix):
    return prefix.session_id, prefix.through_seq, prefix.prefix_digest


@dataclass(frozen=True)
class PrefixDeltaReceipt:
    receipt: object
    before_identity: tuple
    after_identity: tuple
    suffix: bytes


class ReceiptPrefixSender:
    def __init__(self):
        self.chain = self.prefix = None

    def encode(self, receipt, task):
        if receipt is None or receipt.status != 'committed':
            self.chain = self.prefix = None
            return receipt
        before, after = receipt.before, receipt.after
        known = self.prefix if self.chain == receipt.chain_id else task.base
        packet = receipt  # Non-append content retains the original full packet.
        if (receipt.request_id == task.request_id and receipt.chain_id == task.chain_id
                and type(before) is PrefixSnapshot and type(after) is PrefixSnapshot
                and known == before and type(before.content) is bytes
                and type(after.content) is bytes and before.content.startswith(b'[')
                and before.content.endswith(b']') and after.content.endswith(b']')
                and after.content.startswith(before.content[:-1])):
            packet = PrefixDeltaReceipt(replace(receipt, before=None, after=None),
                _identity(before), _identity(after), after.content[len(before.content) - 1:-1])
        self.chain, self.prefix = receipt.chain_id, after
        return packet


class ReceiptPrefixReceiver:
    def __init__(self):
        self.chain = self.prefix = None

    def reference(self, task):
        return self.prefix if self.chain == task.chain_id else task.base

    def decode(self, packet, task):
        if not isinstance(packet, PrefixDeltaReceipt):
            return packet
        if task is None:
            raise ValueError('前缀回执缺少原始已接收请求')
        receipt = packet.receipt
        known = self.reference(task)
        if (receipt.request_id != task.request_id or receipt.chain_id != task.chain_id
                or receipt.status != 'committed' or receipt.before is not None
                or receipt.after is not None or packet.before_identity != _identity(known)
                or type(packet.suffix) is not bytes or type(packet.after_identity) is not tuple
                or len(packet.after_identity) != 3):
            raise ValueError('前缀回执身份、顺序或内容不符')
        session, seq, digest = packet.after_identity
        if session != known.session_id or type(seq) is not int:
            raise ValueError('前缀回执会话或序号无效')
        content = known.content[:-1] + packet.suffix + b']'
        if hashlib.sha256(content).hexdigest() != digest:
            raise ValueError('前缀回执完整内容摘要不符')
        return replace(receipt, before=known,
            after=PrefixSnapshot(session, seq, content, digest))

    def accepted(self, receipt, task):
        """Call only after independent full-ledger verification in the parent."""
        if receipt.status != 'committed':
            self.chain = self.prefix = None
            return
        if task is None or receipt.before != self.reference(task):
            raise ValueError('前缀回执与已核对的完整前缀不符')
        self.chain, self.prefix = receipt.chain_id, receipt.after
