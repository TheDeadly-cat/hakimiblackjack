"""One bounded worker for realtime preview; no controller, solver or ledger calls.

Video replay and WGC share the existing FrameIntake contract. Reading the preview
does not consume recognition frames. All timing uses the process monotonic clock.
"""
from __future__ import annotations

import json
import math
import threading
import time
from copy import deepcopy
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from .capture.frame_intake import FrameIntake
from .vision.contracts import SOURCE_OBSERVER_VIDEO
from .vision.image_io import rgb_to_bgr
from .vision.live_input import LiveStyle, SOURCE_LIVE_CAPTURE, frame_to_loaded, layout_for_frame, capture_crop_pixels
from .vision.temporal_preview import TemporalPreviewTracker, POLICY_VERSION, display_state
from .vision.model_adapter import RecognitionRuntime
from .vision.video_io import VideoReader


class RealtimeVideoSource:
    """Wall-clock paced adapter around the existing readonly VideoReader."""

    is_live = False
    source_declaration = SOURCE_OBSERVER_VIDEO

    def __init__(self, path, style, *, first_frame=0, last_frame=None, preview_fps=20.0):
        if first_frame < 0 or not 1 <= preview_fps <= 60:
            raise ValueError("Invalid playback interval or preview FPS")
        if not isinstance(style, LiveStyle):
            raise ValueError("实时预览需选择已有的归一化区域样式 JSON")
        self.path, self.style = Path(path), style
        self.first_frame, self.last_frame = first_frame, last_frame
        self.preview_fps = float(preview_fps)
        self.intake = None
        self.asset = None
        self.error = None
        self.finished = False
        self.base_ns = None
        self.playback_ended_ns = None
        self.skipped_source_frames = 0
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread is not None:
            raise RuntimeError("A playback source cannot be started twice")
        self._thread = threading.Thread(target=self._run, name="blackjack-video-preview", daemon=True)
        self._thread.start()

    def clone(self):
        return type(self)(self.path,self.style,first_frame=self.first_frame,
                          last_frame=self.last_frame,preview_fps=self.preview_fps)

    def _run(self):
        try:
            with VideoReader(self.path) as reader:
                self.asset = reader.asset
                if self.asset.fps <= 0:
                    raise ValueError("1倍速播放需要可信的源帧率")
                last = self.asset.frame_count-1 if self.last_frame is None else self.last_frame
                if not self.first_frame <= last < self.asset.frame_count:
                    raise ValueError("播放范围超出录像")
                crop = capture_crop_pixels(self.style, self.asset.width, self.asset.height)
                width, height = crop[2:] if crop else (self.asset.width, self.asset.height)
                cadence_fps=min(self.preview_fps,self.asset.fps)
                self.intake = FrameIntake("video:"+self.asset.sha256,
                    # Playback is already paced. A second nearly identical rate
                    # limit discarded valid jittered frames and capped 15 FPS tests.
                    target_fps=max(self.asset.fps,cadence_fps*4), queue_length=2, crop=crop,
                    layout_version=self.style.layout_version(width, height))
                # Startup file verification is outside playback, not hidden in frame latency.
                first = reader.seek(self.first_frame)
                if self._stop.is_set():
                    return
                self.base_ns = time.perf_counter_ns()
                self.intake.mark_started(now_ns=self.base_ns)
                index, previous = self.first_frame, self.first_frame-1
                while not self._stop.is_set():
                    loaded = first if index == self.first_frame else reader.advance(index)
                    self.skipped_source_frames += max(0, index-previous-1)
                    self.intake.offer(rgb_to_bgr(loaded),
                        media_time_ns=round(index/self.asset.fps*1_000_000_000))
                    previous = index
                    if index >= last:
                        break
                    elapsed = (time.perf_counter_ns()-self.base_ns)/1_000_000_000
                    next_tick = math.floor(elapsed*cadence_fps)+1
                    deadline = self.base_ns+round(next_tick/cadence_fps*1_000_000_000)
                    if self._stop.wait(max(0, (deadline-time.perf_counter_ns())/1_000_000_000)):
                        break
                    elapsed = (time.perf_counter_ns()-self.base_ns)/1_000_000_000
                    index = min(last, max(previous+1, self.first_frame+int(elapsed*self.asset.fps)))
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            self.playback_ended_ns = time.perf_counter_ns()
            self.finished = True

    def latest(self):
        return self.intake.latest() if self.intake else None

    def preview(self):
        return self.intake.preview() if self.intake else None

    def token(self):
        return self.intake.token() if self.intake else None

    def stop(self):
        self._stop.set()
        if self.intake:
            self.intake.mark_stopped()

    def report(self):
        return {**(self.intake.report() if self.intake else {}),
            "backend": "existing-video-reader-1x", "finished": self.finished,
            "error": self.error, "source_frames_skipped_for_preview": self.skipped_source_frames,
            "playback_base_ns": self.base_ns, "source_sha256": self.asset.sha256 if self.asset else None,
            "playback_ended_ns": self.playback_ended_ns,
            "source_fps": self.asset.fps if self.asset else None,
            "first_frame": self.first_frame, "last_frame": self.last_frame,
            "preview_target_fps": self.preview_fps,
            "preview_paced_fps": min(self.preview_fps,self.asset.fps) if self.asset else None}


class RealtimeWgcSource:
    """Lifecycle adapter over the existing WGC backend and its FrameIntake."""

    is_live = True
    source_declaration = SOURCE_LIVE_CAPTURE

    def __init__(self, hwnd, style, *, preview_fps=20.):
        if not isinstance(style,LiveStyle):raise ValueError('实时窗口需要归一化样式')
        self.hwnd,self.style,self.preview_fps=int(hwnd),style,float(preview_fps)
        self.backend=self.intake=None
        self.finished=False
        self.error=None
        self.base_ns=None
        self._stop=threading.Event()
        self._lock=threading.Lock()
        self._configured_size=None
        self._thread=None

    def clone(self):
        return type(self)(self.hwnd,self.style,preview_fps=self.preview_fps)

    def start(self):
        if self._thread is not None:raise RuntimeError('Capture preview already started')
        self._thread=threading.Thread(target=self._run,name='blackjack-wgc-preview',daemon=True)
        self._thread.start()

    def _run(self):
        from .capture.wgc_source import open_window_source
        from .capture.contracts import STATUS_DENIED,STATUS_SOURCE_LOST
        try:
            self.backend=open_window_source(self.hwnd,target_fps=self.preview_fps,queue_length=2)
            self.intake=self.backend.intake
            if self._stop.is_set():return
            self.backend.start()
            self.base_ns=time.perf_counter_ns()
            while not self._stop.wait(.05):
                status=self.backend.status()
                if status in (STATUS_DENIED,STATUS_SOURCE_LOST):
                    self.error='窗口捕获停止：'+status
                    break
        except Exception as exc:
            self.error=f'{type(exc).__name__}: {exc}'
        finally:
            self._stop.set()
            if self.intake:self.intake.new_epoch('窗口预览来源结束')
            if self.backend:self.backend.stop()
            self.finished=True

    def _packet(self, preview=False):
        if self._stop.is_set() or self.intake is None:return None
        packet=self.intake.preview() if preview else self.intake.latest()
        if packet is None:return None
        with self._lock:
            if packet.source_size!=self._configured_size:
                crop=capture_crop_pixels(self.style,*packet.source_size)
                self.intake.set_crop(crop)
                width,height=crop[2:] if crop else packet.source_size
                self.intake.set_layout_version(self.style.layout_version(width,height))
                self._configured_size=packet.source_size
            if packet.token()!=self.intake.token():return None
        return packet

    def latest(self):return self._packet()

    def preview(self):return self._packet(preview=True)

    def token(self):return self.intake.token() if self.intake else None

    def stop(self):
        # UI only invalidates the intake; the background owner releases WGC.
        self._stop.set()
        if self.intake:
            self.intake.new_epoch('窗口预览停止')
            self.intake.mark_stopped()

    def report(self):
        return {**(self.backend.report() if self.backend else {}),'backend':'windows-graphics-capture',
                'finished':self.finished,'error':self.error,'playback_base_ns':self.base_ns,
                'window_hwnd':self.hwnd,'preview_target_fps':self.preview_fps,'is_live':True}


@dataclass
class PreviewResult:
    packet: Any
    recognition: Any
    timings: dict
    row_id: int
    display_ns: int | None = None
    tracks: list = field(default_factory=list)

    def display_tracks(self, now_ns):
        return [display_state(row, now_ns) for row in self.tracks]

    def metadata(self):
        return {"row_id": self.row_id, "packet": self.packet.as_dict(),
            "observed_monotonic_ns": self.packet.observed_monotonic_ns,
            "timings": dict(self.timings), "display_submitted_ns": self.display_ns,
            "observations": [{"track_id": obs.observation_id, "bbox": dict(obs.bbox),
                "observed_rank": obs.accepted_rank(), "region_id": obs.region_id}
                for obs in self.recognition.observations],
            "tracks": [dict(row) for row in self.tracks],
            "geometry_review_count": len(self.recognition.geometry_review),
            "model_id": self.recognition.model_id, "model_digest": self.recognition.model_digest,
            "writes_ledger": False}


class RealtimePreviewSession:
    """Single resident model, latest-frame inference and bounded timing evidence."""

    def __init__(self, source, style, adapter, *, recognition_fps=8.0, evidence_limit=1200):
        if not 1 <= recognition_fps <= 60 or evidence_limit < 1:
            raise ValueError("Invalid realtime preview settings")
        self.source, self.style, self.adapter = source, style, adapter
        self.runtime = RecognitionRuntime(adapter)
        rank_model=getattr(adapter,'model',None)
        if rank_model is not None and hasattr(rank_model,'clear_prediction_cache'):
            rank_model.clear_prediction_cache()
        self.run_id = uuid4().hex
        self.recognition_fps = float(recognition_fps)
        self.tracker = TemporalPreviewTracker()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._latest = None
        self.records = deque(maxlen=evidence_limit)
        self.source_displays = deque(maxlen=evidence_limit)
        self.display_updates = deque(maxlen=evidence_limit)
        self._display_key = None
        self.display_update_evictions = 0
        self.error = None
        self.finished = False
        self.processed = self.stale_results = self.repeat_or_black = 0
        self.evidence_evictions = 0
        self.source_display_evictions = 0
        self._token = None

    def start(self):
        if self._thread is not None:
            raise RuntimeError("Preview session already started")
        self._thread = threading.Thread(target=self._run, name="blackjack-recognition-preview", daemon=True)
        self._thread.start()

    def _run(self):
        try:
            self.source.start()
            deadline = 0
            while not self._stop.is_set():
                now = time.perf_counter_ns()
                if now < deadline:
                    self._stop.wait(min(.02, (deadline-now)/1_000_000_000))
                    continue
                packet = self.source.latest()
                if packet is None:
                    if getattr(self.source, "finished", False):
                        break
                    self._stop.wait(.005)
                    continue
                picked = time.perf_counter_ns()
                if packet.token() != self.source.token():
                    self.stale_results += 1
                    continue
                if packet.token() != self._token:
                    self._token = packet.token()
                    self.runtime.invalidate('实时来源代次改变')
                    self.tracker = TemporalPreviewTracker()
                    with self._lock:
                        self._latest = None
                if packet.is_black or packet.is_repeat:
                    self.repeat_or_black += 1
                    continue
                timings = {"picked_ns": picked, "preprocessing_start_ns": time.perf_counter_ns()}
                loaded = frame_to_loaded(packet)
                layout = layout_for_frame(self.style, packet.width, packet.height)
                timings["preprocessing_end_ns"] = time.perf_counter_ns()
                result = self.runtime.recognize_loaded(loaded,layout=layout,
                    source_key=json.dumps(packet.token().as_dict(),sort_keys=True),
                    source_declaration=getattr(self.source,'source_declaration',SOURCE_OBSERVER_VIDEO),
                    timings=timings)
                if result is None:
                    self.stale_results += 1
                    continue
                result.captured_at=packet.observed_at
                result.clock_note='captured_at 来自本机接帧时刻；媒体时刻单独保留在 FramePacket。'
                for observation in result.observations:
                    observation.captured_at=packet.observed_at
                timings["tracking_start_ns"] = time.perf_counter_ns()
                states = self.tracker.update(result, packet.pixels, packet.observed_monotonic_ns,
                    (packet.token(), packet.frame_content_signature))
                timings["candidate_ready_ns"] = time.perf_counter_ns()
                if self._stop.is_set() or packet.token() != self.source.token():
                    self.stale_results += 1
                    continue
                self.processed += 1
                row = PreviewResult(packet, result, timings, self.processed, tracks=states)
                with self._lock:
                    self._latest = row
                    if len(self.records) == self.records.maxlen:
                        self.evidence_evictions += 1
                    # Keep metadata, not 1200 full-size pixel buffers.
                    self.records.append(row.metadata())
                # Schedule from the previous start, never catch up a queue of old frames.
                deadline = max(picked+round(1_000_000_000/self.recognition_fps), time.perf_counter_ns())
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            if self.error:self.source.stop()
            self.finished = True

    def latest_result(self):
        if self._stop.is_set():
            return None
        if self._token is not None and self._token != self.source.token():
            self.runtime.invalidate('实时来源已经改变，撤回旧结果')
            return None
        with self._lock:
            row = self._latest
            return row if row is not None and row.packet.token() == self.source.token() else None

    def result_display_ns(self, row_id):
        with self._lock:
            return next((row["display_submitted_ns"] for row in reversed(self.records)
                         if row["row_id"] == row_id), None)

    @property
    def stopped(self):
        return self._stop.is_set()

    def note_source_display(self, packet, submitted_ns):
        with self._lock:
            if self.source_displays and self.source_displays[-1]["frame_id"] == packet.frame_id:
                return
            if len(self.source_displays) == self.source_displays.maxlen:
                self.source_display_evictions += 1
            self.source_displays.append({"frame_id": packet.frame_id, "media_time_ns": packet.media_time_ns,
                "display_submitted_ns": submitted_ns, "observed_monotonic_ns": packet.observed_monotonic_ns,
                "source_token": packet.token().as_dict(),
                "frame_content_signature": packet.frame_content_signature,
                "image_size": [packet.width, packet.height], "source_size": packet.source_size,
                "crop_origin": packet.crop_origin})

    def note_result_display(self, row_id, submitted_ns):
        with self._lock:
            for row in reversed(self.records):
                if row["row_id"] == row_id and row["display_submitted_ns"] is None:
                    row["display_submitted_ns"] = submitted_ns
                    row["displayed_tracks"] = [display_state(t, submitted_ns) for t in row["tracks"]]
                    return

    def note_rendered_state(self, row_id, tracks, source_packet, submitted_ns, scale):
        """Record the state actually drawn, including later expiry-only repaints."""
        if self.stopped or source_packet is None or source_packet.token() != self.source.token():
            return
        key=(row_id,source_packet.frame_id,tuple((t['track_id'],t['observed_rank'],
            t['stable_rank'],t['identity_state'],t['current']) for t in tracks))
        with self._lock:
            if key == self._display_key:return
            self._display_key=key
            if len(self.display_updates)==self.display_updates.maxlen:
                self.display_update_evictions+=1
            actual=deepcopy(tracks)
            self.display_updates.append({'row_id':row_id,'display_submitted_ns':submitted_ns,
                'source_frame_id':source_packet.frame_id,'source_media_time_ns':source_packet.media_time_ns,
                'source_token':source_packet.token().as_dict(),'source_display_scale':scale,'tracks':actual})
            for row in reversed(self.records):
                if row['row_id']==row_id and row['display_submitted_ns'] is None:
                    row['display_submitted_ns']=submitted_ns
                    row['displayed_tracks']=deepcopy(actual)
                    break

    def stop(self):
        self._stop.set()
        self.runtime.invalidate('实时预览已停止')
        self.source.stop()

    def snapshot(self):
        with self._lock:
            return {"schema": "realtime-preview-evidence-1", "run_id": self.run_id,
                "source": self.source.report(),
                "processed_frames": self.processed, "target_recognition_fps": self.recognition_fps,
                "stale_results_discarded": self.stale_results, "repeat_or_black_skipped": self.repeat_or_black,
                "evidence_evictions": self.evidence_evictions, "finished": self.finished,
                "source_display_evictions": self.source_display_evictions,
                "display_update_evictions": self.display_update_evictions,
                "track_capacity_drops": self.tracker.capacity_drops,
                "error": self.error or getattr(self.source, "error", None),
                "model_id": self.adapter.model_id, "model_digest": self.adapter.digest,
                "rows": [dict(row) for row in self.records], "source_displays": list(self.source_displays),
                "display_updates": list(self.display_updates),
                "latency_scope": "monotonic frame arrival to application display submission; readable-event ground truth is separate",
                "stable_fusion_implemented": True, "temporal_policy": POLICY_VERSION,
                "identity_scope": "session-local preview associations, not verified physical or ledger IDs",
                "writes_ledger": False}

    def save(self, path):
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(self.snapshot(), stream, ensure_ascii=False, indent=2)
