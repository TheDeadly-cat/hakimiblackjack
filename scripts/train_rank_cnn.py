"""One frozen local CNN experiment, using the existing reviewed-queue pipeline."""
from __future__ import annotations
import argparse,hashlib,json,os,subprocess,sys,time
from pathlib import Path
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    plan_bytes=args.plan.read_bytes();plan=json.loads(plan_bytes)
    if plan['schema']!='rank-cnn-experiment-1':raise ValueError('Unknown experiment plan')
    if args.output.exists():raise ValueError('Preserve existing experiment; choose a new output directory')
    from scripts.train_rank_classifier import _load_inputs,_training_digest,_sha256
    from blackjack_lab.vision.glyph_dataset import labeled_only,LABEL_RANKS,label_counts
    from blackjack_lab.vision.rank_classifier import RankClassifier,evaluate_items,rotate_mask,mask_to_patch
    from blackjack_lab.vision.rank_cnn import ARCHITECTURE,RankCnnClassifier,create_network
    if plan['architecture']!=ARCHITECTURE:raise ValueError('Architecture differs from frozen plan')
    for source,digest in plan['runtime_source_hashes'].items():
        if _sha256(ROOT/source)!=digest:raise ValueError('Training implementation changed after plan freeze')
    for source in plan['queues']:
        if _sha256(source['queue'])!=source['sha256']:raise ValueError('Reviewed queue changed')
    parameters=plan['training']
    if (parameters['steps']!=1500 or parameters['seed']!=914 or parameters['batch_size']!=64
            or parameters['angles']!=[0,15,-15,30,-30] or parameters['learning_rate']!=.001
            or parameters['weight_decay']!=.001 or parameters['label_smoothing']!=.02
            or plan['rejection']!={'min_score':.9,'min_margin':.2}):
        raise ValueError('This experiment supports only its predeclared recipe')
    options=SimpleNamespace(queue=None,train_queue=[],validation_queue=[],holdout_queue=[])
    for source in plan['queues']:getattr(options,source['split']+'_queue').append(source['queue'])
    items,source_receipts=_load_inputs(options)
    if any(i.label_provenance!='human_reviewed' for i in items):raise ValueError('Unreviewed label in CNN inputs')
    train=labeled_only(items,split='train')
    if _training_digest(train)!=plan['training_digest']:raise ValueError('Training content or provenance changed')
    if len(train)!=270 or set(label_counts(train))!=set(LABEL_RANKS):raise ValueError('Training coverage changed')
    for source in plan['frozen_artifacts']:
        if _sha256(source['path'])!=source['sha256']:raise ValueError('Frozen baseline artifact changed')
    os.environ.setdefault('CUBLAS_WORKSPACE_CONFIG',':4096:8')
    import numpy as np
    import torch
    from blackjack_lab.vision.deps import load_cv2
    cv2=load_cv2();torch.set_num_threads(4);torch.manual_seed(parameters['seed'])
    torch.use_deterministic_algorithms(True);torch.backends.cudnn.benchmark=False
    device=plan['device']
    if device=='cuda' and not torch.cuda.is_available():raise ValueError('Planned CUDA unavailable')
    patches=[];labels=[]
    for item in train:
        mask=cv2.imdecode(np.fromfile(item.mask_file,dtype=np.uint8),cv2.IMREAD_GRAYSCALE)
        if mask is None:raise ValueError('Unreadable training mask')
        for angle in parameters['angles']:
            patches.append(mask_to_patch(rotate_mask(mask,angle)));labels.append(LABEL_RANKS.index(item.label))
    x=torch.from_numpy(np.stack(patches)[:,None]).to(device)
    y=torch.tensor(labels,dtype=torch.long,device=device)
    counts=np.bincount(labels,minlength=len(LABEL_RANKS))
    weights=torch.tensor([1./counts[label] for label in labels],dtype=torch.float64)
    generator=torch.Generator().manual_seed(parameters['seed'])
    network=create_network().to(device).train()
    optimizer=torch.optim.AdamW(network.parameters(),lr=parameters['learning_rate'],weight_decay=parameters['weight_decay'])
    loss_function=torch.nn.CrossEntropyLoss(label_smoothing=parameters['label_smoothing'])
    args.output.mkdir(parents=True,exist_ok=False)
    (args.output/'plan.json').write_bytes(plan_bytes)
    started=time.perf_counter();losses=[]
    for step in range(parameters['steps']):
        index=torch.multinomial(weights,parameters['batch_size'],replacement=True,generator=generator).to(device)
        optimizer.zero_grad(set_to_none=True)
        loss=loss_function(network(x[index]),y[index]);loss.backward();optimizer.step()
        if not torch.isfinite(loss):raise ValueError('Nonfinite training loss')
        if step==0 or (step+1)%100==0:
            row={'step':step+1,'loss':float(loss.detach().cpu()),'elapsed_s':time.perf_counter()-started}
            losses.append(row);print(json.dumps(row),flush=True)
    elapsed=time.perf_counter()-started
    model=RankCnnClassifier(network,style_id=plan['style_id'],training_digest=plan['training_digest'],
        plan_digest=hashlib.sha256(plan_bytes).hexdigest(),device=device,label_review_status='human_reviewed',**plan['rejection'])
    model.save(args.output/'model')  # Freeze the final step before any external evaluation.
    baseline=RankClassifier.load(plan['baseline_model'])
    result={'schema':'rank-cnn-experiment-result-1','plan_sha256':hashlib.sha256(plan_bytes).hexdigest(),
        'run_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'runtime_source_hashes':plan['runtime_source_hashes'],
        'model_id':model.model_id,'baseline_model_id':baseline.model_id,'training_seconds':elapsed,
        'parameters':sum(p.numel() for p in network.parameters()),'losses':losses,'source_queues':source_receipts,
        'versions':{'torch':str(torch.__version__),'numpy':np.__version__},'device':device,
        'selection':'one fixed final checkpoint; no validation or holdout selection','comparisons':{},
        'score_is_calibrated_probability':False,'writes_ledger':False,'auto_promote':False}
    for split in ('train','validation','holdout'):
        selected=labeled_only(items,split=split)
        comparisons={}
        for name,classifier in [('baseline',baseline),('cnn',model)]:
            comparisons[name]=evaluate_items(classifier,selected,Path())
        result['comparisons'][split]=comparisons
        print(json.dumps({'split':split,**{name:{k:r[k] for k in ('n_labeled','accepted_correct','accepted_wrong','rejected_identifiable','junk_as_rank')}
            for name,r in comparisons.items()}}),flush=True)
    for source in plan['frozen_artifacts']:
        if _sha256(source['path'])!=source['sha256']:raise ValueError('Frozen artifact changed during experiment')
    result['frozen_artifacts_unchanged']=True
    (args.output/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0


if __name__=='__main__':raise SystemExit(main())
