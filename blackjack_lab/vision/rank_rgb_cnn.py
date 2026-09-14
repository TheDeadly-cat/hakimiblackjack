"""Explicit pretrained RGB-crop classifier experiment, independent of ink masks."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import zipfile

from .deps import ImageRejected,load_numpy,load_cv2
from .glyph_dataset import LABEL_RANKS,JUNK_LABEL
from .rank_classifier import RankGuess
from .rank_cnn import RankCnnClassifier,identity_hash

SCHEMA='rank-cnn-rgb-1'
ARCHITECTURE='mobilenet-v3-small-rgb64-rank-1'
FEATURE_VERSION='native-rgb-letterbox64-imagenet-1'
PATCH_SIZE=64


def rgb_to_patch(rgb):
    """Retain color and grayscale information; normalization adds no source detail."""
    np,cv2=load_numpy(),load_cv2()
    if (not isinstance(rgb,np.ndarray) or rgb.dtype!=np.uint8 or rgb.ndim!=3
            or rgb.shape[2]!=3 or min(rgb.shape[:2])<1 or max(rgb.shape[:2])>512):
        raise ImageRejected('RGB 分类需要非空原生 uint8 三通道裁片，最长边不超过 512')
    h,w=rgb.shape[:2];scale=PATCH_SIZE/max(h,w);nw,nh=max(1,round(w*scale)),max(1,round(h*scale))
    resized=cv2.resize(rgb,(nw,nh),interpolation=cv2.INTER_AREA)
    canvas=np.full((PATCH_SIZE,PATCH_SIZE,3),255,dtype=np.uint8)
    x,y=(PATCH_SIZE-nw)//2,(PATCH_SIZE-nh)//2;canvas[y:y+nh,x:x+nw]=resized
    value=canvas.astype(np.float32)/255
    value=(value-np.array([.485,.456,.406],np.float32))/np.array([.229,.224,.225],np.float32)
    return np.ascontiguousarray(value.transpose(2,0,1))


def create_network(*,pretrained=False):
    from torch import nn
    from torchvision.models import mobilenet_v3_small,MobileNet_V3_Small_Weights
    class RgbRankNetwork(nn.Module):
        def __init__(self):
            super().__init__()
            self.backbone=mobilenet_v3_small(weights=MobileNet_V3_Small_Weights.IMAGENET1K_V1 if pretrained else None).features
            self.pool=nn.AdaptiveAvgPool2d(1)
            self.classifier=nn.Sequential(nn.Flatten(),nn.Linear(576,128),nn.Hardswish(),nn.Dropout(.2),nn.Linear(128,len(LABEL_RANKS)))
        def train(self,mode=True):
            super().train(mode)
            for layer in self.backbone.modules():
                if isinstance(layer,nn.BatchNorm2d):layer.eval()
            return self
        def forward(self,x):return self.classifier(self.pool(self.backbone(x)))
    return RgbRankNetwork()


class RankRgbCnnClassifier(RankCnnClassifier):
    feature_version=FEATURE_VERSION
    batch_version='within-frame-rgb64-mobilenet-1'
    input_kind='native_rgb_crop'

    def __init__(self,network,*,initialization_digest,**kwargs):
        if not isinstance(initialization_digest,str) or len(initialization_digest)!=64 or any(c not in '0123456789abcdef' for c in initialization_digest):
            raise ImageRejected('RGB 分类需要冻结的初始化权重摘要')
        super().__init__(network,**kwargs)
        self.initialization_digest=initialization_digest
        self.model_id='unpersisted-rgb-cnn'

    def predict_masks(self,*args,**kwargs):
        raise ImageRejected('此分类器需要原生 RGB 裁片，不能用二值掩膜代替')

    def predict_rgb_crops(self,crops,*,stats=None):
        import torch
        np=load_numpy();crops=list(crops)
        if not crops:return []
        patches=np.stack([rgb_to_patch(rgb) for rgb in crops])
        with torch.inference_mode():
            probabilities=self.network(torch.from_numpy(patches).to(self.device)).softmax(dim=1).cpu().numpy()
        if probabilities.shape!=(len(crops),len(LABEL_RANKS)) or not np.isfinite(probabilities).all():
            raise ImageRejected('RGB 分类输出非法，未生成候选')
        output=[]
        for crop,values in zip(crops,probabilities):
            if np.all(crop==crop[0,0]):
                output.append(RankGuess(raw_label=JUNK_LABEL,rank=None,score=0.,margin=0.,accepted=False,
                                        rejection_reason='constant_rgb_no_index_detail'))
                continue
            top=np.argsort(-values,kind='stable')[:2];label=LABEL_RANKS[int(top[0])]
            score=float(values[top[0]]);margin=score-float(values[top[1]])
            reason=('junk' if label==JUNK_LABEL else 'rgb_cnn_low_score' if score<self.min_score
                    else 'rgb_cnn_small_margin' if margin<self.min_margin else '')
            output.append(RankGuess(raw_label=label,rank=label if not reason else None,score=score,
                                    margin=margin,accepted=not reason,rejection_reason=reason))
        if stats is not None:
            stats.update(classification_requested_crops=len(crops),classification_computed_crops=len(crops),
                classification_cache_hits=0,classification_deduplicated_crops=0,
                classification_score_kind='uncalibrated_rgb_cnn_softmax',classifier_device=self.device,
                classification_input_kind=self.input_kind)
        return output

    def save(self,directory):
        np=load_numpy();directory=Path(directory);directory.mkdir(parents=True,exist_ok=False)
        arrays={name:value.detach().cpu().numpy() for name,value in self.network.state_dict().items()}
        with (directory/'model.npz').open('xb') as stream:np.savez(stream,**arrays)
        identity={'schema':SCHEMA,'architecture':ARCHITECTURE,'feature_version':FEATURE_VERSION,
            'input_kind':self.input_kind,'class_names':list(LABEL_RANKS),'patch_size':PATCH_SIZE,
            'style_id':self.style_id,'orientation_policy':self.orientation_policy,
            'training_digest':self.training_digest,'plan_digest':self.plan_digest,
            'initialization_digest':self.initialization_digest,'min_score':self.min_score,'min_margin':self.min_margin,
            'blob_sha256':hashlib.sha256((directory/'model.npz').read_bytes()).hexdigest(),
            'label_review_status':self.label_review_status,'score_is_calibrated_probability':False,
            'auto_confirm_enabled':False,'initialization':'torchvision MobileNet_V3_Small_Weights.IMAGENET1K_V1 backbone; random rank head'}
        self.model_id='rank-rgb-cnn-'+identity_hash(identity)
        (directory/'manifest.json').write_text(json.dumps({**identity,'model_id':self.model_id},indent=2),encoding='utf-8')
        return directory

    @classmethod
    def load(cls,directory,*,device='cpu'):
        import torch
        np=load_numpy();directory=Path(directory)
        try:
            raw=(directory/'manifest.json').read_bytes();manifest=json.loads(raw);model_id=manifest.pop('model_id')
            blob_path=directory/'model.npz'
            if blob_path.stat().st_size>16*1024*1024:raise ValueError('RGB model artifact exceeds limit')
            blob=blob_path.read_bytes()
            if (manifest['schema']!=SCHEMA or manifest['architecture']!=ARCHITECTURE
                    or manifest['feature_version']!=FEATURE_VERSION or manifest['patch_size']!=PATCH_SIZE
                    or manifest['input_kind']!=cls.input_kind or manifest['class_names']!=list(LABEL_RANKS)
                    or manifest['orientation_policy']!='upright_upper'
                    or manifest['auto_confirm_enabled'] is not False or manifest['score_is_calibrated_probability'] is not False
                    or manifest['blob_sha256']!=hashlib.sha256(blob).hexdigest()
                    or model_id!='rank-rgb-cnn-'+identity_hash(manifest)):
                raise ValueError('RGB classifier identity mismatch')
            for key in ('training_digest','plan_digest','initialization_digest'):
                value=manifest[key]
                if len(value)!=64 or any(c not in '0123456789abcdef' for c in value):raise ValueError('Invalid RGB data digest')
            with zipfile.ZipFile(io.BytesIO(blob)) as archive:
                if sum(f.file_size for f in archive.infolist())>16*1024*1024:raise ValueError('Expanded RGB artifact exceeds limit')
            network=create_network(pretrained=False);expected=network.state_dict()
            with np.load(io.BytesIO(blob),allow_pickle=False) as arrays:
                if set(arrays.files)!=set(expected):raise ValueError('RGB tensor keys differ')
                state={}
                for name,tensor in expected.items():
                    value=arrays[name]
                    if (value.dtype!=tensor.numpy().dtype or tuple(value.shape)!=tuple(tensor.shape)
                            or not np.isfinite(value).all()):raise ValueError('Invalid RGB tensor')
                    state[name]=torch.from_numpy(value.copy())
            network.load_state_dict(state,strict=True)
            model=cls(network,style_id=manifest['style_id'],training_digest=manifest['training_digest'],
                plan_digest=manifest['plan_digest'],initialization_digest=manifest['initialization_digest'],
                min_score=manifest['min_score'],min_margin=manifest['min_margin'],device=device,
                label_review_status=manifest['label_review_status'])
            model.model_id=model_id
            if raw!=(directory/'manifest.json').read_bytes() or blob!=blob_path.read_bytes():raise ValueError('RGB artifact changed while loading')
            return model
        except (OSError,ValueError,KeyError,TypeError,RuntimeError,zipfile.BadZipFile) as exc:
            raise ImageRejected(f'不能加载指定 RGB 分类器：{exc}') from exc
