"""Immutable derived analysis sidecars. Writes never mutate event SQLite."""
from pathlib import Path
import json
from time import time
import uuid

from ..analysis.contracts import RESULT_SCHEMA, canonical, digest
from .safe_files import atomic_write

SNAPSHOT_SCHEMA = "hakimi-analysis-snapshot-v1"


class AnalysisSnapshots:
    def __init__(self, directory):
        self.directory = Path(directory)

    def save(self, result, recomputed_from=None):
        if result.get("schema") != RESULT_SCHEMA or digest(result["input"]) != result["input_digest"]:
            raise ValueError("分析结果输入身份不匹配")
        snapshot_id = uuid.uuid4().hex
        body = {"schema": SNAPSHOT_SCHEMA, "snapshot_id": snapshot_id, "saved_at": time(),
                "recomputed_from": recomputed_from, "result": result}
        envelope = {**body, "content_digest": digest(body)}
        atomic_write(self.directory / (snapshot_id + ".json"), canonical(envelope).encode("utf-8"), overwrite=False)
        return envelope

    def load(self, snapshot_id):
        if not isinstance(snapshot_id, str) or len(snapshot_id) != 32 or any(c not in "0123456789abcdef" for c in snapshot_id):
            raise ValueError("无效分析快照ID")
        data = json.loads((self.directory / (snapshot_id + ".json")).read_text(encoding="utf-8"))
        signature = data.pop("content_digest")
        if data.get("schema") != SNAPSHOT_SCHEMA or data.get("snapshot_id") != snapshot_id or digest(data) != signature:
            raise ValueError("分析快照内容摘要校验失败")
        result = data["result"]
        if result.get("schema") != RESULT_SCHEMA or digest(result["input"]) != result["input_digest"]:
            raise ValueError("分析快照输入身份不匹配")
        return {**data, "content_digest": signature}

    def list(self):
        entries, damaged = [], []
        for path in self.directory.glob("*.json"):
            try:
                entries.append(self.load(path.stem))
            except (ValueError, OSError, KeyError) as error:
                damaged.append({"file": path.name, "error": str(error)})
        return sorted(entries, key=lambda e: (e["saved_at"], e["snapshot_id"])), damaged

    @staticmethod
    def matches_prefix(saved, ledger):
        data = saved["result"]["input"]
        if ledger.session_id != data["session_id"]:
            return False
        prefix = [e for e in ledger.to_list() if e["seq"] <= data["through_seq"]]
        return digest(prefix) == data["prefix_digest"]
