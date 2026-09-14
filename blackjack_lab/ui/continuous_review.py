"""Continuous preview -> immutable review queue, without stopping capture."""
from __future__ import annotations

import hashlib
import json
import time
from uuid import uuid4

from ..vision.evidence_store import EvidenceStore
from .realtime_review import freeze_video_result, freeze_window_result


class ContinuousReviewFeed:
    def __init__(self, work, owner, context_guard=lambda: True):
        self.work, self.owner, self.context_guard = work, owner, context_guard
        self.bound = work.binding()
        self.last_row = None
        self.token = None
        self.error = ""
        self.enabled = True
        self.replay_mode = False
        self.last_frame = None
        self.title_snapshot = None
        self.last_change_ns = time.perf_counter_ns()
        self.epoch = owner.run_id + ":review:" + uuid4().hex
        work.connect(self.epoch, self.problem)
        work.manual_evidence = self.manual_evidence

    def manual_evidence(self, draft_id):
        from ..vision.live_input import frame_to_loaded
        from ..vision.image_io import write_png_rgb
        packet = self.owner.source.preview()
        loaded = frame_to_loaded(packet)
        folder = self.work.root / "manual-frames"
        folder.mkdir(exist_ok=True)
        path = folder / (draft_id + ".png")
        write_png_rgb(path, loaded.width, loaded.height, loaded.rgb)
        return str(path), {"packet": packet.as_dict(), "source_epoch": self.epoch,
                           "source_png_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    def problem(self):
        owner, source = self.owner, self.owner.source
        if self.error:
            return self.error
        if not self.context_guard() or self.work.binding() != self.bound:
            return "来源配置或轮次已改变；请重新连接当前轮"
        if self.replay_mode:
            return ""
        if owner.stopped or owner.finished or getattr(source, "finished", False):
            return "来源已停止；旧候选仅供回看，不能作为当前牌桌确认"
        if owner.error or getattr(source, "error", None):
            return "来源中断：" + str(owner.error or source.error)
        packet = source.preview()
        if packet is None:
            return "尚未收到来源画面"
        if self.token is not None and packet.token() != self.token:
            return "捕获窗口、尺寸或区域已改变，请重新连接"
        if (time.perf_counter_ns() - packet.observed_monotonic_ns) > 1_500_000_000:
            return "来源超过 1.5 秒未更新，暂停确认"
        if self.last_frame != packet.frame_content_signature:
            self.last_frame, self.last_change_ns = packet.frame_content_signature, time.perf_counter_ns()
        elif time.perf_counter_ns() - self.last_change_ns > 3_000_000_000:
            return "来源画面已静止超过 3 秒，请检查播放/捕获；暂停确认"
        if getattr(source, "is_live", False):
            source.verify_selected_window()
            title = (getattr(source, "window_info", None) or {}).get("title")
            if self.title_snapshot is not None and title != self.title_snapshot:
                self.error = "窗口标题已改变，可能切换了牌桌；请核对来源后重新绑定"
                return self.error
            self.title_snapshot = title
        return ""

    def poll(self):
        if not self.enabled or self.replay_mode:
            return False
        if self.problem():
            return False
        row = self.owner.latest_result()
        if row is None or self.last_row == row.row_id:
            return False
        if self.token is None:
            self.token = row.packet.token()
        self.last_row = row.row_id
        fresh = [o for o in row.recognition.observations
                 if (self.epoch, o.observation_id) not in self.work.seen]
        if not fresh:
            return False
        if sum(d.source_epoch is not None and not d.blocked for d in self.work.pending) >= self.work.capacity:
            if not self.work.overflow:
                self.work._record("queue-overflow", bound=self.bound, requires_observation_check=True)
            self.work.overflow += len(fresh)
            return False
        source = self.owner.source
        if getattr(source, "is_live", False):
            snapshot = freeze_window_result(self.owner, row, source.hwnd, source.expected_process_id)
        else:
            snapshot = freeze_video_result(self.owner, row, source.asset.sha256)
        wanted = {o.observation_id for o in fresh}
        snapshot.result.observations = [o for o in snapshot.result.observations if o.observation_id in wanted]
        originals = {o.observation_id: o.as_dict() for o in snapshot.result.observations}
        keys = {}
        for obs in snapshot.result.observations:
            key = obs.observation_id
            obs.observation_id = hashlib.sha256(f"{self.epoch}:{row.row_id}:{key}".encode()).hexdigest()[:32]
            keys[obs.observation_id] = key
        # Rebinding the same retained row must not overwrite older review files.
        folder = self.work.root / "frames" / (snapshot.metadata["snapshot_id"] + "-" + uuid4().hex[:12])
        folder.mkdir(parents=True, exist_ok=False)
        EvidenceStore(folder).save_result(snapshot.loaded, snapshot.result)
        snapshot.metadata["review_scope"] = "queued draft only; no verified physical identity or seat"
        (folder / "capture.json").write_text(json.dumps(snapshot.metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        added = False
        for obs in snapshot.result.observations:
            key = keys[obs.observation_id]
            draft = self.work.enqueue(key=key, rank=obs.accepted_rank(),
                original={"observation": originals[key], "capture": snapshot.metadata},
                source_image=folder / "source.png", crop_image=folder / obs.crop_relpath,
                arrival_ns=row.packet.observed_monotonic_ns,
                observed_at=obs.captured_at)
            added = draft is not None or added
        return added

    def enter_replay(self):
        if getattr(self.owner.source, "is_live", False):
            raise ValueError("窗口来源不能转为录像复盘")
        if not self.context_guard() or self.work.binding() != self.bound or self.error:
            raise ValueError("来源或轮次已改变，不能继续核对旧队列")
        self.owner.stop()
        self.replay_mode = True
        self.work._record("explicit-offline-review", epoch=self.epoch, bound=self.bound,
                          note="source is stopped; confirmations are retrospective, not live")
