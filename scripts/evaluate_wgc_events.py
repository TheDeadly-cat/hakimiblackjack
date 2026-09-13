"""Grade saved WGC displays against the independently rendered native source.

This is offline analysis of immutable real-run evidence, never a runtime input.
WinRT media time and video media time remain separate in every mapped receipt.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.evaluate_realtime_events import _score_timeline, sha

NATIVE_SIZE = [1850, 520]


def mapped_timeline(run, receipt):
    """Validate ownership, native pixels and clocks without guessing missing frames."""
    if run['source'].get('backend') != 'windows-graphics-capture':
        raise ValueError('A real WGC run is required')
    if any((run.get('error'), run['source'].get('error'), receipt.get('error'))):
        raise ValueError('Failed runs remain diagnostic evidence, not completed event runs')
    if not all(receipt.get(k) is True for k in
               ('source_only_owned_raw_player', 'player_finished', 'capture_finished', 'worker_finished')):
        raise ValueError('A completed owned-source playback and cleanup receipt is required')
    if (receipt.get('owned_pid') != receipt.get('hwnd_owner_pid') or not receipt.get('owned_pid')
            or receipt.get('hwnd') != run['source'].get('window_hwnd')):
        raise ValueError('Owned source window identity mismatch')
    player = receipt['player_report']
    if player.get('backend') != 'existing-video-reader-1x' or player.get('source_sha256') != receipt.get('source_sha256'):
        raise ValueError('An independently identified 1x video source is required')
    if player.get('first_frame') != 0 or player.get('error') or not player.get('finished'):
        raise ValueError('Source playback must cover its declared interval from frame zero')
    if receipt.get('model_digest') != run.get('model_digest') or receipt.get('processed') != len(run['rows']):
        raise ValueError('Receipt does not describe this model/run')
    raw = sorted(receipt['raw_displays'], key=lambda r: r['media_time_ns'])
    if len(raw) < 2 or any(raw[i]['display_submitted_ns'] >= raw[i+1]['display_submitted_ns']
                           or raw[i]['media_time_ns'] >= raw[i+1]['media_time_ns'] for i in range(len(raw)-1)):
        raise ValueError('Source display clocks must be strictly ordered')
    if raw[0]['media_time_ns'] != 0:
        raise ValueError('Missing initial source display')
    media_span = (raw[-1]['media_time_ns'] - raw[0]['media_time_ns']) / 1e9
    wall_span = (raw[-1]['display_submitted_ns'] - raw[0]['display_submitted_ns']) / 1e9
    if media_span <= 0 or abs(wall_span - media_span) > max(.5, media_span * .02):
        raise ValueError('Recorded playback is inconsistent with 1x wall time')

    by_signature = defaultdict(list)
    for frame in raw:
        by_signature[frame['signature']].append(frame)
    captures = {}
    for frame in receipt['capture_frames']:
        if frame['image_size'] != NATIVE_SIZE or list(frame['source_size']) != NATIVE_SIZE:
            raise ValueError('Native WGC source dimensions do not match this reference')
        if frame['frame_id'] in captures:
            raise ValueError('Duplicate captured frame identity')
        captures[frame['frame_id']] = dict(frame)
    full_checks = receipt.get('full_pixel_checks', [])
    if not full_checks:
        raise ValueError('Native captured pixels need nonempty full-hash spot checks')
    for check in full_checks:
        frame = captures.get(check['frame_id'])
        matches = by_signature.get(frame['signature'], []) if frame else []
        if (len(matches) != 1 or check.get('equal') is not True
                or not check.get('actual') or check['actual'] != check.get('expected')
                or check['actual'] != frame.get('full_bgr_sha256')
                or check['actual'] != matches[0].get('full_bgr_sha256')):
            raise ValueError('Native full-pixel check mismatch')

    input_matches = 0
    input_unmapped = []
    for row in run['rows']:
        packet = row['packet']
        if packet['image_size'] != NATIVE_SIZE or list(packet['source_size']) != NATIVE_SIZE:
            raise ValueError('Inference input was resized or cropped differently')
        frame = {'frame_id': packet['frame_id'], 'signature': packet['frame_content_signature'],
                 'observed_ns': row['observed_monotonic_ns'], 'wgc_media_time_ns': packet['media_time_ns']}
        previous = captures.get(frame['frame_id'])
        if previous and any(previous[k] != frame[k] for k in frame):
            raise ValueError('Inference and captured frame metadata disagree')
        captures.setdefault(frame['frame_id'], frame)
        if len(by_signature.get(frame['signature'], [])) == 1:
            input_matches += 1
        else:
            input_unmapped.append(row['row_id'])

    source_metadata_used = 0
    for display in run.get('source_displays', []):
        if 'frame_content_signature' not in display:
            continue  # Older runs keep their mapping gaps; no synthetic signature.
        if display['image_size'] != NATIVE_SIZE or list(display['source_size']) != NATIVE_SIZE:
            raise ValueError('Displayed WGC source dimensions differ from the native reference')
        if display['source_token']['source_id'] != run['source']['source_id']:
            raise ValueError('Displayed source token belongs to another capture')
        frame = {'frame_id': display['frame_id'], 'signature': display['frame_content_signature'],
                 'observed_ns': display['observed_monotonic_ns'], 'wgc_media_time_ns': display['media_time_ns']}
        previous = captures.get(frame['frame_id'])
        if previous and any(previous[k] != frame[k] for k in frame):
            raise ValueError('Source display and captured metadata disagree')
        captures.setdefault(frame['frame_id'], frame)
        source_metadata_used += 1

    updates, unmapped, mapping = [], [], []
    for original in run.get('display_updates', []):
        frame = captures.get(original['source_frame_id'])
        candidates = by_signature.get(frame['signature'], []) if frame else []
        if len(candidates) != 1:
            unmapped.append({'row_id': original['row_id'], 'display_ns': original['display_submitted_ns'],
                             'wgc_source_frame_id': original['source_frame_id'],
                             'reason': 'capture_metadata_missing' if frame is None else 'source_signature_absent_or_ambiguous',
                             'current_tracks': sum(bool(t.get('current')) for t in original['tracks'])})
            continue
        source = candidates[0]
        if original['source_media_time_ns'] != frame['wgc_media_time_ns']:
            raise ValueError('WGC source media timestamp mismatch')
        if frame['observed_ns'] > original['display_submitted_ns']:
            raise ValueError('Result source pixels were captured after their recorded display')
        derived = deepcopy(original)
        derived.update(wgc_source_frame_id=original['source_frame_id'],
                       wgc_source_media_time_ns=original['source_media_time_ns'],
                       source_media_time_ns=source['media_time_ns'],
                       source_mapping_signature=frame['signature'])
        # Preserve the actual result-window scale. The independently captured
        # source was native; the smaller results preview is not enlarged here.
        updates.append(derived)
        mapping.append({'wgc_frame_id': frame['frame_id'], 'raw_frame_id': source['frame_id'],
                        'video_media_time_ns': source['media_time_ns'], 'signature': frame['signature'],
                        'raw_display_receipt_minus_capture_ns': source['display_submitted_ns'] - frame['observed_ns']})
    late_receipts = [m['raw_display_receipt_minus_capture_ns'] for m in mapping
                     if m['raw_display_receipt_minus_capture_ns'] > 0]
    evidence = {'native_source_size': NATIVE_SIZE, 'full_pixel_checks': len(full_checks),
                'source_display_count': len(raw), 'captured_metadata_count': len(receipt['capture_frames']),
                'input_rows_matched': input_matches, 'input_rows_unmapped': input_unmapped,
                'rendered_source_metadata_used': source_metadata_used,
                'mapped_render_updates': len(updates), 'unmapped_render_updates': unmapped,
                'ambiguous_raw_signatures': sum(len(v) > 1 for v in by_signature.values()),
                'raw_display_span_s': wall_span, 'raw_media_span_s': media_span,
                'result_window_scales': sorted({u['source_display_scale'] for u in run.get('display_updates', [])}),
                'max_observed_raw_receipt_lag_ms': max(late_receipts, default=0) / 1e6,
                'mapping': mapping}
    return raw, updates, evidence


def score_wgc(run, receipt, reference):
    displays, updates, evidence = mapped_timeline(run, receipt)
    result = _score_timeline(run, reference, displays, updates, source_sha256=receipt['source_sha256'])
    result.update(schema='wgc-source-readable-event-score-1', wgc_mapping=evidence,
                  manual_runtime_clicks=receipt['tk_mouse_clicks'],
                  manual_runtime_key_events=receipt['tk_key_events'],
                  manual_effort_note='Tk mouse/key events only; not a count of card corrections or formal confirmations.')
    result['uncertainty'].extend([
        'Event onset uses the independently rendered native raw player; result-window scale is preserved separately.',
        'Source mapping requires a unique sampled pixel signature and full-pixel spot checks; signatures are not full hashes.',
        'Unmapped display updates remain ungraded; all eligible events remain in denominators.',
        'Raw source timestamps are after-idle receipts, not hardware scanout. Measured capture/receipt inversions are reported.',
        'Onset intervals do not bound all software callback jitter or ungraded reference positions.',
    ])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('run', 'receipt', 'driver', 'reference', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    receipt = json.loads(args.receipt.read_text(encoding='utf-8'))
    if sha(args.run) != receipt.get('run_sha256') or sha(args.driver) != receipt.get('driver_sha256'):
        raise ValueError('Run/driver bytes do not match the original receipt')
    result = score_wgc(json.loads(args.run.read_text(encoding='utf-8')), receipt,
                       json.loads(args.reference.read_text(encoding='utf-8')))
    result.update(run_sha256=sha(args.run), receipt_sha256=sha(args.receipt), driver_sha256=sha(args.driver),
                  reference_sha256=sha(args.reference), evaluator_sha256=sha(__file__))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'output': str(args.output), 'events': result['all_events']['eligible_events'],
                      'new_event_stable': result['new_visibility_events']['first_correct_stable'],
                      'unmapped_render_updates': len(result['wgc_mapping']['unmapped_render_updates'])}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
