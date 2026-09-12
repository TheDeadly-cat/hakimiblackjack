# -*- coding: utf-8 -*-
"""跨帧物理牌身份。同一观察只形成一次；不按牌面去重；不写账本。"""
from __future__ import annotations

import hashlib
from typing import Dict, Iterable, List, Sequence, Tuple

from .contracts import CardObservation, RecognitionResult
from .video_contracts import Detection, PhysicalTrack


def detections_from_observations(observations: Sequence[CardObservation]) -> List[Detection]:
    return [
        Detection(
            bbox=dict(obs.bbox),
            region_id=obs.region_id,
            seat_hint=obs.seat_hint,
            hand_hint=obs.hand_hint,
            rank_hint=obs.accepted_rank(),
            face_state=obs.face_state_candidate,
            crop_sha256=obs.crop_sha256,
        )
        for obs in observations
    ]


def bbox_iou(a: Dict[str, int], b: Dict[str, int]) -> float:
    ax2, ay2 = a["x"] + a["w"], a["y"] + a["h"]
    bx2, by2 = b["x"] + b["w"], b["y"] + b["h"]
    ix1, iy1 = max(a["x"], b["x"]), max(a["y"], b["y"])
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    union = a["w"] * a["h"] + b["w"] * b["h"] - inter
    return inter / union if union else 0.0


def _center(box: Dict[str, int]) -> Tuple[float, float]:
    return box["x"] + box["w"] / 2.0, box["y"] + box["h"] / 2.0


def _distance(a: Dict[str, int], b: Dict[str, int]) -> float:
    ax, ay = _center(a)
    bx, by = _center(b)
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


def _new_ids(seed: str) -> Tuple[str, str]:
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return digest[:32], "vtrack:" + digest[32:48]


class FrameTracker:
    """贪心 IoU / 中心距离关联。跳转重放时已提交观察不再生成新身份。"""

    def __init__(self, *, iou_min: float = 0.25, max_center_distance: float = 80.0):
        self.iou_min = iou_min
        self.max_center_distance = max_center_distance
        self.tracks: List[PhysicalTrack] = []
        self._seq = 0
        self.round_key = "initial"
        self._round_groups = {}

    def confirm_round_boundary(self, boundary_key: str) -> bool:
        """Explicitly bind a user-confirmed round, preserving past identities.

        Neither time gaps nor empty detector output proves that a new round
        started. Callers must obtain a real round decision (for example the
        controller's already-opened round), and never invoke this on a timeout.
        Rebinding the same round is idempotent; revisiting one restores its
        committed tracks instead of permitting a second ledger debit.
        """
        if not isinstance(boundary_key, str) or not boundary_key.strip():
            raise ValueError("需要明确的已确认轮次身份")
        if boundary_key == self.round_key:
            return False
        self._round_groups[self.round_key] = (self.tracks, self._seq)
        self.tracks, self._seq = self._round_groups.get(boundary_key, ([], 0))
        self.round_key = boundary_key
        return True

    def mark_committed(self, observation_id: str) -> None:
        for track in self.tracks:
            if track.observation_id == observation_id:
                track.committed = True
                track.rejected = False

    def mark_rejected(self, observation_id: str) -> None:
        for track in self.tracks:
            if track.observation_id == observation_id:
                track.rejected = True

    def ingest(self, frame_index: int, video_time_ms: int,
               detections: Sequence[Detection]) -> List[PhysicalTrack]:
        live = [t for t in self.tracks if not t.rejected]
        pairs = self._associate(live, detections)
        used_tracks = set()
        used_dets = set()
        for track_i, det_i, _score in pairs:
            track = live[track_i]
            det = detections[det_i]
            used_tracks.add(track_i)
            used_dets.add(det_i)
            moved = det.region_id != track.region_id
            track.last_seen_frame = frame_index
            track.last_seen_ms = video_time_ms
            track.bbox = dict(det.bbox)
            track.face_state = det.face_state
            track.rank_hint = det.rank_hint
            track.occluded = False
            if moved:
                track.moved = True
                track.region_id = det.region_id
                track.seat_hint = det.seat_hint
                track.hand_hint = det.hand_hint
            track.history.append({
                "frame": frame_index, "ms": video_time_ms,
                "bbox": dict(det.bbox), "region_id": det.region_id, "moved": moved,
            })
        for track_i, track in enumerate(live):
            if track_i not in used_tracks:
                track.occluded = True
        for det_i, det in enumerate(detections):
            if det_i in used_dets:
                continue
            self._seq += 1
            seed = f"{frame_index}:{video_time_ms}:{self._seq}:{det.region_id}:{det.bbox}:{det.crop_sha256}"
            if self.round_key != "initial":
                seed = f"round:{self.round_key}:" + seed
            observation_id, visual_track_id = _new_ids(seed)
            self.tracks.append(PhysicalTrack(
                observation_id=observation_id,
                visual_track_id=visual_track_id,
                first_seen_frame=frame_index,
                first_seen_ms=video_time_ms,
                last_seen_frame=frame_index,
                last_seen_ms=video_time_ms,
                region_id=det.region_id,
                seat_hint=det.seat_hint,
                hand_hint=det.hand_hint,
                bbox=dict(det.bbox),
                face_state=det.face_state,
                rank_hint=det.rank_hint,
                history=[{"frame": frame_index, "ms": video_time_ms,
                          "bbox": dict(det.bbox), "region_id": det.region_id, "moved": False}],
            ))
        return list(self.tracks)

    def _associate(self, tracks: Sequence[PhysicalTrack],
                   detections: Sequence[Detection]) -> List[Tuple[int, int, float]]:
        scored: List[Tuple[float, int, int]] = []
        for ti, track in enumerate(tracks):
            for di, det in enumerate(detections):
                iou = bbox_iou(track.bbox, det.bbox)
                dist = _distance(track.bbox, det.bbox)
                same_region = track.region_id == det.region_id
                if iou >= self.iou_min or (same_region and dist <= self.max_center_distance):
                    score = iou * 10.0 + (1.0 if same_region else 0.0) - dist / 1000.0
                    scored.append((score, ti, di))
                elif dist <= self.max_center_distance * 2.5:
                    # 分牌移位：区域变了仍可用中心距离续上，避免当成新发牌。
                    scored.append((iou + 0.05 - dist / 2000.0, ti, di))
        scored.sort(reverse=True)
        used_t, used_d = set(), set()
        pairs = []
        for score, ti, di in scored:
            if ti in used_t or di in used_d:
                continue
            used_t.add(ti)
            used_d.add(di)
            pairs.append((ti, di, score))
        return pairs

    def ledger_candidates(self) -> Iterable[PhysicalTrack]:
        return [t for t in self.tracks if t.may_write_ledger()]

    def apply_to_result(self, result: RecognitionResult, frame_index: int,
                        video_time_ms: int) -> RecognitionResult:
        self.ingest(frame_index, video_time_ms, detections_from_observations(result.observations))
        rewritten = []
        used = set()
        for obs in result.observations:
            best_i, best_iou = None, -1.0
            for index, track in enumerate(self.tracks):
                if index in used or track.last_seen_frame != frame_index or track.occluded:
                    continue
                score = bbox_iou(track.bbox, obs.bbox)
                if score > best_iou:
                    best_i, best_iou = index, score
            if best_i is None:
                continue
            used.add(best_i)
            track = self.tracks[best_i]
            obs.observation_id = track.observation_id
            obs.frame_index = frame_index
            obs.relative_time_ms = video_time_ms
            if track.moved and "归属已移动，确认时不要当新发牌" not in obs.notes:
                obs.notes = list(obs.notes) + ["归属已移动，确认时不要当新发牌"]
            rewritten.append(obs)
        result.observations = rewritten
        extra = list(result.warnings)
        extra.append("跨帧同一物理牌共用 observation_id；重复播放已确认观察不得再入账。")
        extra.append("空牌区或时间间隔不能证明新一局；新局请先在主窗开轮，再人工绑定当前轮。")
        result.warnings = extra
        return result
