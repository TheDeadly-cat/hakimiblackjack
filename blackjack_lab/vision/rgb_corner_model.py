"""One experimental RGB index detector; optional torch is loaded explicitly.

Native-resolution stride-4 center/size prediction on a pretrained truncated
MobileNetV3-small backbone. It never calls the white-body/hole extractor.
"""
from __future__ import annotations

ARCHITECTURE = "mobilenet-v3-small-s4-rgb-index-1"
STRIDE = 4


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
