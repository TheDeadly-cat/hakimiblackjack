"""Preview association contracts; synthetic fixtures are not physical-ID accuracy."""
import unittest
from types import SimpleNamespace

from blackjack_lab.vision.deps import cv2_available
from blackjack_lab.vision.temporal_preview import TemporalPreviewTracker, _assignment, display_state


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


if __name__=='__main__': unittest.main()
