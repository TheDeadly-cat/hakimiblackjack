"""Report source-body visibility separately from readable-rank response time."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import statistics


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def interval(value, name):
    if (not isinstance(value, list) or len(value) != 2
            or any(isinstance(v, bool) or not isinstance(v, (int, float))
                   or not math.isfinite(v) or v < 0 for v in value)
            or value[0] > value[1]):
        raise ValueError(f'Invalid {name} interval')
    return value


def summarize(reference, appearance):
    if (not appearance.get('ready_for_wait_scoring')
            or appearance.get('model_outputs_used_for_annotation') is not False):
        raise ValueError('A completed source-image visibility review is required')
    if reference['source_sha256'] != appearance['source_sha256']:
        raise ValueError('Source identity mismatch')
    expected = {e['physical_id_proposal']: e for e in reference['events']}
    items = appearance['items']
    counts = Counter(e['event_id'] for e in items)
    if set(counts) != set(expected) or any(n != 1 for n in counts.values()):
        raise ValueError('Each original readable event needs exactly one visibility entry')
    output = []
    for item in items:
        event = expected[item['event_id']]
        if (item.get('assistant_source_reviewed') is not True or item['rank'] != event['rank']
                or item['cohort'] != event['cohort']
                or item['first_readable_interval_s'] != event['first_readable_interval_s']):
            raise ValueError('Visibility entry differs from its reviewed reference')
        visible = interval(item['first_visible_interval_s'], 'visible')
        readable = interval(event['first_readable_interval_s'], 'readable')
        if readable[1] < visible[0]:
            raise ValueError('Rank cannot be readable before the card is visible')
        output.append({'event_id': item['event_id'], 'rank': item['rank'], 'cohort': item['cohort'],
                       'first_visible_interval_s': visible, 'first_readable_interval_s': readable,
                       'visible_to_readable_wait_interval_s': [max(0, readable[0]-visible[1]),
                                                              readable[1]-visible[0]],
                       'physical_deal_time_known': item.get('physical_deal_time_known', False)})
    unreadable = []
    unknown_ids = set()
    for item in appearance.get('unreadable_at_horizon', []):
        eid = item['event_id']
        if eid in expected or eid in unknown_ids or item['rank'] is not None or item['rank_readable_observed'] is not False:
            raise ValueError('An unreadable card cannot duplicate or invent a readable-rank event')
        unknown_ids.add(eid)
        visible = interval(item['first_visible_interval_s'], 'unreadable body')
        end = item['last_observed_media_s']
        if not isinstance(end, (int, float)) or not math.isfinite(end) or end < visible[1]:
            raise ValueError('Observation horizon precedes visibility')
        unreadable.append({'event_id': eid, 'rank': None, 'first_visible_interval_s': visible,
                           'observed_through_media_s': end,
                           'visible_to_readable_wait_lower_bound_s': end-visible[1],
                           'visible_to_readable_wait_upper_bound_s': None})
    new = [e for e in output if e['cohort'] == 'new_visibility']
    return {'schema': 'source-visibility-wait-report-1', 'source_sha256': reference['source_sha256'],
            'human_confirmed': appearance.get('human_confirmed', False),
            'made_after_model_outcomes_known': appearance.get('made_after_model_outcomes_known', True),
            'readable_events': len(output), 'new_readable_events': len(new),
            'startup_events': len(output)-len(new), 'unreadable_events_at_horizon': len(unreadable),
            'new_event_wait_median_interval_s': ([statistics.median(e['visible_to_readable_wait_interval_s'][i]
                for e in new) for i in (0, 1)] if new else None),
            'events': output, 'unreadable_at_horizon': unreadable,
            'scope': 'Assistant source-image visibility intervals; not physical-table dealing times or model latency.',
            'censoring_note': 'Cards still unreadable at the horizon have no finite upper wait bound and do not join the readable-rank denominator.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('reference', 'appearance', 'evidence-root', 'output'):
        parser.add_argument('--'+name, required=True, type=Path)
    args = parser.parse_args()
    appearance = json.loads(args.appearance.read_text(encoding='utf-8'))
    if sha(args.reference) != appearance.get('reference_sha256'):
        raise ValueError('Appearance review is bound to a different reference artifact')
    root = args.evidence_root.resolve()
    files = appearance.get('reviewed_source_files', {})
    if not files:
        raise ValueError('Reviewed source-image identities are required')
    for relative, digest in files.items():
        path = (root/relative).resolve()
        if not path.is_relative_to(root) or sha(path) != digest:
            raise ValueError(f'Reviewed source image mismatch: {relative}')
    result = summarize(json.loads(args.reference.read_text(encoding='utf-8')), appearance)
    result.update(reference_sha256=sha(args.reference), appearance_sha256=sha(args.appearance),
                  reviewed_source_files=len(files), summarizer_sha256=sha(__file__))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({k: result[k] for k in ('readable_events', 'new_readable_events', 'startup_events',
                                            'unreadable_events_at_horizon', 'new_event_wait_median_interval_s')}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
