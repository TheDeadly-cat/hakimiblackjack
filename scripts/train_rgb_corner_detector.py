"""One controlled, partial-label RGB detector run. Never trains the rank model."""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def digest(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def read_entries(bundle, title, negative_queues, negative_regions=()):
    import cv2
    import numpy as np
    spec=next(s for s in bundle['sessions'] if s['title']==title)
    root=Path(spec['session'])
    annotation=json.loads(Path(spec['annotations']).read_text(encoding='utf-8'))
    manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    if not annotation['source_sha256']==manifest['source_sha256']==spec['source_sha256']:
        raise ValueError('Annotation and material source identities differ')
    extras={}
    for path in negative_queues:
        for line in Path(path).read_text(encoding='utf-8').splitlines():
            row=json.loads(line)
            if row['source_sha256']==spec['source_sha256'] and row['label']=='junk' and row['label_provenance']=='human_reviewed':
                extras.setdefault(row['frame'],[]).append(row)
    records=list(annotation['frames'])
    known={r['file'] for r in records}
    manifest_frames={r['file']:r for r in manifest['frames']}
    reviewed_regions={}
    for path in negative_regions:
        document=json.loads(Path(path).read_text(encoding='utf-8'))
        if document.get('source_sha256')!=spec['source_sha256']:
            raise ValueError('Negative region source differs from selected training source')
        if document.get('provenance')!='assistant_visual_review' or document.get('human_confirmed') is not False:
            raise ValueError('Explicit negative-region provenance required')
        for row in document['frames']:
            original=manifest_frames[row['file']]
            if original['sha256']!=row['sha256']:raise ValueError('Negative region frame identity mismatch')
            reviewed_regions.setdefault(row['file'],[]).extend(row['regions'])
            if row['file'] not in known:
                records.append(dict(file=row['file'],sha256=row['sha256'],objects=[],complete=False,
                    frame_index=original['frame_id'],elapsed_s=original['elapsed_s']))
                known.add(row['file'])
    entries=[]
    skipped=Counter()
    for frame in records:
        positives=[]
        negatives=[]
        for obj in frame['objects']:
            box=obj['bbox']
            if obj.get('label_provenance')!='human_reviewed' or obj.get('bbox_provenance')=='assistant_upper_corner_crop':
                skipped['unconfirmed']+=1;continue
            if obj['rank']=='junk':negatives.append(box);continue
            if obj['rank'] not in ('A','2','3','4','5','6','7','8','9','10','J','Q','K'):
                skipped['unreadable']+=1;continue
            if obj.get('upper_corner_selection',{}).get('keep') is not True:
                skipped['no_explicit_upper_selection']+=1;continue
            x,y,w,h=box
            if not (4<=w<=90 and 8<=h<=60):
                skipped['outside_index_box_scope']+=1;continue
            positives.append(box)
        for row in extras.get(frame['file'],[]):
            if row['frame_sha256']!=frame['sha256']:raise ValueError('Negative label frame identity mismatch')
            negatives.append(row['bbox'])
        regions=reviewed_regions.get(frame['file'],[])
        for x,y,w,h in regions:
            for px,py,pw,ph in positives:
                if x<px+pw and x+w>px and y<py+ph and y+h>py:
                    raise ValueError('Negative region overlaps a reviewed positive target')
        negatives.extend(regions)
        negatives=[list(b) for b in dict.fromkeys(tuple(b) for b in negatives)]
        complete=bool(frame.get('complete') and frame.get('reviewed_by'))
        if not positives and not negatives and not complete:continue
        path=(root/'frames'/frame['file']).resolve()
        if not path.is_relative_to((root/'frames').resolve()):raise ValueError('Frame path escapes material')
        if digest(path)!=frame['sha256']:raise ValueError('Material frame changed')
        bgr=cv2.imdecode(np.frombuffer(path.read_bytes(),np.uint8),cv2.IMREAD_COLOR)
        if bgr is None:raise ValueError('Unreadable frame')
        image=np.ascontiguousarray(bgr[:,:,::-1])
        entries.append(dict(path=str(path),sha256=frame['sha256'],source_sha256=spec['source_sha256'],
                            positives=positives,negatives=negatives,complete=complete,image=image,
                            frame_index=frame.get('frame_index'),elapsed_s=frame.get('elapsed_s')))
    return entries,dict(title=title,source_sha256=spec['source_sha256'],annotation_sha256=digest(spec['annotations']),
                        frames=len(entries),positives=sum(len(e['positives']) for e in entries),
                        negatives=sum(len(e['negatives']) for e in entries),
                        assistant_negative_regions=sum(len(v) for v in reviewed_regions.values()),skipped=dict(skipped))


def sample_tile(entries, rng, size=320):
    import numpy as np
    from blackjack_lab.vision.rgb_corner_model import make_targets
    empty=[e for e in entries if not e['positives'] and (e['complete'] or e['negatives'])]
    positive=[e for e in entries if e['positives']]
    if empty and rng.random()<.15:
        entry=rng.choice(empty)
        anchor=rng.choice(entry['negatives']) if entry['negatives'] else None
    else:
        entry=rng.choice(positive)
        items=entry['positives'] if not entry['negatives'] or rng.random()<.8 else entry['negatives']
        anchor=rng.choice(items)
    h,w=entry['image'].shape[:2]
    if h<size or w<size:raise ValueError('Training crop requires native material >=320x320')
    if anchor is None:
        left,top=rng.randint(0,w-size),rng.randint(0,h-size)
    else:
        x,y,bw,bh=anchor
        cx=rng.uniform(x,x+bw) if not entry['positives'] else x+bw/2
        cy=rng.uniform(y,y+bh) if not entry['positives'] else y+bh/2
        left=max(0,min(w-size,round(cx-rng.uniform(70,size-70))))
        top=max(0,min(h-size,round(cy-rng.uniform(70,size-70))))
    # Keep only fully visible labels; cut-off labels and unlabelled cards stay unknown.
    def shift(boxes):
        return [[x-left,y-top,bw,bh] for x,y,bw,bh in boxes
                if left<=x and top<=y and x+bw<=left+size and y+bh<=top+size]
    rgb=entry['image'][top:top+size,left:left+size].astype(np.float32)/255
    rgb=np.clip(rgb*rng.uniform(.85,1.15),0,1)
    shifted=shift(entry['positives'])
    complete=entry['complete'] and len(shifted)==len(entry['positives'])
    clipped_negatives=[]
    for x,y,bw,bh in entry['negatives']:
        x0,y0=max(left,x),max(top,y)
        x1,y1=min(left+size,x+bw),min(top+size,y+bh)
        if x1>x0 and y1>y0:clipped_negatives.append([x0-left,y0-top,x1-x0,y1-y0])
    target=make_targets(size,size,shifted,clipped_negatives,complete=complete)
    return rgb.transpose(2,0,1),target


def evaluate(model, entries, device, threshold):
    from blackjack_lab.vision.rgb_corner_model import predict_boxes
    from blackjack_lab.vision.tracker import bbox_iou
    rows=[]
    for entry in entries:
        start=time.perf_counter_ns()
        boxes=predict_boxes(model,entry['image'],device=device,threshold=threshold)
        elapsed=(time.perf_counter_ns()-start)/1e6
        targets=[dict(zip(('x','y','w','h'),b)) for b in entry['positives']]
        pairs=sorted(((bbox_iou(gt,pred['bbox']),i,j) for i,gt in enumerate(targets) for j,pred in enumerate(boxes)),reverse=True)
        matched_t,matched_p=set(),set()
        for iou,i,j in pairs:
            if iou>=.3 and i not in matched_t and j not in matched_p:matched_t.add(i);matched_p.add(j)
        rows.append(dict(path=entry['path'],sha256=entry['sha256'],positive_targets=len(targets),
                         matched_targets=len(matched_t),missed_targets=len(targets)-len(matched_t),
                         predictions=boxes,unmatched_predictions_ungraded=len(boxes)-len(matched_p),
                         detector_ms=elapsed,complete=entry['complete']))
    return dict(scope='partial annotated index localization, not full-frame precision or rank accuracy',
                positive_targets=sum(r['positive_targets'] for r in rows),matched_targets=sum(r['matched_targets'] for r in rows),rows=rows)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bundle',type=Path,required=True)
    p.add_argument('--train-session',required=True)
    p.add_argument('--validation-session',required=True)
    p.add_argument('--negative-queue',action='append',default=[],type=Path)
    p.add_argument('--negative-regions',action='append',default=[],type=Path)
    p.add_argument('--reserved-source',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--steps',type=int,default=1200)
    p.add_argument('--batch-size',type=int,default=12)
    p.add_argument('--seed',type=int,default=913)
    p.add_argument('--threshold',type=float,default=.35)
    p.add_argument('--prepare-only',action='store_true')
    args=p.parse_args()
    if args.output.exists():raise ValueError('Use a new run directory; never overwrite prior evidence')
    if args.steps<1 or args.batch_size<1 or not 0<args.threshold<1:raise ValueError('Invalid training settings')
    bundle=json.loads(args.bundle.read_text(encoding='utf-8'))
    reserved=json.loads(args.reserved_source.read_text(encoding='utf-8'))['sha256']
    chosen=[next(s for s in bundle['sessions'] if s['title']==name)['source_sha256']
            for name in (args.train_session,args.validation_session)]
    if chosen[0]==chosen[1] or reserved in chosen:raise ValueError('Training, validation and reserved source must be disjoint')
    train,train_meta=read_entries(bundle,args.train_session,args.negative_queue,args.negative_regions)
    validation,val_meta=read_entries(bundle,args.validation_session,[])
    if any(e['image'].shape[:2]!=(520,1850) for e in train+validation):
        raise ValueError('This prototype is calibrated to native 1850x520 material only')
    if not train_meta['positives'] or not val_meta['positives']:
        raise ValueError('Training and validation need reviewed upper index targets')
    from blackjack_lab.vision.rgb_corner_model import ARCHITECTURE,create_model,training_loss
    args.output.mkdir(parents=True)
    spec=dict(architecture=ARCHITECTURE,style_id='navy-live-felt-v1',train=train_meta,validation=val_meta,reserved_source_sha256=reserved,
              steps=args.steps,batch_size=args.batch_size,seed=args.seed,threshold=args.threshold,
              initialization='torchvision MobileNet_V3_Small_Weights.IMAGENET1K_V1, truncated features 0..8',
              policy='Only reviewed positive upper boxes, explicit reviewed junk, separately attributed assistant-reviewed empty regions and explicitly complete frames receive loss; all other pixels ignored.',
              classifier_changed=False,whole_roi_resized=False,rotation_or_lower_corner_augmentation=False,
              implementation_sha256={str(path.relative_to(ROOT)):digest(path) for path in
                  (ROOT/'blackjack_lab/vision/rgb_corner_model.py',Path(__file__).resolve())},
              additional_negative_regions=[dict(path=str(path.resolve()),sha256=digest(path)) for path in args.negative_regions],
              training_frames=[{k:v for k,v in e.items() if k!='image'} for e in train])
    (args.output/'plan.json').write_text(json.dumps(spec,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(train=train_meta,validation=val_meta),ensure_ascii=False),flush=True)
    if args.prepare_only:return
    import numpy as np
    import torch
    torch.manual_seed(args.seed);np.random.seed(args.seed)
    if not torch.cuda.is_available():raise RuntimeError('CUDA unavailable; preserve preparation and report the runtime issue')
    torch.set_num_threads(4)
    rng=random.Random(args.seed)
    device='cuda'
    model=create_model(pretrained=True).to(device).train()
    backbone=list(model.backbone.parameters());backbone_ids={id(v) for v in backbone}
    optimizer=torch.optim.AdamW([dict(params=backbone,lr=.0001),dict(params=[v for v in model.parameters() if id(v) not in backbone_ids],lr=.001)],weight_decay=.0001)
    start=time.perf_counter()
    losses=[]
    for step in range(args.steps):
        batch=[sample_tile(train,rng) for _ in range(args.batch_size)]
        rgb=torch.from_numpy(np.stack([v[0] for v in batch])).to(device)
        target={k:torch.from_numpy(np.stack([v[1][k] for v in batch])).to(device) for k in batch[0][1]}
        optimizer.zero_grad(set_to_none=True)
        loss,parts=training_loss(model(rgb),target)
        if not torch.isfinite(loss):raise RuntimeError('Nonfinite training loss')
        loss.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),10);optimizer.step()
        if step%100==0 or step==args.steps-1:
            row=dict(step=step+1,loss=float(loss.detach()),seconds=time.perf_counter()-start)
            losses.append(row);print(json.dumps(row),flush=True)
    model.eval()
    torch.cuda.synchronize()
    checkpoint=args.output/'detector.pt'
    torch.save(dict(architecture=ARCHITECTURE,state_dict=model.cpu().state_dict()),checkpoint)
    model.to(device)
    # Warm once before validation; training and first-load latency are separately reported.
    with torch.inference_mode():model(torch.zeros(1,3,320,320,device=device))
    torch.cuda.synchronize()
    report=evaluate(model,validation,device,args.threshold)
    report.update(detector_sha256=digest(checkpoint),parameter_count=sum(v.numel() for v in model.parameters()),
                  torch_version=torch.__version__,cuda_version=torch.version.cuda,gpu=torch.cuda.get_device_name(),
                  training_trace=losses,threshold_selected_on_holdout=False,rank_model_changed=False)
    (args.output/'validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    manifest=dict(schema='rgb-index-detector-1',architecture=ARCHITECTURE,style_id=spec['style_id'],
                  checkpoint_sha256=digest(checkpoint),plan_sha256=digest(args.output/'plan.json'),
                  threshold=args.threshold,rank_model_changed=False,training_steps=args.steps,
                  source_sha256=train_meta['source_sha256'],reserved_source_sha256=reserved,
                  parameter_count=report['parameter_count'],validation_scope=report['scope'])
    (args.output/'detector-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('rows','training_trace')},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
