"""Hand-calculated event denominators and clocks, independent of model outputs."""
from copy import deepcopy
import unittest
from scripts.evaluate_realtime_events import score

class RealtimeEventScoringTests(unittest.TestCase):
    def fixture(self):
        def box(x):return {'x':x,'y':10,'w':20,'h':20}
        reference={'ready_for_scoring':True,'model_outputs_used':False,'source_sha256':'source',
            'playback_last_frame':40,'source_fps':4,'events':[]}
        for eid,rank,x in [('a','Q',0),('b','2',100),('missing','A',200)]:
            reference['events'].append({'physical_id_proposal':eid,'rank':rank,'cohort':'new_visibility',
                'first_readable_interval_s':[1,1.25],'scored_until_media_s':10,
                'quarter_positions':[{'quarter':q,'bbox':[x,10,20,20],'gradeable':True} for q in range(5,41)]})
        tracks=[{'track_id':f'track-{i}','bbox':box(x),'observed_rank':'Q','stable_rank':'Q',
                 'current':True,'identity_state':'temporally_associated'} for i,x in enumerate((0,100))]
        displays=[{'media_time_ns':round(q/4*1e9),'display_submitted_ns':round((1+q/4)*1e9)} for q in range(41)]
        run={'source':{'source_sha256':'source'},'writes_ledger':False,'source_displays':displays,
            'display_updates':[{'source_media_time_ns':1_500_000_000,'display_submitted_ns':3_000_000_000,
                'source_display_scale':1.,'row_id':1,'tracks':tracks}],
            'rows':[],'run_id':'fixture','model_digest':'fixture','temporal_policy':'fixture',
            'target_recognition_fps':8,'processed_frames':1}
        return run,reference

    def test_all_events_remain_in_denominator_and_rank_does_not_choose_match(self):
        run,ref=self.fixture();out=score(run,ref);group=out['new_visibility_events']
        self.assertEqual(group['eligible_events'],3)
        self.assertEqual(group['first_correct_stable']['observed_events'],1)
        self.assertEqual(group['first_stable_was_wrong'],1)
        self.assertEqual(group['first_correct_stable']['conditional_p95_interval_ms'],[750,1000])
        self.assertEqual(group['first_correct_stable']['deadline_counts']['1000'],
            {'verified_within':1,'within_if_onset_at_later_bound':1,'denominator':3})
        self.assertEqual(out['events'][1]['first_wrong_stable']['track_id'],'track-1')
        self.assertEqual(out['events'][2]['outcome'],'no_matched_candidate')

    def test_same_preview_id_on_two_spatial_targets_is_a_reference_merge(self):
        run,ref=self.fixture();second=deepcopy(run['display_updates'][0])
        second.update(source_media_time_ns=2_500_000_000,display_submitted_ns=4_000_000_000,row_id=2)
        second['tracks'][0]['bbox']['x']=100
        second['tracks'][1]['bbox']['x']=0
        run['display_updates'].append(second)
        out=score(run,ref)
        self.assertEqual(out['reference_identity_merges']['track-0'],['a','b'])
        self.assertEqual(out['events_with_reference_id_fragmentation'],2)

    def test_missing_reference_positions_are_ungraded_not_false_positives(self):
        run,ref=self.fixture()
        for e in ref['events']:
            for p in e['quarter_positions']:p['gradeable']=False
        out=score(run,ref)
        self.assertEqual(out['all_events']['eligible_events'],3)
        self.assertEqual(out['all_events']['first_correct_stable']['observed_events'],0)
        self.assertEqual(out['unmatched_track_display_updates'],{'stable':2})
        self.assertNotIn('false_positive_count',out)

    def test_identity_evidence_loss_and_resized_display_fail_closed(self):
        for field,value in [('display_update_evictions',1),('source_display_evictions',1),('evidence_evictions',1)]:
            run,ref=self.fixture();run[field]=value
            with self.assertRaises(ValueError):score(run,ref)
        run,ref=self.fixture();run['source']['source_sha256']='different'
        with self.assertRaises(ValueError):score(run,ref)
        run,ref=self.fixture();run['display_updates'][0]['source_display_scale']=.5
        with self.assertRaises(ValueError):score(run,ref)

if __name__=='__main__':unittest.main()
