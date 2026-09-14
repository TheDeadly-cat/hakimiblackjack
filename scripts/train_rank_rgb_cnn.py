"""One source-bound pretrained RGB experiment; no threshold/checkpoint search."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():raise ValueError('Preserve previous runs; choose a new directory')
    raw=args.plan.read_bytes();plan=json.loads(raw)
    from scripts.train_rank_classifier import _load_inputs,_training_digest,_sha256
    from blackjack_lab.vision.glyph_dataset import labeled_only,LABEL_RANKS,label_counts
    from blackjack_lab.vision.rank_classifier import evaluate_items
    from blackjack_lab.vision.rank_cnn import RankCnnClassifier
    from blackjack_lab.vision.rank_rgb_cnn import ARCHITECTURE,create_network,rgb_to_patch,RankRgbCnnClassifier
    if plan['schema']!='rank-rgb-cnn-experiment-1' or plan['architecture']!=ARCHITECTURE:
        raise ValueError('Unknown RGB experiment')
    expected={'steps':1500,'seed':914,'batch_size':64,'angles':[0,15,-15,30,-30],
              'backbone_learning_rate':.0001,'head_learning_rate':.001,'weight_decay':.001,'label_smoothing':.02}
    if plan['training']!=expected or plan['rejection']!={'min_score':.9,'min_margin':.2}:
        raise ValueError('Recipe differs from declared fixed experiment')
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    if head!=plan['run_head'] or subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip():
        raise ValueError('Training requires the declared clean implementation commit')
    def verify():
        for path,digest in plan['runtime_source_hashes'].items():
            if _sha256(ROOT/path)!=digest:raise ValueError('Implementation changed')
        for artifact in plan['frozen_artifacts']+plan['queues']+[plan['initialization']]:
            if _sha256(artifact.get('path',artifact.get('queue')))!=artifact['sha256']:
                raise ValueError('Frozen input changed')
    verify()
    options=SimpleNamespace(queue=None,train_queue=[],validation_queue=[],holdout_queue=[])
    for source in plan['queues']:getattr(options,source['split']+'_queue').append(source['queue'])
    items,receipts=_load_inputs(options);train=labeled_only(items,split='train')
    if any(i.label_provenance!='human_reviewed' for i in items):raise ValueError('Unreviewed rank label')
    if (len(train)!=296 or _training_digest(train)!=plan['training_digest']
            or set(label_counts(train))!=set(LABEL_RANKS)
            or sorted({i.source_sha256 for i in train})!=plan['training_source_sha256s']):
        raise ValueError('Training data/coverage changed')
    os.environ.setdefault('CUBLAS_WORKSPACE_CONFIG',':4096:8')
    import numpy as np
    import torch
    import torchvision
    from blackjack_lab.vision.deps import load_cv2
    cv2=load_cv2();torch.set_num_threads(4);torch.manual_seed(expected['seed'])
    torch.use_deterministic_algorithms(True);torch.backends.cudnn.benchmark=False
    device=plan['device']
    if device!='cuda' or not torch.cuda.is_available():raise ValueError('Declared CUDA unavailable')
    initializer=Path(plan['initialization']['path']).resolve()
    cache=(Path(torch.hub.get_dir())/'checkpoints/mobilenet_v3_small-047dcff4.pth').resolve()
    if initializer!=cache or not _sha256(cache).startswith('047dcff4'):
        raise ValueError('Use the verified existing pretrained cache; do not download during training')
    patches=[];labels=[]
    for item in train:
        bgr=cv2.imdecode(np.fromfile(item.crop_file,dtype=np.uint8),cv2.IMREAD_COLOR)
        if bgr is None:raise ValueError('Unreadable RGB training crop')
        rgb=cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB);h,w=rgb.shape[:2]
        for angle in expected['angles']:
            matrix=cv2.getRotationMatrix2D(((w-1)/2,(h-1)/2),angle,1)
            # Expanded canvas preserves the original strokes, with white padding.
            c,s=abs(matrix[0,0]),abs(matrix[0,1]);nw,nh=int(np.ceil(w*c+h*s)),int(np.ceil(h*c+w*s))
            matrix[0,2]+=(nw-w)/2;matrix[1,2]+=(nh-h)/2
            rotated=rgb if angle==0 else cv2.warpAffine(rgb,matrix,(nw,nh),flags=cv2.INTER_LINEAR,borderValue=(255,255,255))
            patches.append(rgb_to_patch(rotated));labels.append(LABEL_RANKS.index(item.label))
    x=torch.from_numpy(np.stack(patches)).to(device);y=torch.tensor(labels,dtype=torch.long,device=device)
    counts=np.bincount(labels,minlength=len(LABEL_RANKS))
    weights=torch.tensor([1./counts[label] for label in labels],dtype=torch.float64)
    generator=torch.Generator().manual_seed(expected['seed'])
    network=create_network(pretrained=True).to(device).train()
    optimizer=torch.optim.AdamW([
        {'params':network.backbone.parameters(),'lr':expected['backbone_learning_rate']},
        {'params':network.classifier.parameters(),'lr':expected['head_learning_rate']}],weight_decay=expected['weight_decay'])
    criterion=torch.nn.CrossEntropyLoss(label_smoothing=expected['label_smoothing'])
    args.output.mkdir(parents=True,exist_ok=False);(args.output/'plan.json').write_bytes(raw)
    started=time.perf_counter();losses=[]
    for step in range(expected['steps']):
        index=torch.multinomial(weights,expected['batch_size'],replacement=True,generator=generator).to(device)
        optimizer.zero_grad(set_to_none=True);loss=criterion(network(x[index]),y[index])
        if not torch.isfinite(loss):raise ValueError('Nonfinite RGB training loss')
        loss.backward();optimizer.step()
        if step==0 or (step+1)%100==0:
            row={'step':step+1,'loss':float(loss.detach().cpu()),'elapsed_s':time.perf_counter()-started}
            losses.append(row);print(json.dumps(row),flush=True)
    elapsed=time.perf_counter()-started
    model=RankRgbCnnClassifier(network,style_id=plan['style_id'],training_digest=plan['training_digest'],
        plan_digest=hashlib.sha256(raw).hexdigest(),initialization_digest=plan['initialization']['sha256'],
        device=device,label_review_status='human_reviewed',**plan['rejection'])
    model.save(args.output/'model')
    baseline=RankCnnClassifier.load(plan['baseline_model'],device=device)
    report={'schema':'rank-rgb-cnn-result-1','run_head':head,'plan_sha256':hashlib.sha256(raw).hexdigest(),
        'model_id':model.model_id,'baseline_model_id':baseline.model_id,'parameters':sum(p.numel() for p in network.parameters()),
        'training_seconds':elapsed,'losses':losses,'source_queues':receipts,
        'versions':{'torch':str(torch.__version__),'torchvision':str(torchvision.__version__),'numpy':np.__version__},
        'selection':'One final checkpoint; no validation selection or threshold fitting',
        'scope':'Historical extracted-crop diagnostic, not a fresh independent-session test or end-to-end accuracy',
        'rgb_augmentation':'expanded-canvas upright rotations; no flips, masks, source enhancement or synthetic votes',
        'comparisons':{},'writes_ledger':False,'auto_promote':False}
    for split in ('train','validation','holdout'):
        selected=labeled_only(items,split=split)
        results={name:evaluate_items(classifier,selected,Path()) for name,classifier in [('mask_cnn',baseline),('rgb_cnn',model)]}
        report['comparisons'][split]=results
        print(json.dumps({'split':split,**{name:{k:r[k] for k in ('n_labeled','accepted_correct','accepted_wrong','rejected_identifiable','junk_as_rank','n_invalid')}
            for name,r in results.items()}}),flush=True)
    verify();report['frozen_artifacts_unchanged']=True
    (args.output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0


if __name__=='__main__':raise SystemExit(main())
