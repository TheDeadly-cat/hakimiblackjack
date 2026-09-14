"""Reconcile completed rank reviews with selected upper boxes, preserving originals."""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def as_crop_observation(row):
    """Withdraw an uncertified physical-ID claim without inventing a new card ID."""
    if row.get('physical_identity_confirmed') is True:
        raise ValueError('Cannot withdraw a confirmed physical identity as a crop-only review')
    updated=dict(row)
    updated['source_declared_physical_card_id']=row.get('physical_card_id','')
    updated['physical_card_id']=''
    updated['identity_provenance']='crop_observation_only_not_verified_physical_card'
    updated['physical_identity_confirmed']=False
    # Retain any existing origin alias. Reprojection must not give a copy more votes.
    updated['origin_crop_id']=row.get('origin_crop_id') or row['crop_id']
    return updated


def select_rows(tagged_rows, selected, source_sha256):
    """Keep reviewed junk and exactly the current upper boxes, without relabelling."""
    kept=[];excluded=[];seen=set();found=set()
    for origin,row in tagged_rows:
        if row.get('source_sha256')!=source_sha256 or row.get('label_provenance')!='human_reviewed':
            raise ValueError('Rank update requires human rank labels from the selected training source')
        key=(row['frame'],tuple(row['bbox']))
        if row['label']=='junk' or key in selected:
            if key in selected and (row['label'],row['frame_sha256'])!=selected[key]:
                raise ValueError('Queue rank or frame differs from the current reviewed annotation')
            if key in seen:raise ValueError('Duplicate selected rank crop')
            seen.add(key)
            if key in selected:found.add(key)
            kept.append((origin,dict(row)))
        else:
            excluded.append(dict(queue=origin,crop_id=row['crop_id'],frame=row['frame'],bbox=row['bbox'],
                                 original_label=row['label'],reason='not_in_current_selected_upper_boxes'))
    if found!=set(selected):raise ValueError('Current upper boxes are missing reviewed classifier crops')
    return kept,excluded


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-queue',required=True,type=Path)
    parser.add_argument('--supplement-queue',required=True,type=Path)
    parser.add_argument('--detector',required=True,type=Path)
    parser.add_argument('--annotations',required=True,type=Path)
    parser.add_argument('--supplement-crop-observations',action='store_true',
                        help='Explicitly project crop-only supplemental IDs to observations, preserving original claims')
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    if args.output.exists():raise ValueError('Preserve old queues; use a new output directory')
    plan_path=args.detector/'plan.json'
    manifest=json.loads((args.detector/'detector-manifest.json').read_text(encoding='utf-8'))
    plan=json.loads(plan_path.read_text(encoding='utf-8'))
    annotation=json.loads(args.annotations.read_text(encoding='utf-8'))
    if (args.supplement_crop_observations
            and annotation.get('review_scope')!='new_crop_confirmation_only_not_temporal_truth'):
        raise ValueError('Supplement identity projection requires an explicit crop-only review scope')
    if manifest['plan_sha256']!=sha(plan_path) or plan['train']['annotation_sha256']!=sha(args.annotations):
        raise ValueError('Detector plan or original annotation identity mismatch')
    source=plan['train']['source_sha256']
    if annotation['source_sha256']!=source:raise ValueError('Annotation source mismatch')
    frames={f['file']:f for f in annotation['frames']}
    selected={}
    for frame in plan['training_frames']:
        name=Path(frame['path']).name
        for box in frame['positives']:
            original=frames[name]
            if frame['sha256']!=original['sha256']:raise ValueError('Selected frame changed')
            objects=[o for o in original['objects'] if o['bbox']==box]
            if len(objects)!=1 or objects[0].get('label_provenance')!='human_reviewed':
                raise ValueError('Selected upper box needs one original human rank review')
            obj=objects[0]
            if obj['rank'] not in ('A','2','3','4','5','6','7','8','9','10','J','Q','K'):
                raise ValueError('Selected upper box is not an identifiable rank')
            selected[(name,tuple(box))]=(obj['rank'],frame['sha256'])
    queues={'base':args.base_queue.resolve(),'supplement':args.supplement_queue.resolve()}
    receipts={name:dict(path=str(path),sha256=sha(path)) for name,path in queues.items()}
    tagged=[(name,json.loads(line)) for name,path in queues.items()
            for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    kept,excluded=select_rows(tagged,selected,source)
    rows=[]
    identity_projections=[]
    for origin,row in kept:
        if args.supplement_crop_observations and origin=='supplement':
            identity_projections.append(dict(crop_id=row['crop_id'],
                                             original_physical_card_id=row.get('physical_card_id','')))
            row=as_crop_observation(row)
        root=queues[origin].parent
        for field in ('crop_file','mask_file'):
            path=(root/row[field]).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise ValueError('Reviewed crop/mask is missing or escapes its original queue')
            row[field]=str(path)
        row['split']='train';rows.append(row)
    args.output.mkdir(parents=True)
    queue_path=args.output/'queue.jsonl'
    queue_path.write_text(''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in rows),encoding='utf-8')
    from scripts.train_rank_classifier import _load_inputs,_training_digest
    items,_=_load_inputs(SimpleNamespace(queue=None,train_queue=[queue_path],validation_queue=[],holdout_queue=[]))
    # Verify recorded asset digests and compute missing mask digests, then freeze
    # the actual training content. This does not invent new human review status.
    report=dict(schema='reviewed-rank-update-1',source_sha256=source,queues=receipts,
                detector_plan_sha256=sha(plan_path),annotation_sha256=sha(args.annotations),
                orientation_review=plan.get('orientation_review'),
                queue_sha256=sha(queue_path),training_digest=_training_digest(items),
                labels=dict(Counter(row['label'] for row in rows)),items=len(rows),
                kept_by_queue=dict(Counter(origin for origin,_ in kept)),excluded=excluded,
                supplemental_identity_projections=identity_projections,
                identity_projection_scope='crop observations only; no new physical-card identity or independence claim',
                source_rank_labels_changed=False,original_files_changed=False,
                scope='Human rank labels retained; upper-box selection includes separately attributed assistant orientation review. Counts are crops, not independent physical cards.')
    for original in receipts.values():
        if sha(original['path'])!=original['sha256']:raise ValueError('Original queue changed during preparation')
    (args.output/'receipt.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('items','labels','kept_by_queue','training_digest')},ensure_ascii=False))
    return 0


if __name__=='__main__':raise SystemExit(main())
