"""Score real display receipts against an independent, bounded source reference.

Unknown reference positions stay ungraded. All eligible events remain in the
deadline denominator; a successful-only P95 is explicitly conditional.
"""
from __future__ import annotations
import argparse
from bisect import bisect_left,bisect_right
from collections import Counter,defaultdict
import hashlib,json,math,sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from blackjack_lab.vision.temporal_preview import _assignment
from blackjack_lab.vision.tracker import bbox_iou

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def quantile(values,p=.95):
    if not values:return None
    values=sorted(values);i=(len(values)-1)*p;lo=math.floor(i);hi=math.ceil(i)
    return values[lo]+(values[hi]-values[lo])*(i-lo)

def source_position(event,media_s):
    """Nearest reviewed quarter-second anchor; never a runtime input."""
    if not event['first_readable_interval_s'][0]<=media_s<=event['scored_until_media_s']:
        return None
    q=round(media_s*4)
    p=event['_positions'].get(q)
    if p is None or not p['gradeable']:return None
    return dict(zip(('x','y','w','h'),p['bbox']))

def score(run,reference):
    updates=run.get('display_updates',[])
    if any(abs(u['source_display_scale']-1)>1e-6 for u in updates):
        raise ValueError('Native-size source reference requires native-size preview')
    return _score_timeline(run,reference,run['source_displays'],updates,
                           source_sha256=run['source']['source_sha256'])


def _score_timeline(run,reference,source_displays,display_updates,*,source_sha256):
    """Shared event arithmetic; callers must validate their source/display mapping.

    Direct replay validates native preview scale. WGC validates the separately
    rendered native source and captured-pixel mapping before calling this helper.
    Neither path rewrites the saved run or replaces WinRT timestamps in it.
    """
    if not reference.get('ready_for_scoring') or reference.get('model_outputs_used'):
        raise ValueError('An independently frozen scoring reference is required')
    if source_sha256!=reference['source_sha256']:
        raise ValueError('Source identity mismatch')
    if run.get('writes_ledger') is not False:raise ValueError('This evaluator is preview-only')
    for key in ('evidence_evictions','source_display_evictions','display_update_evictions'):
        if run.get(key,0):raise ValueError(f'Incomplete bounded evidence: {key}')
    displays=sorted(source_displays,key=lambda d:d['media_time_ns'])
    media=[d['media_time_ns']/1e9 for d in displays]
    if not media or media[-1]<reference['playback_last_frame']/reference['source_fps']-.1:
        raise ValueError('Incomplete source playback')
    updates=sorted(display_updates,key=lambda d:d['display_submitted_ns'])
    if not updates:raise ValueError('Actual rendered-state updates are required')
    events=[];result={};by_track=defaultdict(set);unmatched=Counter();unmatched_examples={}
    merged=defaultdict(set);matched_updates=0;ungraded_updates=0;duplicate_matches=[]
    for original in reference['events']:
        e=dict(original);e['_positions']={p['quarter']:p for p in e['quarter_positions']};events.append(e)
        lo,hi=e['first_readable_interval_s']
        before=max(0,bisect_right(media,lo)-1);after=min(len(displays)-1,bisect_left(media,hi))
        onset_lower=displays[before].get('display_request_ns',displays[before]['display_submitted_ns'])
        onset_upper=displays[after]['display_submitted_ns']
        if onset_lower>onset_upper:raise ValueError('Invalid source display interval')
        result[e['physical_id_proposal']]={'event_id':e['physical_id_proposal'],'rank':e['rank'],'cohort':e['cohort'],
            'first_readable_media_interval_s':[lo,hi],
            'first_readable_display_interval_ns':[onset_lower,onset_upper],
            'first_candidate':None,'first_accepted':None,'first_correct_candidate':None,
            'first_stable':None,'first_correct_stable':None,'first_wrong_candidate':None,'first_wrong_stable':None,
            'candidate_correction':None,'stable_correction':None,'track_ids':set(),'wrong_rank_labels':set(),
            'matched_render_updates':0,'unknown_rank_render_updates':0,'simultaneous_duplicate_render_updates':0,
            'first_visible_interval_s':e.get('first_visible_interval_s')}
    def receipt(update,t):
        value={'display_ns':update['display_submitted_ns'],'source_media_s':update['source_media_time_ns']/1e9,
                'row_id':update['row_id'],'track_id':t['track_id'],'observed_rank':t.get('observed_rank'),
                'stable_rank':t.get('stable_rank'),'bbox':t['bbox']}
        for key in ('wgc_source_frame_id','wgc_source_media_time_ns','source_mapping_signature'):
            if key in update:value[key]=update[key]
        return value
    for update in updates:
        s=update['source_media_time_ns']/1e9
        truth=[(e,source_position(e,s)) for e in events]
        truth=[(e,b) for e,b in truth if b is not None]
        tracks=[t for t in update['tracks'] if t.get('current') and t.get('identity_state')!='expired']
        if not truth:ungraded_updates+=1
        costs=[];overlaps=[]
        for e,b in truth:
            ious=[bbox_iou(b,t['bbox']) for t in tracks];overlaps.append(ious)
            costs.append([1-v if v>=.3 else 10. for v in ious]+[1.1]*len(truth))
        matched=set()
        for i,j in _assignment(costs):
            if j>=len(tracks) or overlaps[i][j]<.3:continue
            e,_=truth[i];t=tracks[j];eid=e['physical_id_proposal'];r=result[eid];matched.add(j);matched_updates+=1
            now=receipt(update,t);r['matched_render_updates']+=1;r['track_ids'].add(t['track_id'])
            by_track[t['track_id']].add(eid)
            if r['first_candidate'] is None:r['first_candidate']=now
            observed=t.get('observed_rank');stable=t.get('stable_rank')
            if observed is None:r['unknown_rank_render_updates']+=1
            if observed is not None:
                if r['first_accepted'] is None:r['first_accepted']=now
                if observed==e['rank']:
                    if r['first_correct_candidate'] is None:r['first_correct_candidate']=now
                    if r['first_wrong_candidate'] and r['candidate_correction'] is None:r['candidate_correction']=now
                else:
                    r['wrong_rank_labels'].add(observed)
                    if r['first_wrong_candidate'] is None:r['first_wrong_candidate']=now
            if stable is not None:
                if r['first_stable'] is None:r['first_stable']=now
                if stable==e['rank']:
                    if r['first_correct_stable'] is None:r['first_correct_stable']=now
                    if r['first_wrong_stable'] and r['stable_correction'] is None:r['stable_correction']=now
                elif r['first_wrong_stable'] is None:r['first_wrong_stable']=now
            duplicate=[k for k,v in enumerate(overlaps[i]) if k!=j and v>=.3]
            if duplicate:
                r['simultaneous_duplicate_render_updates']+=1
                duplicate_matches.append({'event_id':eid,'display_ns':update['display_submitted_ns'],
                    'tracks':[t['track_id']]+[tracks[k]['track_id'] for k in duplicate]})
        for j,t in enumerate(tracks):
            if j in matched:continue
            state='stable' if t.get('stable_rank') else 'accepted' if t.get('observed_rank') else 'unknown'
            unmatched[state]+=1
            key=(t['track_id'],state,t.get('stable_rank') or t.get('observed_rank'))
            if key not in unmatched_examples:unmatched_examples[key]=receipt(update,t)
    for eid,r in result.items():
        r['track_ids']=sorted(r['track_ids']);r['wrong_rank_labels']=sorted(r['wrong_rank_labels'])
        r['reference_track_fragments']=max(0,len(r['track_ids'])-1)
        a,b=r['first_readable_display_interval_ns']
        for key in ('first_candidate','first_correct_candidate','first_correct_stable'):
            value=r[key]
            r[key+'_latency_interval_ms']=None if value is None else [max(0,(value['display_ns']-b)/1e6),max(0,(value['display_ns']-a)/1e6)]
        r['outcome']=('correct_stable_observed' if r['first_correct_stable'] else
          'only_wrong_stable' if r['first_wrong_stable'] else 'accepted_never_correct_stable' if r['first_accepted'] else
          'unknown_only' if r['first_candidate'] else 'no_matched_candidate')
    def cohort(rows):
        out={'eligible_events':len(rows),'outcomes':dict(Counter(r['outcome'] for r in rows)),
            'events_with_any_wrong_candidate':sum(r['first_wrong_candidate'] is not None for r in rows),
            'events_with_any_wrong_stable':sum(r['first_wrong_stable'] is not None for r in rows),
            'first_accepted_was_wrong':sum(r['first_accepted'] is not None and r['first_accepted']['observed_rank']!=r['rank'] for r in rows),
            'first_stable_was_wrong':sum(r['first_stable'] is not None and r['first_stable']['stable_rank']!=r['rank'] for r in rows),
            'candidate_corrections':sum(r['candidate_correction'] is not None for r in rows),
            'stable_corrections':sum(r['stable_correction'] is not None for r in rows)}
        for key in ('first_candidate','first_correct_candidate','first_correct_stable'):
            times=[r[key+'_latency_interval_ms'] for r in rows if r[key+'_latency_interval_ms'] is not None]
            out[key]={'observed_events':len(times),'unobserved_events':len(rows)-len(times),
                'conditional_p95_interval_ms':[quantile([v[0] for v in times]),quantile([v[1] for v in times])],
                'deadline_counts':{str(ms):{'verified_within':sum(t[1]<=ms for t in times),
                    'within_if_onset_at_later_bound':sum(t[0]<=ms for t in times),
                    'denominator':len(rows)} for ms in (500,1000,2000)}}
        return out
    rows=list(result.values());arrival=[(r['display_submitted_ns']-r['observed_monotonic_ns'])/1e6 for r in run['rows'] if r.get('display_submitted_ns')]
    stages={}
    for name,start,end in [('queue','arrival','picked_ns'),('preprocess','preprocessing_start_ns','preprocessing_end_ns'),
        ('detect','detection_start_ns','detection_end_ns'),('classify','classification_start_ns','classification_end_ns'),
        ('track','tracking_start_ns','candidate_ready_ns')]:
        values=[]
        for r in run['rows']:
            t=r['timings'];a=r['observed_monotonic_ns'] if start=='arrival' else t.get(start);b=t.get(end)
            if a is not None and b is not None:values.append((b-a)/1e6)
        stages[name]={'median_ms':quantile(values,.5),'p95_ms':quantile(values),'samples':len(values)}
    span=(displays[-1]['display_submitted_ns']-displays[0]['display_submitted_ns'])/1e9
    return {'schema':'real-display-readable-event-score-1','run_id':run['run_id'],'model_digest':run['model_digest'],
        'source_sha256':source_sha256,'temporal_policy':run['temporal_policy'],
        'target_fps':run['target_recognition_fps'],'processed_frames':run['processed_frames'],
        'source_display_span_s':span,'source_media_span_s':media[-1]-media[0],
        'effective_processed_fps':run['processed_frames']/span,'all_events':cohort(rows),
        'new_visibility_events':cohort([r for r in rows if r['cohort']=='new_visibility']),
        'startup_events':cohort([r for r in rows if r['cohort']=='startup_already_visible']),
        'arrival_to_candidate_display':{'median_ms':quantile(arrival,.5),'p95_ms':quantile(arrival),'samples':len(arrival)},
        'stages':stages,'source_counters':run['source'],'events':rows,
        'reference_identity_merges':{k:sorted(v) for k,v in by_track.items() if len(v)>1},
        'events_with_reference_id_fragmentation':sum(r['reference_track_fragments']>0 for r in rows),
        'simultaneous_duplicate_reference_matches':duplicate_matches,
        'matched_track_display_updates':matched_updates,'display_updates_without_gradeable_truth':ungraded_updates,
        'unmatched_track_display_updates':dict(unmatched),'unmatched_track_state_examples':list(unmatched_examples.values()),
        'ownership_error_count':None,'ownership_note':'Current layout is unassigned; physical hand/seat ownership is not emitted and cannot be claimed correct.',
        'automatic_ledger_writes':0,'duplicate_ledger_events':None,
        'manual_runtime_clicks':None,'manual_formal_confirmation_count':0,
        'manual_effort_note':'CLI runner issued no review/confirmation actions. Physical user clicks were not instrumented, so their count is unknown. Reference creation is separate assistant annotation work.',
        'uncertainty':['Assistant source reference, not human-confirmed truth; quarter-second positions and onset intervals.',
         'Unmatched outputs are ungraded, not automatically false. Whole-output precision is unavailable.',
         'Identity counts are lower bounds on gradeable anchors; motion/collection reference gaps remain.',
         'Conditional P95 covers observed matches only. All eligible events remain in deadline denominators.',
         'First reported result is the first verified at gradeable source anchors; results inside ungraded onset/motion intervals may be earlier.',
         'Application display submission is not hardware scanout; phase P95 values are not summed.']}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',required=True,type=Path);parser.add_argument('--reference',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path);args=parser.parse_args()
    result=score(json.loads(args.run.read_text(encoding='utf-8')),json.loads(args.reference.read_text(encoding='utf-8')))
    result.update(run_sha256=sha(args.run),reference_sha256=sha(args.reference))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps({'output':str(args.output),'events':result['all_events']['eligible_events'],
        'new_event_stable':result['new_visibility_events']['first_correct_stable']},ensure_ascii=False))
    return 0

if __name__=='__main__':raise SystemExit(main())
