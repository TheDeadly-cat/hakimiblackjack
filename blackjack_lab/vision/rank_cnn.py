"""One optional, local upright-mask CNN classifier; never trains while loading."""
from __future__ import annotations

import hashlib
import io
import json
import math
from pathlib import Path

from .deps import ImageRejected,load_numpy
from .glyph_dataset import LABEL_RANKS,JUNK_LABEL
from .rank_classifier import RankGuess,mask_to_patch

SCHEMA='rank-cnn-mask-1'
ARCHITECTURE='upright-mask-cnn-16-24-32-64-v1'
FEATURE_VERSION='existing-mask-to-patch32-cnn-v1'


def create_network():
    from torch import nn
    return nn.Sequential(
        nn.Conv2d(1,16,3,padding=1),nn.ReLU(),nn.MaxPool2d(2),
        nn.Conv2d(16,24,3,padding=1),nn.ReLU(),nn.MaxPool2d(2),
        nn.Conv2d(24,32,3,padding=1),nn.ReLU(),nn.AvgPool2d(2),
        nn.Flatten(),nn.Linear(32*4*4,64),nn.ReLU(),nn.Dropout(.15),nn.Linear(64,len(LABEL_RANKS)))


def identity_hash(identity):
    return hashlib.sha256(json.dumps(identity,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


class RankCnnClassifier:
    orientation_policy='upright_upper'
    feature_version=FEATURE_VERSION
    batch_version='within-frame-cnn-mask32-1'

    def __init__(self,network,*,style_id,training_digest,plan_digest,min_score=.90,min_margin=.20,device='cpu',label_review_status='unverified'):
        import torch
        if device not in ('cpu','cuda') or (device=='cuda' and not torch.cuda.is_available()):
            raise ImageRejected('CNN 需要明确可用的 cpu 或 cuda 设备')
        if (not math.isfinite(min_score) or not 0<min_score<1
                or not math.isfinite(min_margin) or not 0<=min_margin<1):
            raise ImageRejected('非法 CNN 拒识门槛')
        self.network=network.to(device).eval();self.device=device
        self.style_id,self.training_digest,self.plan_digest=style_id,training_digest,plan_digest
        self.min_score,self.min_margin=float(min_score),float(min_margin)
        if label_review_status not in ('unverified','human_reviewed','synthetic'):raise ImageRejected('非法训练标签状态')
        self.label_review_status=label_review_status;self.model_id='unpersisted-cnn'

    def predict_masks(self,masks,*,angles=None,stats=None):
        import torch
        np=load_numpy();masks=list(masks)
        if angles is not None and tuple(angles)!=(0,):
            raise ImageRejected('CNN 不进行推理翻转或角度搜索；只处理当前正向裁片')
        if not masks:return []
        if any(mask is None or mask.ndim!=2 or mask.size==0 for mask in masks):
            raise ImageRejected('推理掩膜必须是非空二维图像')
        patches=np.stack([mask_to_patch(mask) for mask in masks])[:,None]
        with torch.inference_mode():
            probabilities=self.network(torch.from_numpy(patches).to(self.device)).softmax(dim=1).cpu().numpy()
        if not np.isfinite(probabilities).all():raise ImageRejected('CNN 输出非有限值，未生成候选')
        output=[]
        for mask,probabilities_row in zip(masks,probabilities):
            if not (mask>0).any():
                output.append(RankGuess(raw_label=JUNK_LABEL,rank=None,score=0.,margin=0.,accepted=False,rejection_reason='empty_ink'))
                continue
            top=np.argsort(-probabilities_row,kind='stable')[:2]
            label=LABEL_RANKS[int(top[0])];score=float(probabilities_row[top[0]])
            margin=score-float(probabilities_row[top[1]])
            reason=('junk' if label==JUNK_LABEL else 'cnn_low_score' if score<self.min_score
                    else 'cnn_small_margin' if margin<self.min_margin else '')
            output.append(RankGuess(raw_label=label,rank=label if not reason else None,
                score=score,margin=margin,accepted=not reason,rejection_reason=reason))
        if stats is not None:
            stats.update(classification_requested_crops=len(masks),classification_computed_crops=len(masks),
                classification_cache_hits=0,classification_deduplicated_crops=0,
                classification_score_kind='uncalibrated_cnn_softmax',classifier_device=self.device)
        return output

    def predict_mask(self,mask,*,angles=None):
        return self.predict_masks([mask],angles=angles)[0]

    def save(self,directory):
        np=load_numpy();directory=Path(directory)
        directory.mkdir(parents=True,exist_ok=False)
        arrays={name:value.detach().cpu().numpy() for name,value in self.network.state_dict().items()}
        with (directory/'model.npz').open('xb') as stream:np.savez(stream,**arrays)
        identity={'schema':SCHEMA,'architecture':ARCHITECTURE,'feature_version':FEATURE_VERSION,
            'class_names':list(LABEL_RANKS),'patch_size':32,'style_id':self.style_id,
            'orientation_policy':self.orientation_policy,'training_digest':self.training_digest,
            'plan_digest':self.plan_digest,'min_score':self.min_score,'min_margin':self.min_margin,
            'blob_sha256':hashlib.sha256((directory/'model.npz').read_bytes()).hexdigest(),
            'label_review_status':self.label_review_status,'score_is_calibrated_probability':False,
            'auto_confirm_enabled':False,'initialization':'random; no external pretrained weights'}
        self.model_id='rank-cnn-'+identity_hash(identity)
        (directory/'manifest.json').write_text(json.dumps({**identity,'model_id':self.model_id},indent=2),encoding='utf-8')
        return directory

    @classmethod
    def load(cls,directory,*,device='cpu'):
        import torch
        np=load_numpy();directory=Path(directory)
        try:
            raw=(directory/'manifest.json').read_bytes();manifest=json.loads(raw)
            model_id=manifest.pop('model_id')
            blob_path=directory/'model.npz'
            if blob_path.stat().st_size>2*1024*1024:raise ValueError('CNN artifact exceeds size limit')
            blob=blob_path.read_bytes()
            if (manifest['schema']!=SCHEMA or manifest['architecture']!=ARCHITECTURE
                    or manifest['feature_version']!=FEATURE_VERSION or manifest['patch_size']!=32
                    or manifest['class_names']!=list(LABEL_RANKS) or manifest['orientation_policy']!='upright_upper'
                    or manifest['label_review_status'] not in ('unverified','human_reviewed','synthetic')
                    or manifest['auto_confirm_enabled'] is not False or manifest['score_is_calibrated_probability'] is not False
                    or manifest['blob_sha256']!=hashlib.sha256(blob).hexdigest()
                    or model_id!='rank-cnn-'+identity_hash(manifest)):
                raise ValueError('CNN identity mismatch')
            for key in ('training_digest','plan_digest'):
                value=manifest[key]
                if len(value)!=64 or any(c not in '0123456789abcdef' for c in value):raise ValueError('Invalid data digest')
            network=create_network();expected=network.state_dict()
            with np.load(io.BytesIO(blob),allow_pickle=False) as arrays:
                if set(arrays.files)!=set(expected):raise ValueError('CNN tensor keys mismatch')
                state={}
                for name,tensor in expected.items():
                    value=arrays[name]
                    if value.dtype!=np.float32 or tuple(value.shape)!=tuple(tensor.shape) or not np.isfinite(value).all():
                        raise ValueError('Invalid CNN tensor')
                    state[name]=torch.from_numpy(value.copy())
            network.load_state_dict(state,strict=True)
            model=cls(network,style_id=manifest['style_id'],training_digest=manifest['training_digest'],
                plan_digest=manifest['plan_digest'],min_score=manifest['min_score'],min_margin=manifest['min_margin'],device=device,
                label_review_status=manifest['label_review_status'])
            model.model_id=model_id
            if raw!=(directory/'manifest.json').read_bytes() or blob!=blob_path.read_bytes():
                raise ValueError('CNN artifact changed during load')
            return model
        except (OSError,ValueError,KeyError,TypeError,RuntimeError) as exc:
            raise ImageRejected(f'不能加载指定 CNN 分类器：{exc}') from exc
