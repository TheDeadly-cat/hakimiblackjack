"""Freeze a selected video inference frame for the existing manual review flow."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
import time
from pathlib import Path

from ..vision.live_input import frame_to_loaded, layout_for_frame
from ..vision.evidence_store import EvidenceStore
from .vision_bridge import VisionBridgeError


@dataclass
class VideoReviewSnapshot:
    loaded: object
    result: object
    frame_index: int
    video_time_ms: int
    metadata: dict

    def save(self, root, tracker):
        """Save one explicit snapshot, without touching the ledger or source file."""
        root = Path(root)
        folder = root / 'realtime' / self.metadata['snapshot_id']
        folder.mkdir(parents=True, exist_ok=False)
        original = self.result.as_dict()
        tracker.apply_to_result(self.result, self.frame_index, self.video_time_ms)
        EvidenceStore(folder).save_result(self.loaded, self.result)
        prefix = folder.relative_to(root).as_posix()
        for observation in self.result.observations:
            observation.crop_relpath = prefix + '/' + observation.crop_relpath
        self.result.image_path = str(folder / 'source.png')
        self.metadata['original_recognition'] = original
        self.metadata['manual_review_observation_ids'] = [o.observation_id for o in self.result.observations]
        self.metadata['source_png_sha256'] = hashlib.sha256((folder/'source.png').read_bytes()).hexdigest()
        (folder/'handoff.json').write_text(json.dumps(self.metadata,ensure_ascii=False,indent=2),encoding='utf-8')
        return folder


def freeze_video_result(owner, row, expected_source_sha256):
    """No inferred video index, future stable rank, or preview ID becomes a fact."""
    if not owner.can_review_result(row):
        raise VisionBridgeError('预览结果已撤回或不属于当前来源，请重新选择画面。')
    source = owner.source
    asset = getattr(source, 'asset', None)
    packet = row.packet
    if getattr(source, 'is_live', True) or asset is None:
        raise VisionBridgeError('此入口用于已打开的本地录像；窗口捕获尚未绑定人工核对来源。')
    if asset.sha256 != expected_source_sha256 or packet.source_id != 'video:' + asset.sha256:
        raise VisionBridgeError('核对录像与预览来源不一致。')
    index = packet.source_frame_index
    if type(index) is not int or not 0 <= index < asset.frame_count:
        raise VisionBridgeError('缺少确切原录像帧号，不能用接帧序号或猜测的时间换算代替。')
    if not math.isfinite(asset.fps) or asset.fps <= 0 or packet.media_time_ns != round(index / asset.fps * 1e9):
        raise VisionBridgeError('原录像帧号与媒体时刻不一致。')
    if row.recognition.model_digest != owner.adapter.digest or row.recognition.model_id != owner.adapter.model_id:
        raise VisionBridgeError('核对结果的模型已改变。')
    if row.recognition.layout_profile_id != layout_for_frame(owner.style, packet.width, packet.height).layout_profile_id:
        raise VisionBridgeError('核对结果的区域已改变。')
    if packet.layout_version != owner.style.layout_version(packet.width, packet.height):
        raise VisionBridgeError('冻结帧的区域版本与当前样式不一致。')
    loaded = frame_to_loaded(packet)
    if loaded.sha256 != row.recognition.asset_sha256:
        raise VisionBridgeError('识别结果与冻结图像不一致。')
    if row.region_observation is not None:
        digest = hashlib.blake2b(packet.pixels.tobytes(), digest_size=16).hexdigest()
        if digest != row.region_observation['detector_context_digest']:
            raise VisionBridgeError('识别后原始像素发生变化，不能保存为同一证据。')
    result = deepcopy(row.recognition)
    for obs in result.observations:
        if obs.asset_sha256 != loaded.sha256 or obs.model_digest != result.model_digest:
            raise VisionBridgeError('候选的来源或模型与冻结图像不一致。')
    if not owner.can_review_result(row):
        raise VisionBridgeError('冻结期间来源改变，旧结果已撤回。')
    identity = f'{owner.run_id}:{row.row_id}:{loaded.sha256}:{result.model_digest}'
    metadata = {'schema': 'realtime-video-manual-handoff-1',
        'snapshot_id': hashlib.sha256(identity.encode()).hexdigest()[:32],
        'preview_run_id': owner.run_id, 'row_id': row.row_id, 'packet': packet.as_dict(),
        'source_video_sha256': asset.sha256, 'source_video_path': str(asset.path),
        'source_frame_index': index, 'media_time_ns': packet.media_time_ns,
        'source_rgb_sha256': hashlib.sha256(loaded.rgb).hexdigest(),
        'model_id': result.model_id, 'model_digest': result.model_digest,
        'selected_result_timings': dict(row.timings),
        'preview_tracks_for_context_only': deepcopy(row.display_tracks(time.perf_counter_ns())),
        'writes_ledger': False, 'preview_ids_are_ledger_ids': False,
        'note': '冻结选中识别结果的原始图像；非播放器最新帧，不重新推理，不用稳定值改写本帧候选。'}
    return VideoReviewSnapshot(loaded, result, index, asset.time_ms(index), metadata)
