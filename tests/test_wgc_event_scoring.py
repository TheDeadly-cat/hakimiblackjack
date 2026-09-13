"""Hand-calculated external-source clocks and invalid mapping evidence."""
from copy import deepcopy
import unittest

from scripts.evaluate_realtime_events import score
from scripts.evaluate_wgc_events import score_wgc
from tests import test_realtime_event_scoring as direct_scoring


class WgcEventScoringTests(unittest.TestCase):
    def fixture(self):
        run, reference = direct_scoring.RealtimeEventScoringTests().fixture()
        raw = [dict(d, frame_id=i+1, signature=f'sig-{i}',
                    full_bgr_sha256='a'*64 if i == 6 else None)
               for i, d in enumerate(run['source_displays'])]
        winrt_time = 501_500_000_000
        run['source'] = {'backend': 'windows-graphics-capture', 'window_hwnd': 11, 'source_id': 'window:11'}
        run['source_displays'] = []  # Event onset comes from the external raw player.
        run['display_updates'][0].update(source_frame_id=99, source_media_time_ns=winrt_time,
                                          source_display_scale=.5)
        run['rows'] = [{'row_id': 1, 'observed_monotonic_ns': 2_600_000_000,
                        'display_submitted_ns': 3_000_000_000, 'timings': {},
                        'packet': {'frame_id': 99, 'frame_content_signature': 'sig-6',
                                   'media_time_ns': winrt_time, 'image_size': [1850, 520],
                                   'source_size': [1850, 520]}}]
        capture = {'frame_id': 99, 'signature': 'sig-6', 'observed_ns': 2_600_000_000,
                   'wgc_media_time_ns': winrt_time, 'image_size': [1850, 520],
                   'source_size': [1850, 520], 'full_bgr_sha256': 'a'*64}
        receipt = {'source_only_owned_raw_player': True, 'owned_pid': 7, 'hwnd_owner_pid': 7,
                   'hwnd': 11, 'player_finished': True, 'capture_finished': True, 'worker_finished': True,
                   'source_sha256': 'source', 'model_digest': 'fixture', 'processed': 1,
                   'tk_mouse_clicks': 1, 'tk_key_events': 0,
                   'player_report': {'backend': 'existing-video-reader-1x', 'source_sha256': 'source',
                                     'first_frame': 0, 'finished': True},
                   'raw_displays': raw, 'capture_frames': [capture],
                   'full_pixel_checks': [{'frame_id': 99, 'actual': 'a'*64, 'expected': 'a'*64, 'equal': True}]}
        return run, receipt, reference

    def test_external_source_clock_preserves_scale_winrt_and_all_events(self):
        run, receipt, ref = self.fixture()
        original = deepcopy((run, receipt, ref))
        result = score_wgc(run, receipt, ref)
        stable = result['new_visibility_events']['first_correct_stable']
        self.assertEqual(stable['conditional_p95_interval_ms'], [750, 1000])
        self.assertEqual(stable['deadline_counts']['1000']['denominator'], 3)
        self.assertEqual(result['new_visibility_events']['first_stable_was_wrong'], 1)
        self.assertEqual(result['wgc_mapping']['result_window_scales'], [.5])
        event = result['events'][0]['first_correct_stable']
        self.assertEqual(event['source_media_s'], 1.5)
        self.assertEqual(event['wgc_source_media_time_ns'], 501_500_000_000)
        self.assertEqual(event['wgc_source_frame_id'], 99)
        self.assertEqual(result['manual_runtime_clicks'], 1)
        self.assertEqual((run, receipt, ref), original)
        with self.assertRaises(ValueError):
            score(run, ref)  # Direct replay still rejects the resized preview.

    def test_missing_and_ambiguous_pixel_mappings_stay_ungraded(self):
        run, receipt, ref = self.fixture()
        missing = deepcopy(run['display_updates'][0])
        missing.update(source_frame_id=998, display_submitted_ns=4_000_000_000)
        run['display_updates'].append(missing)
        receipt['raw_displays'][5]['signature'] = 'ambiguous'
        receipt['raw_displays'][7]['signature'] = 'ambiguous'
        receipt['capture_frames'].append({'frame_id': 999, 'signature': 'ambiguous',
            'observed_ns': 2_800_000_000, 'wgc_media_time_ns': 9_000_000_000,
            'image_size': [1850, 520], 'source_size': [1850, 520]})
        ambiguous = deepcopy(missing)
        ambiguous.update(source_frame_id=999, source_media_time_ns=9_000_000_000)
        run['display_updates'].append(ambiguous)
        result = score_wgc(run, receipt, ref)
        mapping = result['wgc_mapping']
        self.assertEqual(mapping['mapped_render_updates'], 1)
        self.assertEqual(len(mapping['unmapped_render_updates']), 2)
        self.assertEqual(mapping['ambiguous_raw_signatures'], 1)
        self.assertEqual(result['all_events']['eligible_events'], 3)
        self.assertEqual(result['all_events']['first_correct_stable']['observed_events'], 1)

    def test_rendered_source_metadata_maps_frames_absent_from_probe_and_inference(self):
        run, receipt, ref = self.fixture()
        second = deepcopy(run['display_updates'][0])
        second.update(source_frame_id=100, source_media_time_ns=502_000_000_000,
                      display_submitted_ns=4_000_000_000)
        run['display_updates'].append(second)
        run['source_displays'].append({'frame_id': 100, 'media_time_ns': 502_000_000_000,
            'observed_monotonic_ns': 3_100_000_000, 'display_submitted_ns': 3_200_000_000,
            'frame_content_signature': 'sig-8', 'image_size': [1850, 520],
            'source_size': [1850, 520], 'source_token': {'source_id': 'window:11'}})
        result = score_wgc(run, receipt, ref)
        self.assertEqual(result['wgc_mapping']['mapped_render_updates'], 2)
        self.assertEqual(result['wgc_mapping']['unmapped_render_updates'], [])
        self.assertEqual(result['wgc_mapping']['rendered_source_metadata_used'], 1)
        run['source_displays'][0]['source_token']['source_id'] = 'window:99'
        with self.assertRaises(ValueError):
            score_wgc(run, receipt, ref)

    def test_inconsistent_source_evidence_fails_instead_of_becoming_fast_success(self):
        changes = [
            lambda r, c: c.update(capture_finished=False),
            lambda r, c: c.update(hwnd_owner_pid=8),
            lambda r, c: c.update(full_pixel_checks=[]),
            lambda r, c: c['capture_frames'][0].update(full_bgr_sha256='b'*64),
            lambda r, c: c['capture_frames'][0].update(image_size=[1180, 282]),
            lambda r, c: c['raw_displays'][-1].update(display_submitted_ns=6_000_000_000),
            lambda r, c: r['display_updates'][0].update(source_media_time_ns=1),
            lambda r, c: r['display_updates'][0].update(display_submitted_ns=2_500_000_000),
            lambda r, c: r.update(error='failed'),
        ]
        for change in changes:
            with self.subTest(change=change):
                run, receipt, ref = self.fixture()
                change(run, receipt)
                with self.assertRaises(ValueError):
                    score_wgc(run, receipt, ref)


if __name__ == '__main__':
    unittest.main()
