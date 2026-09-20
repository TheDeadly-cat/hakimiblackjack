"""One experimental RGB index detector; optional torch is loaded explicitly.

Native-resolution stride-4 center/size prediction on a pretrained truncated
MobileNetV3-small backbone. It never calls the white-body/hole extractor.
"""
from __future__ import annotations

ARCHITECTURE = "mobilenet-v3-small-s4-rgb-index-1"
STRIDE = 4
FULL_FRAME_POLICY = 'native-full-frame-1'
TILED_POLICY = 'native-tiles-320-step160-1'
TILE_SIZE = 320
TILE_STEP = 160


def detector_training_sources(manifest):
    """Read both legacy and explicit multi-source metadata without dropping a source."""
    primary=manifest.get('source_sha256')
    sources=manifest.get('training_source_sha256s')
    if sources is None:sources=[primary]
    if (not isinstance(sources,list) or not sources
            or any(not isinstance(s,str) or not s for s in sources)
            or len(set(sources))!=len(sources)
            or (primary is not None and primary not in sources)):
        raise ValueError('Invalid detector training source identities')
    return frozenset(sources)


def create_model(*, pretrained=False):
    import torch
    from torch import nn
    import torch.nn.functional as F
    from torchvision.models import mobilenet_v3_small, MobileNet_V3_Small_Weights

    class RgbIndexModel(nn.Module):
        def __init__(self):
            super().__init__()
            weights = MobileNet_V3_Small_Weights.IMAGENET1K_V1 if pretrained else None
            self.backbone = mobilenet_v3_small(weights=weights).features[:9]
            self.projections = nn.ModuleList(nn.Conv2d(c, 32, 1) for c in (16,24,48))
            self.head = nn.Sequential(nn.Conv2d(32,32,3,padding=1), nn.BatchNorm2d(32),nn.SiLU(),
                                      nn.Conv2d(32,32,3,padding=1), nn.BatchNorm2d(32),nn.SiLU())
            self.center = nn.Conv2d(32,1,1)
            self.size = nn.Conv2d(32,2,1)
            self.offset = nn.Conv2d(32,2,1)
            nn.init.constant_(self.center.bias,-2.19)
            nn.init.zeros_(self.size.weight)
            nn.init.constant_(self.size.bias,1.8)
            nn.init.zeros_(self.offset.weight)
            nn.init.zeros_(self.offset.bias)
            self.register_buffer("mean",torch.tensor([.485,.456,.406]).view(1,3,1,1))
            self.register_buffer("std",torch.tensor([.229,.224,.225]).view(1,3,1,1))

        def train(self, mode=True):
            super().train(mode)
            # Preserve pretrained BN population estimates during small local batches.
            for layer in self.backbone.modules():
                if isinstance(layer,nn.BatchNorm2d): layer.eval()
            return self

        def forward(self, rgb):
            x=(rgb-self.mean)/self.std
            levels=[]
            for i, layer in enumerate(self.backbone):
                x=layer(x)
                if i in (1,3,8):levels.append(x)
            size=levels[0].shape[-2:]
            fused=sum(F.interpolate(proj(level),size=size,mode='bilinear',align_corners=False)
                      for proj,level in zip(self.projections,levels))
            h=self.head(fused)
            return self.center(h),self.size(h),self.offset(h)

    return RgbIndexModel()


def make_targets(width, height, positives, negatives=(), *, complete=False):
    """Unknown pixels have zero loss weight, never implicit background labels."""
    import numpy as np
    gh,gw=height//STRIDE,width//STRIDE
    hm=np.zeros((1,gh,gw),dtype=np.float32)
    valid=np.full_like(hm,float(complete))
    wh=np.zeros((2,gh,gw),dtype=np.float32)
    offset=np.zeros_like(wh)
    reg=np.zeros_like(hm)
    yy,xx=np.mgrid[:gh,:gw]
    def region(box):
        x,y,w,h=box
        return ((xx+.5)*STRIDE>=x)&((xx+.5)*STRIDE<x+w)&((yy+.5)*STRIDE>=y)&((yy+.5)*STRIDE<y+h)
    for box in negatives:valid[0,region(box)]=1
    for box in positives:
        x,y,w,h=box
        cx,cy=(x+w/2)/STRIDE,(y+h/2)/STRIDE
        ix,iy=int(cx),int(cy)
        if not (0<=ix<gw and 0<=iy<gh):continue
        if reg[0,iy,ix]:raise ValueError("Two labelled indices share one detector center cell")
        valid[0,region(box)]=1
        gaussian=np.exp(-((xx-ix)**2+(yy-iy)**2)/(2*1.0**2))
        hm[0]=np.maximum(hm[0],gaussian)
        hm[0,iy,ix]=valid[0,iy,ix]=reg[0,iy,ix]=1
        wh[:,iy,ix]=np.log([w/STRIDE,h/STRIDE])
        offset[:,iy,ix]=[cx-ix,cy-iy]
    return dict(heatmap=hm,valid=valid,size=wh,offset=offset,regression=reg)


def training_loss(outputs, target):
    import torch
    import torch.nn.functional as F
    logits,size,offset=outputs
    p=logits.sigmoid().clamp(1e-5,1-1e-5)
    truth,valid=target['heatmap'],target['valid']
    positives=(truth==1).float()
    negative=(truth<1).float()*valid
    normalizer=positives.sum().clamp(min=1)
    focal=(-(p.log())*(1-p).pow(2)*positives
           -(1-p).log()*p.pow(2)*(1-truth).pow(4)*negative).sum()/normalizer
    mask=target['regression']
    sizes=(F.smooth_l1_loss(size,target['size'],reduction='none')*mask).sum()/normalizer
    offsets=(F.l1_loss(offset.sigmoid(),target['offset'],reduction='none')*mask).sum()/normalizer
    return focal+.5*sizes+offsets,dict(center=focal.detach(),size=sizes.detach(),offset=offsets.detach())


def predict_boxes(model, rgb, *, device, threshold=.35, max_candidates=96):
    """Full native RGB ROI, padded only; copy and synchronization are caller timed."""
    import numpy as np
    import torch
    import torch.nn.functional as F
    from .tracker import bbox_iou
    height,width=rgb.shape[:2]
    if width>2048 or height>1024:raise ValueError("RGB prototype requires a calibrated ROI <=2048x1024")
    tensor=torch.from_numpy(np.ascontiguousarray(rgb.transpose(2,0,1))).unsqueeze(0).to(device).float()/255
    tensor=F.pad(tensor,(0,(-width)%32,0,(-height)%32))
    with torch.inference_mode():
        logits,size,offset=model(tensor)
        score=logits.sigmoid()
        maxima=(score==F.max_pool2d(score,3,stride=1,padding=1))&(score>=threshold)
        ys,xs=torch.where(maxima[0,0])
        # Explicitly include GPU->CPU transfer before returning inference results.
        candidates=torch.stack((xs,ys,score[0,0,ys,xs],size[0,0,ys,xs],size[0,1,ys,xs],
                                offset[0,0,ys,xs].sigmoid(),offset[0,1,ys,xs].sigmoid()),dim=1).cpu().numpy()
    boxes=[]
    for ix,iy,confidence,lw,lh,ox,oy in sorted(candidates,key=lambda row:-float(row[2])):
        w,h=np.exp(np.clip([lw,lh],-1,4))*STRIDE
        cx,cy=(ix+ox)*STRIDE,(iy+oy)*STRIDE
        if not (0<=cx<width and 0<=cy<height):continue
        x,y=max(0,round(float(cx-w/2))),max(0,round(float(cy-h/2)))
        right,bottom=min(width,round(float(cx+w/2))),min(height,round(float(cy+h/2)))
        box=dict(x=x,y=y,w=right-x,h=bottom-y)
        if box['w']<1 or box['h']<1:continue
        if any(bbox_iou(box,row['bbox'])>.3 for row in boxes):continue
        boxes.append(dict(bbox=box,score=float(confidence),score_is_calibrated_probability=False))
        if len(boxes)>=max_candidates:break
    return boxes


def native_tiles(width,height):
    """Fixed grid, independent of detections, labels, ranks or future frames.

    Interior centers have at least 80px of context, matching the training
    sampler's 70px minimum. Boundary tiles retain original source edges.
    """
    if not TILE_SIZE<=width<=2048 or not TILE_SIZE<=height<=1024:
        raise ValueError('Native tiled detector requires a ROI within 320..2048 by 320..1024')
    def axis(length):
        starts=list(range(0,length-TILE_SIZE+1,TILE_STEP))
        if starts[-1]!=length-TILE_SIZE:starts.append(length-TILE_SIZE)
        boundaries=[0]+[(a+b)/2+TILE_SIZE/2 for a,b in zip(starts,starts[1:])]+[length]
        return [(start,boundaries[i],boundaries[i+1]) for i,start in enumerate(starts)]
    return [{'x':x,'y':y,'owner':(left,top,right,bottom)}
            for y,top,bottom in axis(height) for x,left,right in axis(width)]


def predict_boxes_tiled(model,rgb,*,device,threshold=.35,max_candidates=96,stats=None):
    """Batch fixed native crops; globally deduplicate before temporal tracking."""
    import numpy as np
    import torch
    import torch.nn.functional as F
    from .tracker import bbox_iou
    height,width=rgb.shape[:2];tiles=native_tiles(width,height)
    candidates=[];all_peaks=0
    for start in range(0,len(tiles),8):
        chunk=tiles[start:start+8]
        images=np.stack([rgb[t['y']:t['y']+TILE_SIZE,t['x']:t['x']+TILE_SIZE] for t in chunk])
        tensor=torch.from_numpy(np.ascontiguousarray(images.transpose(0,3,1,2))).to(device).float()/255
        with torch.inference_mode():
            logits,size,offset=model(tensor)
            scores=logits.sigmoid()
            maxima=(scores==F.max_pool2d(scores,3,stride=1,padding=1))&(scores>=threshold)
            for index,tile in enumerate(chunk):
                ys,xs=torch.where(maxima[index,0])
                values=torch.stack((xs,ys,scores[index,0,ys,xs],size[index,0,ys,xs],size[index,1,ys,xs],
                    offset[index,0,ys,xs].sigmoid(),offset[index,1,ys,xs].sigmoid()),dim=1).cpu().numpy()
                all_peaks+=len(values)
                for ix,iy,confidence,lw,lh,ox,oy in values:
                    cx,cy=(ix+ox)*STRIDE+tile['x'],(iy+oy)*STRIDE+tile['y']
                    left,top,right,bottom=tile['owner']
                    if not (left<=cx<right and top<=cy<bottom):continue
                    w,h=np.exp(np.clip([lw,lh],-1,4))*STRIDE
                    x0,y0=max(0,round(float(cx-w/2))),max(0,round(float(cy-h/2)))
                    x1,y1=min(width,round(float(cx+w/2))),min(height,round(float(cy+h/2)))
                    if x1<=x0 or y1<=y0:continue
                    candidates.append({'bbox':dict(x=x0,y=y0,w=x1-x0,h=y1-y0),
                        'score':float(confidence),'score_is_calibrated_probability':False})
    boxes=[]
    for candidate in sorted(candidates,key=lambda row:-row['score']):
        if not any(bbox_iou(candidate['bbox'],old['bbox'])>.3 for old in boxes):boxes.append(candidate)
    if stats is not None:
        stats.update(detector_inference_policy=TILED_POLICY,detector_tile_count=len(tiles),detector_tile_batch_limit=8,
            detector_raw_tile_peaks=all_peaks,detector_owned_peaks=len(candidates),
            detector_deduplicated_boxes=len(boxes),detector_truncated_candidates=max(0,len(boxes)-max_candidates),
            detector_whole_frame_resized=False)
    return boxes[:max_candidates]
