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


def geometry_corrections(rows, document, queue_sha256s, source_sha256):
    """Validate explicit missing-stroke expansions, keeping human rank evidence separate."""
    if (document.get('schema')!='rank-crop-geometry-review-1'
            or document.get('provenance')!='assistant_visual_review'
            or document.get('human_confirmed') is not False
            or document.get('source_sha256')!=source_sha256
            or document.get('queue_sha256s')!=queue_sha256s):
        raise ValueError('Geometry review provenance or queue identity mismatch')
    decisions=document.get('decisions')
    if not isinstance(decisions,list) or not decisions:
        raise ValueError('Geometry review needs explicit decisions')
    originals={row['crop_id']:row for row in rows}
    if len(originals)!=len(rows):raise ValueError('Ambiguous crop identifiers')
    result={}
    for decision in decisions:
        row=originals.get(decision.get('crop_id'))
        if (row is None or row['crop_id'] in result
                or row.get('label_provenance')!='human_reviewed'
                or row['label'] not in ('A','2','3','4','5','6','7','8','9','10','J','Q','K')
                or decision.get('label')!=row['label']
                or decision.get('frame')!=row['frame']
                or decision.get('frame_sha256')!=row['frame_sha256']
                or decision.get('original_bbox')!=row['bbox']
                or not isinstance(decision.get('reason'),str) or not decision['reason'].strip()):
            raise ValueError('Geometry correction must match one original reviewed rank crop')
        box=decision.get('bbox')
        if (not isinstance(box,list) or len(box)!=4 or any(type(v) is not int for v in box)):
            raise ValueError('Geometry correction needs an integer box')
        x,y,w,h=box;ox,oy,ow,oh=row['bbox']
        if (x<0 or y<0 or not 4<=w<=90 or not 8<=h<=60 or box==row['bbox']
                or x>ox or y>oy or x+w<ox+ow or y+h<oy+oh):
            raise ValueError('Geometry correction only expands an existing upper index crop')
        result[row['crop_id']]=dict(decision)
    return result


def expand_reviewed_crop(row, decision, frame_path, output):
    """Recrop original pixels; preserve origin aliases and the original human rank."""
    from blackjack_lab.vision.deps import load_cv2
    from blackjack_lab.vision.real_cards import manual_glyph
    import numpy as np
    cv2=load_cv2()
    if sha(frame_path)!=row['frame_sha256']:raise ValueError('Geometry source frame changed')
    bgr=cv2.imdecode(np.frombuffer(Path(frame_path).read_bytes(),np.uint8),cv2.IMREAD_COLOR)
    if bgr is None:raise ValueError('Unreadable geometry source frame')
    box=decision['bbox'];glyph=manual_glyph(bgr,box);x,y,w,h=box
    identifier=hashlib.sha256(json.dumps([row['source_sha256'],row['frame_sha256'],box]).encode()).hexdigest()[:16]
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    crop_path=output/(identifier+'-crop.png');mask_path=output/(identifier+'-mask.png')
    for path,pixels in ((crop_path,bgr[y:y+h,x:x+w]),(mask_path,glyph.mask)):
        ok,encoded=cv2.imencode('.png',pixels)
        if not ok:raise ValueError('Cannot encode geometry crop')
        with path.open('xb') as stream:stream.write(encoded.tobytes())
    return dict(row,crop_id=identifier,source_crop_id=row['crop_id'],source_bbox=list(row['bbox']),
                origin_crop_id=row.get('origin_crop_id') or row['crop_id'],bbox=list(box),ink=glyph.ink,
                bbox_provenance='assistant_visual_review',geometry_human_confirmed=False,
                geometry_reason=decision['reason'],crop_file=str(crop_path.resolve()),mask_file=str(mask_path.resolve()),
                crop_sha256=sha(crop_path),mask_sha256=sha(mask_path),mask_content_sha256='')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-queue',required=True,type=Path)
    parser.add_argument('--supplement-queue',required=True,type=Path)
    parser.add_argument('--detector',required=True,type=Path)
    parser.add_argument('--annotations',required=True,type=Path)
    parser.add_argument('--supplement-crop-observations',action='store_true',
                        help='Explicitly project crop-only supplemental IDs to observations, preserving original claims')
    parser.add_argument('--geometry-review',type=Path,
                        help='Explicit assistant-reviewed missing-stroke expansions; original human rank labels remain unchanged')
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    if args.output.exists():raise ValueError('Preserve old queues; use a new output directory')
    plan_path=args.detector/'plan.json'
    manifest=json.loads((args.detector/'detector-manifest.json').read_text(encoding='utf-8'))
    plan=json.loads(plan_path.read_text(encoding='utf-8'))
    if len(plan.get('training_source_sha256s',[]))>1:
        raise ValueError('This rank preparation requires a single-source detector selection; do not collapse multi-source review provenance')
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
    geometry_review=None
    if args.geometry_review:
        document=json.loads(args.geometry_review.read_text(encoding='utf-8'))
        corrections=geometry_corrections(rows,document,{k:v['sha256'] for k,v in receipts.items()},source)
        frame_paths={Path(f['path']).name:Path(f['path']) for f in plan['training_frames']}
        rows=[expand_reviewed_crop(row,corrections[row['crop_id']],frame_paths[row['frame']],
                                  args.output/'geometry-assets') if row['crop_id'] in corrections else row for row in rows]
        geometry_review=dict(path=str(args.geometry_review.resolve()),sha256=sha(args.geometry_review),
                             corrections=list(corrections.values()),human_confirmed=False,
                             scope='Assistant geometry only; inherit original human rank and origin alias, not a new human-reviewed box')
    args.output.mkdir(parents=True,exist_ok=True)
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
                geometry_review=geometry_review,
                identity_projection_scope='crop observations only; no new physical-card identity or independence claim',
                source_rank_labels_changed=False,original_files_changed=False,
                scope='Human rank labels retained; upper-box selection and any explicit geometry expansion have separately attributed assistant review. Counts are crops, not independent physical cards.')
    for original in receipts.values():
        if sha(original['path'])!=original['sha256']:raise ValueError('Original queue changed during preparation')
    (args.output/'receipt.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('items','labels','kept_by_queue','training_digest')},ensure_ascii=False))
    return 0


if __name__=='__main__':raise SystemExit(main())
