"""Extract exact reviewed boxes from hash-verified frames, preserving all labels."""
from __future__ import annotations
import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))


def prepare(queue,bundle_path,output):
    from blackjack_lab.vision.glyph_dataset import load_queue
    from blackjack_lab.vision.rank_rgb_cnn import INPUT_POLICY
    from scripts.train_rank_classifier import _sha256,_training_digest
    from blackjack_lab.vision.deps import load_cv2,load_numpy
    cv2,np=load_cv2(),load_numpy()
    queue,bundle_path,output=Path(queue).resolve(),Path(bundle_path).resolve(),Path(output)
    if output.exists():raise ValueError('Preserve earlier queue; use a new directory')
    original=load_queue(queue);bundle=json.loads(bundle_path.read_text(encoding='utf-8'))
    sources={s['source_sha256']:s for s in bundle['sessions']}
    frames={};checked=[]
    # Validate every source before producing a new queue.
    for item in original:
        spec=sources[item.source_sha256]
        path=(Path(spec['session'])/'frames'/item.frame).resolve()
        frame_root=(Path(spec['session'])/'frames').resolve()
        if not path.is_relative_to(frame_root):raise ValueError('Frame escaped declared source directory')
        key=(str(path),item.frame_sha256)
        if key not in frames:
            if _sha256(path)!=item.frame_sha256:raise ValueError('Reviewed source frame changed')
            image=cv2.imdecode(np.frombuffer(path.read_bytes(),np.uint8),cv2.IMREAD_COLOR)
            if image is None:raise ValueError('Unreadable source frame')
            frames[key]=image
        x,y,w,h=item.bbox;image=frames[key]
        if x<0 or y<0 or min(w,h)<1 or x+w>image.shape[1] or y+h>image.shape[0]:
            raise ValueError('Reviewed box extends outside source frame')
        for field,digest in (('crop_file',item.crop_sha256),('mask_file',item.mask_sha256)):
            path=(queue.parent/getattr(item,field)).resolve()
            if not path.is_file() or (digest and _sha256(path)!=digest):raise ValueError('Original queue asset changed')
        checked.append((item,image[y:y+h,x:x+w].copy(),key[0]))
    output.mkdir(parents=True,exist_ok=False);(output/'rgb').mkdir()
    result=[];lineage=[]
    for item,pixels,frame_path in checked:
        path=(output/'rgb'/f'{item.crop_id}.png').resolve()
        ok,encoded=cv2.imencode('.png',pixels)
        if not ok:raise ValueError('Cannot encode exact RGB crop')
        path.write_bytes(encoded.tobytes())
        result.append(replace(item,crop_file=str(path),crop_sha256=_sha256(path),
            mask_file=str((queue.parent/item.mask_file).resolve()),extraction_method=INPUT_POLICY))
        lineage.append({'crop_id':item.crop_id,'source_crop_file':str((queue.parent/item.crop_file).resolve()),
            'source_context_sha256':_sha256(queue.parent/item.crop_file),'frame_path':frame_path,
            'frame_sha256':item.frame_sha256,'source_sha256':item.source_sha256,'bbox':list(item.bbox),
            'exact_rgb_sha256':_sha256(path),'label':item.label,'label_provenance':item.label_provenance})
    target=output/'queue.jsonl'
    target.write_text(''.join(json.dumps(i.as_dict(),ensure_ascii=False)+'\n' for i in result),encoding='utf-8')
    receipt={'schema':INPUT_POLICY,'source_queue':str(queue),'source_queue_sha256':_sha256(queue),
        'bundle_sha256':_sha256(bundle_path),'queue_sha256':_sha256(target),'items':len(result),
        'training_digest':_training_digest(result),'lineage':lineage,
        'labels_boxes_origins_unchanged':True,'scope':'Original review context images preserved; new exact raw RGB inputs only. No geometry, rank, orientation or physical identity re-review.'}
    (output/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
    return receipt


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--queue',required=True,type=Path);p.add_argument('--bundle',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path);a=p.parse_args()
    r=prepare(a.queue,a.bundle,a.output);print(json.dumps({k:r[k] for k in ('schema','items','queue_sha256','training_digest')}))
