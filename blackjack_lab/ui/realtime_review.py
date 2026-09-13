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
    frame_index: int | None
    video_time_ms: int | None
    metadata: dict

    @property
    def source_description(self):
        if self.frame_index is not None:
            return f'原录像帧 {self.frame_index} · {self.video_time_ms/1000:.3f} 秒'
        packet = self.metadata['packet']
        title = self.metadata['selected_window']['title']
        return f'窗口 {title} · 采集序号 {packet["frame_id"]}（不是录像帧号）'

    def save(self, root, tracker=None):
        """Save one explicit snapshot, without touching the ledger or source file."""
        root = Path(root)
        folder = root / 'realtime' / self.metadata['snapshot_id']
        folder.mkdir(parents=True, exist_ok=False)
        original = self.result.as_dict()
        if self.frame_index is not None:
            tracker.apply_to_result(self.result, self.frame_index, self.video_time_ms)
        else:
            # A frozen window frame has no decoded-video index or verified
            # physical association with another snapshot. Keep explicit links.
            for obs in self.result.observations:
                key = self.metadata['snapshot_id'] + ':' + obs.observation_id
                obs.observation_id = hashlib.sha256(key.encode()).hexdigest()[:32]
                obs.frame_index = obs.relative_time_ms = None
        EvidenceStore(folder).save_result(self.loaded, self.result)
        prefix = folder.relative_to(root).as_posix()
        for observation in self.result.observations:
            observation.crop_relpath = prefix + '/' + observation.crop_relpath
        self.result.image_path = str(folder / 'source.png')
        self.metadata['original_recognition'] = original
        self.metadata['manual_review_observation_ids'] = [o.observation_id for o in self.result.observations]
        self.metadata['source_png_sha256'] = hashlib.sha256((folder/'source.png').read_bytes()).hexdigest()
        self.metadata['candidates_json_sha256'] = hashlib.sha256((folder/'candidates.json').read_bytes()).hexdigest()
        (folder/'handoff.json').write_text(json.dumps(self.metadata,ensure_ascii=False,indent=2),encoding='utf-8')
        return folder


def load_video_review_snapshot(path):
    """Open saved evidence without decoding the video or running a new model."""
    from ..vision.contracts import result_from_dict
    from ..vision.image_io import LoadedImage, load_image, validate_local_image_path
    path = validate_local_image_path(path)
    folder = path.parent
    if path.name!='handoff.json' or folder.parent.name!='realtime':
        raise VisionBridgeError('请选择已冻结快照目录内的 handoff.json。')
    def read_json(file):
        if file.stat().st_size>20*1024*1024:raise VisionBridgeError('快照元数据超过大小限制。')
        return json.loads(file.read_text(encoding='utf-8'))
    metadata = read_json(path)
    if metadata.get('schema') not in ('realtime-video-manual-handoff-1','realtime-window-manual-handoff-1') or metadata.get('snapshot_id')!=folder.name:
        raise VisionBridgeError('不是受支持的已冻结识别快照。')
    source,candidates = folder/'source.png',folder/'candidates.json'
    for file,key in [(source,'source_png_sha256'),(candidates,'candidates_json_sha256')]:
        if file.resolve().parent!=folder or not metadata.get(key):
            raise VisionBridgeError('快照缺少完整摘要或引用了目录外文件，请查看原图或重新冻结。')
        if file.stat().st_size>20*1024*1024:raise VisionBridgeError('快照文件超过大小限制。')
        if hashlib.sha256(file.read_bytes()).hexdigest()!=metadata[key]:
            raise VisionBridgeError('快照文件与保存时摘要不一致，未导入。')
    image = load_image(source)
    if hashlib.sha256(image.rgb).hexdigest()!=metadata['source_rgb_sha256']:
        raise VisionBridgeError('快照原始像素与保存时不一致。')
    result = result_from_dict(read_json(candidates))
    if (result.model_id!=metadata['model_id'] or result.model_digest!=metadata['model_digest']
            or [o.observation_id for o in result.observations]!=metadata['manual_review_observation_ids']):
        raise VisionBridgeError('快照模型或核对身份不一致。')
    prefix = folder.relative_to(folder.parent.parent).as_posix()
    for obs in result.observations:
        if len(obs.observation_id)!=32 or any(c not in '0123456789abcdef' for c in obs.observation_id):
            raise VisionBridgeError('非法快照观察身份。')
        obs.crop_relpath=prefix+'/crops/'+obs.observation_id+'.png'
    for box in [o.bbox for o in result.observations]+[o['bbox'] for o in result.geometry_review]:
        if (any(type(box[k]) is not int for k in ('x','y','w','h'))
                or box['x']+box['w']>image.width or box['y']+box['h']>image.height):
            raise VisionBridgeError('快照裁片坐标超出原图。')
    result.image_path=str(source)
    loaded=LoadedImage(source,image.width,image.height,result.asset_sha256,image.rgb,image.byte_size,'frozen-review-source')
    if metadata['schema']=='realtime-video-manual-handoff-1':
        index=metadata['source_frame_index']
        if type(index) is not int or index<0:raise VisionBridgeError('快照缺少原录像帧号。')
        video_time_ms=round(metadata['media_time_ns']/1e6)
    else:
        packet=metadata['packet'];window=metadata['selected_window']
        if (packet.get('source_frame_index') is not None or metadata.get('source_frame_index') is not None
                or packet['source_id']!='window:'+str(window['hwnd'])
                or type(window['process_id']) is not int or window['process_id']<=0
                or not isinstance(window['title'],str)
                or type(packet['frame_id']) is not int or packet['frame_id']<1
                or any(o.frame_index is not None or o.relative_time_ms is not None for o in result.observations)):
            raise VisionBridgeError('窗口快照的来源身份或时钟类型不一致。')
        index=video_time_ms=None
    return VideoReviewSnapshot(loaded,result,index,video_time_ms,metadata),folder.parent.parent


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
    loaded,result,metadata = _freeze_result(owner,row)
    metadata.update({'schema':'realtime-video-manual-handoff-1',
        'source_video_sha256':asset.sha256,'source_video_path':str(asset.path),
        'source_frame_index':index,'media_time_ns':packet.media_time_ns})
    return VideoReviewSnapshot(loaded,result,index,asset.time_ms(index),metadata)


def freeze_window_result(owner,row,expected_hwnd,expected_process_id):
    source,packet=owner.source,row.packet
    if (not getattr(source,'is_live',False) or getattr(source,'hwnd',None)!=expected_hwnd
            or getattr(source,'expected_process_id',None)!=expected_process_id
            or packet.source_id!='window:'+str(expected_hwnd) or packet.source_frame_index is not None):
        raise VisionBridgeError('捕获帧不属于明确选择的窗口，不能导入。')
    source.verify_selected_window()
    selected=source.window_info
    if not selected or selected['process_id']!=expected_process_id or selected['hwnd']!=expected_hwnd:
        raise VisionBridgeError('捕获窗口身份已改变，请重新选择。')
    loaded,result,metadata = _freeze_result(owner,row)
    metadata.update({'schema':'realtime-window-manual-handoff-1','selected_window':deepcopy(selected),
        'capture_observed_monotonic_ns':packet.observed_monotonic_ns,
        'clock_scope':'WGC media timestamp and local capture arrival; no decoded-video frame or video-relative time'})
    return VideoReviewSnapshot(loaded,result,None,None,metadata)


def _freeze_result(owner,row):
    if not owner.can_review_result(row):
        raise VisionBridgeError('预览结果已撤回或不属于当前来源，请重新选择画面。')
    packet=row.packet
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
    metadata = {'snapshot_id': hashlib.sha256(identity.encode()).hexdigest()[:32],
        'preview_run_id': owner.run_id, 'row_id': row.row_id, 'packet': packet.as_dict(),
        'source_rgb_sha256': hashlib.sha256(loaded.rgb).hexdigest(),
        'model_id': result.model_id, 'model_digest': result.model_digest,
        'selected_result_timings': dict(row.timings),
        'preview_tracks_for_context_only': deepcopy(row.display_tracks(time.perf_counter_ns())),
        'writes_ledger': False, 'preview_ids_are_ledger_ids': False,
        'note': '冻结选中识别结果的原始图像；非播放器最新帧，不重新推理，不用稳定值改写本帧候选。'}
    return loaded,result,metadata
