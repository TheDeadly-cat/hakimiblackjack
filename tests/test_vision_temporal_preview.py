"""Preview association contracts; synthetic fixtures are not physical-ID accuracy."""
import unittest
from types import SimpleNamespace

from blackjack_lab.vision.deps import cv2_available
from blackjack_lab.vision.temporal_preview import TemporalPreviewTracker, _assignment, display_state, PERSISTENT_OBSERVATION_POLICY


def observation(x, y=20, rank="8", w=27, h=20):
    return SimpleNamespace(bbox=dict(x=x,y=y,w=w,h=h), region_id="all", observation_id="original",
                           accepted_rank=lambda: rank)


@unittest.skipUnless(cv2_available(), "optional vision dependencies")
class TemporalPreviewTests(unittest.TestCase):
    def setUp(self):
        import numpy as np
        self.pixels=np.full((100,200,3),150,dtype=np.uint8)
        self.tracker=TemporalPreviewTracker()

    def update(self, ms, obs, key=None):
        return self.tracker.update(SimpleNamespace(observations=obs), self.pixels,
                                   int(ms*1e6), str(ms) if key is None else key)

    def test_equal_ranks_remain_separate_and_rank_change_does_not_change_id(self):
        initial=self.update(0,[observation(20),observation(100)])
        for ms in (125,250):
            state=self.update(ms,[observation(20),observation(100)])
        self.assertEqual(len({t['track_id'] for t in state}),2)
        self.assertEqual([t['stable_rank'] for t in state],['8','8'])
        changed=self.update(375,[observation(20,rank='6'),observation(100)])
        self.assertEqual(changed[0]['track_id'],initial[0]['track_id'])
        self.assertEqual(changed[0]['observed_rank'],'6')
        self.assertEqual(changed[0]['stable_rank'],'8')
        self.assertEqual(changed[0]['identity_state'],'rank_conflict')
        changed=self.update(500,[observation(20,rank='6'),observation(100)])
        self.assertIsNone(changed[0]['stable_rank'])

    def test_rereading_frozen_sample_cannot_stabilize_or_extend_lifetime(self):
        self.update(0,[observation(20)],'same-source-pixels')
        self.update(125,[observation(20)],'same-source-pixels')
        state=self.update(250,[observation(20)],'same-source-pixels')
        self.assertIsNone(state[0]['stable_rank'])
        self.assertEqual(state[0]['last_seen_ns'],0)
        for ms in (375,500,625):state=self.update(ms,[observation(20)])
        self.assertEqual(state[0]['stable_rank'],'8')
        aged=display_state(state[0],1_300_000_000)
        self.assertIsNone(aged['stable_rank'])
        self.assertEqual(aged['identity_state'],'expired')
        self.assertFalse(aged['current'])

    def test_unchanged_card_crop_with_changing_background_does_not_add_votes(self):
        obs=observation(20);obs.crop_sha256='unchanged-card'
        for ms in (0,125,250):state=self.update(ms,[obs])
        self.assertIsNone(state[0]['stable_rank'])
        self.assertEqual(state[0]['evidence_count'],1)
        self.assertEqual(state[0]['last_seen_ns'],250_000_000)
        obs.crop_sha256='changed-card-pixels-1';self.update(375,[obs])
        obs.crop_sha256='changed-card-pixels-2';state=self.update(500,[obs])
        self.assertEqual(state[0]['stable_rank'],'8')
        state=self.update(625,[obs])
        self.assertFalse(state[0]['new_rank_support'])
        self.assertEqual(state[0]['stable_supported_ns'],625_000_000)

    def test_lost_seven_cannot_donate_id_to_nearby_unclassified_new_card(self):
        old=self.update(0,[observation(40,50,rank='7')])[0]['track_id']
        self.update(125,[])
        state=self.update(333,[observation(60,16,rank=None,w=40)])
        current=[t for t in state if t['current']]
        self.assertEqual(len(current),1)
        self.assertNotEqual(current[0]['track_id'],old)
        self.assertIsNone(current[0]['stable_rank'])

    def test_clear_and_expiry_create_new_preview_identity(self):
        old=self.update(0,[observation(20)])[0]['track_id']
        state=self.update(700,[observation(20)])[0]
        self.assertNotEqual(old,state['track_id'])
        self.assertIsNone(state['stable_rank'])

    def test_tracks_are_bounded_under_detector_noise(self):
        self.tracker=TemporalPreviewTracker(max_tracks=2)
        self.update(0,[observation(10),observation(80),observation(140)])
        self.assertEqual(len(self.tracker.tracks),2)
        self.assertEqual(self.tracker.capacity_drops,1)

    def test_global_assignment_does_not_greedily_steal_second_match(self):
        self.assertEqual(sorted(_assignment([[.1,.2,.85,.85],[.11,9.,.85,.85]])),[(0,1),(1,0)])

    def test_current_observation_policy_can_display_static_card_without_inventing_pixel_votes(self):
        import hashlib
        self.tracker=TemporalPreviewTracker(policy=PERSISTENT_OBSERVATION_POLICY)
        obs=observation(20,rank='K');obs.crop_sha256='one-unchanged-card-crop'
        for i,ms in enumerate((0,125,250)):
            self.pixels[0,0,0]=i  # A changing source outside the static card crop.
            state=self.update(ms,[obs],hashlib.sha256(self.pixels.tobytes()).hexdigest())
        self.assertEqual(state[0]['stable_rank'],'K')
        self.assertEqual(state[0]['stability_basis'],'persistent_current_observation')
        self.assertEqual(state[0]['current_observation_count'],3)
        self.assertEqual(state[0]['evidence_count'],1)
        self.assertEqual(state[0]['support_count'],1)
        self.assertFalse(state[0]['new_rank_support'])

    def test_current_observation_policy_keeps_freeze_and_expiry_protection(self):
        self.tracker=TemporalPreviewTracker(policy=PERSISTENT_OBSERVATION_POLICY)
        obs=observation(20);obs.crop_sha256='same-crop'
        for ms in (0,125,250,8000):state=self.update(ms,[obs],'same-full-source')
        self.assertIsNone(state[0]['stable_rank'])
        self.assertEqual(state[0]['last_seen_ns'],0)
        self.assertEqual(state[0]['identity_state'],'expired')
        self.assertEqual(len(self.tracker.tracks[0].observation_history),1)
        for ms in (8125,8250,8375):state=self.update(ms,[obs])
        self.assertEqual(state[0]['stable_rank'],'8')
        aged=display_state(state[0],9_100_000_000)
        self.assertIsNone(aged['stable_rank']);self.assertIsNone(aged['stability_basis'])
        self.assertEqual(aged['identity_state'],'expired')

    def test_current_observation_policy_keeps_conflicts_and_independent_equal_rank_tracks(self):
        self.tracker=TemporalPreviewTracker(policy=PERSISTENT_OBSERVATION_POLICY)
        a,b=observation(20),observation(100);a.crop_sha256=b.crop_sha256='same-rank-pixels'
        for ms in (0,125,250):state=self.update(ms,[a,b])
        self.assertEqual(len({t['track_id'] for t in state}),2)
        self.assertEqual([t['stable_rank'] for t in state],['8','8'])
        changed=observation(20,rank='6');changed.crop_sha256='changed-rank-pixels'
        self.update(375,[changed,b]);state=self.update(500,[changed,b])
        self.assertIsNone(state[0]['stable_rank']);self.assertEqual(state[1]['stable_rank'],'8')
        for ms in range(625,5000,125):self.update(ms,[changed,b])
        self.assertTrue(all(len(t.observation_history)<=8 and len(t.evidence)<=8 for t in self.tracker.tracks))
        self.tracker.invalidate_observation_gap()
        self.assertEqual(self.tracker.tracks,[])


if __name__=='__main__': unittest.main()
