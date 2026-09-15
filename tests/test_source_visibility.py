"""Complete event coverage, wait intervals and a censored hidden card."""
from copy import deepcopy
import unittest

from scripts.summarize_source_visibility import summarize


class SourceVisibilityTests(unittest.TestCase):
    def fixture(self):
        events = [{'physical_id_proposal': 'deal', 'rank': 'J', 'cohort': 'new_visibility',
                   'first_readable_interval_s': [3, 4]},
                  {'physical_id_proposal': 'reveal', 'rank': '9', 'cohort': 'new_visibility',
                   'first_readable_interval_s': [61, 61.25]}]
        ref = {'source_sha256': 'source', 'events': events}
        appearance = {'source_sha256': 'source', 'ready_for_wait_scoring': True,
                      'model_outputs_used_for_annotation': False, 'human_confirmed': False,
                      'made_after_model_outcomes_known': True, 'items': [],
                      'unreadable_at_horizon': [{'event_id': 'back', 'rank': None,
                          'rank_readable_observed': False, 'first_visible_interval_s': [8, 8.5],
                          'last_observed_media_s': 10}]}
        for event, visible in zip(events, ([2, 3], [5, 5.5])):
            appearance['items'].append({'event_id': event['physical_id_proposal'], 'rank': event['rank'],
                'cohort': event['cohort'], 'first_readable_interval_s': event['first_readable_interval_s'],
                'first_visible_interval_s': visible, 'assistant_source_reviewed': True})
        return ref, appearance

    def test_waits_and_unreadable_horizon_are_separate_from_rank_denominator(self):
        ref, app = self.fixture()
        before = deepcopy((ref, app))
        result = summarize(ref, app)
        self.assertEqual(result['new_readable_events'], 2)
        self.assertEqual(result['events'][0]['visible_to_readable_wait_interval_s'], [0, 2])
        self.assertEqual(result['events'][1]['visible_to_readable_wait_interval_s'], [55.5, 56.25])
        self.assertEqual(result['unreadable_at_horizon'][0]['visible_to_readable_wait_lower_bound_s'], 1.5)
        self.assertIsNone(result['unreadable_at_horizon'][0]['visible_to_readable_wait_upper_bound_s'])
        self.assertEqual((ref, app), before)

    def test_missing_duplicate_or_stale_rank_entries_cannot_pass_complete_review(self):
        for change in (lambda a: a['items'].pop(), lambda a: a['items'].append(a['items'][0]),
                       lambda a: a['items'][0].update(rank='7')):
            ref, app = self.fixture()
            change(app)
            with self.assertRaises(ValueError):
                summarize(ref, app)

    def test_impossible_clocks_and_guessed_hidden_rank_fail(self):
        for change in (lambda a: a['items'][0].update(first_visible_interval_s=[5, 6]),
                       lambda a: a['unreadable_at_horizon'][0].update(rank='A')):
            ref, app = self.fixture()
            change(app)
            with self.assertRaises(ValueError):
                summarize(ref, app)
