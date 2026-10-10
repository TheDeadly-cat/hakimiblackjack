"""Visible point-value/default-main qualification on the existing frozen scales."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts import sidebet_closeout_measure as stages


def identity():
    value=stages.identity()
    value['point_probe_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    return value


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--fixtures',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--batches-per-scale',type=int,default=3)
    parser.add_argument('--smoke',action='store_true')
    args=parser.parse_args()
    assert 1<=args.batches_per_scale<=5
    args.output.mkdir(parents=True,exist_ok=False)
    before=identity()
    from scripts.candidate_startup import prepare_runtime
    assert prepare_runtime()['status']=='available'
    cases=[stages.one_case(args.output/f'{mode}-{scale}-batch-{index}',mode,scale,
               args.fixtures/f'events-{scale}.json',point_value=True,visible=True)
           for mode in ('pragmatic','bclc') for scale in ((1000,) if args.smoke else (1000,5000,10000))
           for index in range(1,args.batches_per_scale+1)]
    assert identity()==before,'Runtime or probe changed during measurement'
    for case in cases:
        scope=case['product_scope']
        assert scope['main_auto_enabled'] and scope['window_visible']
        assert not scope['card_identity_sidebets_allowed'] and not scope['sidebet_dispatches']
        assert scope['old_enabled_true_sidebet_settings_preserved']
    result=dict(schema='point-value-visible-fixed-measurement-v1',source=before,
        batches_per_scale=args.batches_per_scale,
        input_count=sum(c['input_count'] for c in cases),
        available_decision_batches=sum(c['available_decision_batches'] for c in cases),
        cases=[{k:v for k,v in c.items() if k!='raw_component_data'} for c in cases],
        synthetic_only=True,real_time_acceptance=False,
        scope='Visible normal point-value window, main auto enabled, PP/21+3 unavailable, old enabled=true profiles unchanged. Actual A-9/T input and default spawned recorder; same frozen histories. Distinct request/prefix batches repeat synthetic composition and are not statistically independent live situations. No math budget change or validation shortcut.')
    (args.output/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':main()
