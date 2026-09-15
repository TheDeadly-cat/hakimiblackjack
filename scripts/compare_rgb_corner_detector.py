"""Paired original-frame comparison; partial labels never become precision truth."""
import argparse
import hashlib
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from scripts.train_rgb_corner_detector import read_entries
from blackjack_lab.vision.model_adapter import TrainedModelAdapter
from blackjack_lab.vision.rgb_corner_adapter import RgbCornerAdapter
from blackjack_lab.vision.contracts import LayoutProfile, RegionBox, SOURCE_OBSERVER_VIDEO
from blackjack_lab.vision.image_io import LoadedImage
from blackjack_lab.vision.tracker import bbox_iou
from blackjack_lab.vision.rgb_corner_model import detector_training_sources


def score(observations, targets, ranks):
    edges=sorted(((bbox_iou(box,obs.bbox),i,j) for i,box in enumerate(targets)
                  for j,obs in enumerate(observations)),reverse=True)
    matched={};used=set()
    for overlap,i,j in edges:
        if overlap>=.3 and i not in matched and j not in used:
            matched[i]=j;used.add(j)
    counts=Counter(targets=len(targets),correct=0,wrong=0,rejected=0,missed=0)
    rows=[]
    for i,(box,rank) in enumerate(zip(targets,ranks)):
        obs=observations[matched[i]] if i in matched else None
        observed=obs.accepted_rank() if obs else None
        outcome='missed' if obs is None else 'rejected' if observed is None else 'correct' if observed==rank else 'wrong'
        counts[outcome]+=1
        rows.append(dict(bbox=box,rank=rank,outcome=outcome,observed_rank=observed,
                         prediction_bbox=obs.bbox if obs else None))
    counts['unmatched_outputs_ungraded']=len(observations)-len(used)
    counts['unmatched_accepted_ungraded']=sum(obs.accepted_rank() is not None for j,obs in enumerate(observations) if j not in used)
    return dict(counts=counts,targets=rows,outputs=[dict(bbox=obs.bbox,rank=obs.accepted_rank()) for obs in observations])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bundle',type=Path,required=True)
    p.add_argument('--rank-model',type=Path,required=True)
    p.add_argument('--detector',type=Path,required=True)
    p.add_argument('--sessions',nargs='+',default=['20-21','20-42','21-12'])
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--device',default='cuda',choices=['cuda','cpu'])
    args=p.parse_args()
    if args.output.exists():raise ValueError('Use a new output directory')
    args.output.mkdir(parents=True)
    bundle=json.loads(args.bundle.read_text(encoding='utf-8'))
    adapters=dict(baseline=TrainedModelAdapter(args.rank_model,style_id='navy-live-felt-v1'),
                  rgb=RgbCornerAdapter(args.rank_model,args.detector,style_id='navy-live-felt-v1',device=args.device))
    import cv2
    report=dict(scope='paired partial reviewed upper index targets; not full-event or full-output acceptance',
                classifier_weights_identical=adapters['baseline'].model_id==adapters['rgb'].rank_model_id,
                detector_plan_sha256=adapters['rgb'].detector_manifest['plan_sha256'],sessions=[])
    for title in args.sessions:
        entries,meta=read_entries(bundle,title,[])
        if meta['source_sha256'] in detector_training_sources(adapters['rgb'].detector_manifest):
            raise ValueError('Paired evaluation must not use detector training source')
        spec=next(s for s in bundle['sessions'] if s['title']==title)
        annotation=json.loads(Path(spec['annotations']).read_text(encoding='utf-8'))
        lookup={f['sha256']:{tuple(o['bbox']):o['rank'] for o in f['objects']} for f in annotation['frames']}
        session=dict(meta,frames=[],input_frame_count=meta['frames'],totals={name:Counter() for name in adapters})
        for index,entry in enumerate(entries):
            image=entry['image'];h,w=image.shape[:2];rgb=image.tobytes()
            loaded=LoadedImage(Path(entry['path']),w,h,hashlib.sha256(rgb).hexdigest(),rgb,len(rgb),'original-material')
            layout=LayoutProfile('rgb-paired-native','navy-live-felt-v1',w,h,{'unassigned':RegionBox(0,0,w,h)},felt_kind='navy')
            targets=[dict(zip(('x','y','w','h'),b)) for b in entry['positives']]
            ranks=[lookup[entry['sha256']][tuple(b)] for b in entry['positives']]
            frame=dict(path=entry['path'],sha256=entry['sha256'],results={})
            for name,adapter in adapters.items():
                timings={};start=time.perf_counter_ns()
                result=adapter.recognize(loaded,layout,SOURCE_OBSERVER_VIDEO,timings=timings)
                scored=score(result.observations,targets,ranks)
                scored.update(total_ms=(time.perf_counter_ns()-start)/1e6,timings=timings,
                              model_id=adapter.model_id,model_digest=adapter.digest)
                frame['results'][name]=scored
                session['totals'][name].update(scored['counts'])
                if index<2:
                    canvas=image[:,:,::-1].copy()
                    for target in scored['targets']:
                        x,y,bw,bh=(target['bbox'][k] for k in ('x','y','w','h'))
                        cv2.rectangle(canvas,(x,y),(x+bw,y+bh),(100,230,100),1)
                    for obs in result.observations:
                        x,y,bw,bh=(obs.bbox[k] for k in ('x','y','w','h'))
                        cv2.rectangle(canvas,(x,y),(x+bw,y+bh),(0,160,255),1)
                        cv2.putText(canvas,obs.accepted_rank() or '?',(x,max(10,y-3)),cv2.FONT_HERSHEY_SIMPLEX,.4,(0,160,255),1)
                    cv2.imwrite(str(args.output/f'{title}-{index}-{name}.png'),canvas)
            session['frames'].append(frame)
        report['sessions'].append(session)
        print(json.dumps(dict(title=title,totals=session['totals']),ensure_ascii=False),flush=True)
    (args.output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':main()
