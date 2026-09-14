"""Explicit RGB detector selection with the existing immutable rank classifier."""
from __future__ import annotations

import hashlib
import io
import json
import time
from pathlib import Path

from .deps import ImageRejected
from .image_io import rgb_to_bgr
from .model_adapter import TrainedModelAdapter

CROP_PREPROCESSING = 'rgb-box-manual-hsv-v150-s80-1'
NAVY_OTSU_PREPROCESSING = 'rgb-box-navy-b-r18-gray-otsu-1'
INPUT_PROFILE = 'native-roi-1850x520-1'


def extract_index_glyph(bgr,bbox,*,preprocessing=CROP_PREPROCESSING):
    """Explicit native-pixel extraction; never resize or synthesize source detail."""
    from .real_cards import manual_glyph,Glyph,_ink_kind
    glyph=manual_glyph(bgr,bbox)
    if preprocessing==CROP_PREPROCESSING:return glyph
    if preprocessing!=NAVY_OTSU_PREPROCESSING:raise ImageRejected('未知 RGB 裁片预处理')
    from .deps import load_cv2,load_numpy
    cv2,np=load_cv2(),load_numpy()
    x,y,w,h=glyph.bbox;crop=bgr[y:y+h,x:x+w]
    # Same navy color cue as _ink_kind, applied per pixel before local grayscale
    # segmentation. Work on a new gray array so the captured source stays intact.
    channels=crop.astype(np.int16)
    navy=channels[:,:,0]>channels[:,:,2]+18
    gray=cv2.cvtColor(crop,cv2.COLOR_BGR2GRAY);gray[navy]=255
    _,mask=cv2.threshold(gray,0,255,cv2.THRESH_BINARY_INV|cv2.THRESH_OTSU)
    mask[navy]=0
    return Glyph(glyph.bbox,mask,_ink_kind(np,crop,mask),int((mask>0).sum()))


def require_calibrated_input(width,height):
    # A calibration boundary, not a claimed universal minimum glyph size.
    if (width,height)!=(1850,520):
        raise ImageRejected(f'RGB 原型当前只验证 1850×520 原始牌桌裁区，实际为 {width}×{height}；'
                            '请恢复已标定的清晰来源后重新播放，不通过插值放大或降低拒识门槛补数。')


class RgbCornerAdapter(TrainedModelAdapter):
    expected_input_size = (1850,520)
    def __init__(self, rank_directory, detector_directory, *, style_id, device='cuda'):
        super().__init__(rank_directory,style_id=style_id,classifier_device=device)
        if self.model.orientation_policy!='upright_upper':
            raise ImageRejected('RGB 上角原型需要明确的正向点数模型')
        self.rank_model_id,self.rank_model_digest=self.model_id,self.digest
        from .rgb_corner_model import ARCHITECTURE,create_model,detector_training_sources
        import torch
        source=Path(detector_directory)
        try:
            raw=(source/'detector-manifest.json').read_bytes()
            manifest=json.loads(raw)
            blob=(source/'detector.pt').read_bytes()
            plan=(source/'plan.json').read_bytes()
            if (manifest['schema']!='rgb-index-detector-1' or manifest['architecture']!=ARCHITECTURE
                    or manifest['style_id']!=style_id or not 0<float(manifest['threshold'])<1
                    or hashlib.sha256(blob).hexdigest()!=manifest['checkpoint_sha256']
                    or hashlib.sha256(plan).hexdigest()!=manifest['plan_sha256']):
                raise ValueError('Detector identity mismatch')
            detector_training_sources(manifest)
            checkpoint=torch.load(io.BytesIO(blob),map_location='cpu',weights_only=True)
            if checkpoint['architecture']!=ARCHITECTURE:raise ValueError('Architecture mismatch')
            if device not in ('cpu','cuda'):raise ValueError('Explicit device must be cpu or cuda')
            if device=='cuda' and not torch.cuda.is_available():raise ValueError('CUDA unavailable')
            self.detector=create_model(pretrained=False)
            self.detector.load_state_dict(checkpoint['state_dict'],strict=True)
            self.detector.to(device).eval()
        except (OSError,ValueError,KeyError,RuntimeError) as exc:
            raise ImageRejected(f'无法加载指定 RGB 检测器：{exc}') from exc
        self.detector_directory=source.resolve()
        self.detector_manifest=manifest
        self.device,self.threshold=device,float(manifest['threshold'])
        self.crop_preprocessing=CROP_PREPROCESSING
        self.extraction_version=ARCHITECTURE+'+'+CROP_PREPROCESSING+'+'+INPUT_PROFILE
        from .rgb_corner_model import FULL_FRAME_POLICY
        self.inference_policy=FULL_FRAME_POLICY
        self._base_extraction_version=self.extraction_version
        self.model_id='rgb-index-'+manifest['checkpoint_sha256'][:16]+'+'+self.rank_model_id
        self._detector_identity_bytes=raw+b'\0'+blob+b'\0'
        self.digest=self._combined_digest()
        self.corner_policy_version=None
        self.warmup_ms=None
        self._warmed_shapes=set()
        torch.set_num_threads(4)
        self.warmup(1850,520)

    def _combined_digest(self):
        return hashlib.sha256(self._detector_identity_bytes+self.rank_model_digest.encode()+b'\0'+
                              self.extraction_version.encode()).hexdigest()

    def with_rank_model(self,directory,*,classifier_device=None):
        """Explicit classifier comparison sharing the exact resident detector."""
        from copy import copy
        rank=TrainedModelAdapter(directory,style_id=self.style_id,classifier_device=classifier_device or self.device)
        if rank.model.orientation_policy!='upright_upper':raise ImageRejected('RGB 对照需要正向分类器')
        other=copy(self)
        other.directory,other.model=rank.directory,rank.model
        other.feature_version,other.training_digest=rank.feature_version,rank.training_digest
        other.rank_model_id,other.rank_model_digest=rank.model_id,rank.digest
        other.model_id='rgb-index-'+self.detector_manifest['checkpoint_sha256'][:16]+'+'+rank.model_id
        other.digest=other._combined_digest()
        return other

    def with_inference_policy(self,policy):
        """Explicit context experiment, retaining the same detector/rank weights."""
        from copy import copy
        from .rgb_corner_model import FULL_FRAME_POLICY,TILED_POLICY
        if policy not in (FULL_FRAME_POLICY,TILED_POLICY):raise ImageRejected('未知 RGB 输入策略')
        other=copy(self)
        other.inference_policy=policy
        other.extraction_version=self._base_extraction_version+('+'+policy if policy==TILED_POLICY else '')
        other.digest=other._combined_digest()
        if policy==TILED_POLICY:other.warmup(320,320,batch_size=8)
        return other

    def with_crop_preprocessing(self,preprocessing):
        """Compare a declared crop policy with the same detector and rank weights."""
        from copy import copy
        from .rgb_corner_model import ARCHITECTURE,TILED_POLICY
        if preprocessing not in (CROP_PREPROCESSING,NAVY_OTSU_PREPROCESSING):
            raise ImageRejected('未知 RGB 裁片预处理')
        if preprocessing==NAVY_OTSU_PREPROCESSING and self.style_id!='navy-live-felt-v1':
            raise ImageRejected('灰度分割实验仅用于已标定的深蓝牌桌样式')
        other=copy(self);other.crop_preprocessing=preprocessing
        other._base_extraction_version=ARCHITECTURE+'+'+preprocessing+'+'+INPUT_PROFILE
        other.extraction_version=other._base_extraction_version+(
            '+'+TILED_POLICY if other.inference_policy==TILED_POLICY else '')
        other.digest=other._combined_digest()
        return other

    def warmup(self,width,height,*,batch_size=1):
        import torch
        if (width,height,batch_size) in self._warmed_shapes:return
        start=time.perf_counter_ns()
        with torch.inference_mode():
            self.detector(torch.zeros(batch_size,3,((height+31)//32)*32,((width+31)//32)*32,device=self.device))
        if self.device=='cuda':torch.cuda.synchronize()
        self.warmup_ms=(time.perf_counter_ns()-start)/1e6
        self._warmed_shapes.add((width,height,batch_size))

    @property
    def identity_text(self):
        from .rgb_corner_model import TILED_POLICY
        context=' / 原生分块' if self.inference_policy==TILED_POLICY else ''
        if self.crop_preprocessing==NAVY_OTSU_PREPROCESSING:context+=' / 灰度分割实验'
        return (f'RGB 牌角开发原型{context} / {self.device} / digest={self.digest[:16]}　'
                f'点数权重冻结={self.rank_model_id[:25]}　裁片={self.crop_preprocessing}；未通过独立事件验收')

    def recognize(self,loaded,layout,source_declaration,*,timings=None):
        if layout.style_id!=self.style_id:raise ImageRejected('RGB 检测器与布局样式不一致')
        require_calibrated_input(loaded.width,loaded.height)
        from .rgb_corner_model import predict_boxes,predict_boxes_tiled,TILED_POLICY
        import numpy as np
        if timings is not None:timings['detection_start_ns']=time.perf_counter_ns()
        rgb=np.frombuffer(loaded.rgb,dtype=np.uint8).reshape(loaded.height,loaded.width,3)
        if self.inference_policy==TILED_POLICY:
            boxes=predict_boxes_tiled(self.detector,rgb,device=self.device,threshold=self.threshold,stats=timings)
        else:
            boxes=predict_boxes(self.detector,rgb,device=self.device,threshold=self.threshold)
        if timings is not None:timings['crop_preprocessing_start_ns']=time.perf_counter_ns()
        bgr=rgb_to_bgr(loaded)
        # Explicitly versioned native crop policy; the detector supplies the box
        # without requiring a closed white-card contour.
        glyphs=[extract_index_glyph(bgr,[item['bbox'][k] for k in ('x','y','w','h')],
                                   preprocessing=self.crop_preprocessing) for item in boxes]
        if timings is not None:
            timings['crop_preprocessing_end_ns']=time.perf_counter_ns()
            timings['crop_preprocessing_version']=self.crop_preprocessing
            timings['detector_candidate_count']=len(boxes)
            timings['detector_device']=self.device
            timings['detector_warmup_ms']=self.warmup_ms
        # An experimental detector's silence is not proof of an empty table.
        result=self._recognize_glyphs(loaded,layout,source_declaration,glyphs,
            occupied=set(layout.regions),timings=timings)
        for obs,item in zip(result.observations,boxes):obs.notes.append(f'RGB detection score={item["score"]:.4f}; not calibrated')
        result.warnings.append('直接 RGB 上角检测；不翻转下角补数。检测框裁片预处理版本：'+self.crop_preprocessing)
        return result
