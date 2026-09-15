"""Exact-region scheduling, source ordering and relative detail warnings."""
import hashlib
import threading
import time
import unittest
from unittest.mock import patch

from blackjack_lab.capture.frame_intake import FrameIntake
from blackjack_lab.vision.live_input import LiveStyle, NormalizedBox
from blackjack_lab.vision.region_observer import RegionObserver, inference_fingerprints, same_as_completed_inference
from blackjack_lab.vision.temporal_preview import TemporalPreviewTracker
from blackjack_lab.vision.contracts import RecognitionResult,CardObservation,RankHypothesis,FACE_SHOWN,RECOGNITION_SCHEMA_VERSION
from blackjack_lab.realtime_preview import RealtimePreviewSession

try:
    import cv2
    import numpy as np
except ImportError:
    cv2 = np = None


@unittest.skipIf(cv2 is None or np is None, 'optional vision dependencies')
class RegionObserverTests(unittest.TestCase):
    def fixture(self, limit=8):
        style=LiveStyle('test', {'table': NormalizedBox(0,0,.5,1)})
        intake=FrameIntake('region-test',target_fps=120)
        observer=RegionObserver(style,evidence_limit=limit,expected_size=(120,80))
        pixels=np.full((80,120,3),100,dtype=np.uint8)
        return style,intake,observer,pixels

    def test_gate_compares_completed_inference_not_previous_source_observation(self):
        _,intake,observer,pixels=self.fixture()
        a=intake.offer(pixels,now_ns=1_000_000_000)
        first=observer.observe(a,intake.token)
        completed=inference_fingerprints(first)
        pixels[5,5,2]=101  # Single changed channel; tiny thumbnails may miss it.
        b=intake.offer(pixels,now_ns=1_020_000_000)
        observer.observe(b,intake.token)
        c=intake.offer(pixels,now_ns=1_040_000_000)
        current=observer.observe(c,intake.token)
        self.assertTrue(current['regions'][0]['same_as_previous_source'])
        self.assertFalse(same_as_completed_inference(current,completed))
        self.assertTrue(same_as_completed_inference(current,inference_fingerprints(current)))
        pixels[5,90]=0  # Adapters retain outside-ROI candidates; don't hide these.
        d=intake.offer(pixels,now_ns=1_060_000_000)
        outside=observer.observe(d,intake.token)
        self.assertTrue(outside['regions'][0]['same_as_previous_source'])
        self.assertFalse(same_as_completed_inference(outside,inference_fingerprints(current)))

    def test_ordering_generation_and_bounded_records_do_not_reuse_future_detail(self):
        _,intake,observer,pixels=self.fixture(limit=2)
        older=intake.offer(pixels,now_ns=1_000_000_000)
        pixels[::2,:]=255
        newer=intake.offer(pixels,now_ns=1_030_000_000)
        new_record=observer.observe(newer,intake.token)
        late=observer.observe(older,intake.token,origin='inference')
        self.assertFalse(late['ordered_source_observation'])
        self.assertIsNone(late['regions'][0]['change_magnitude'])
        self.assertEqual(observer.latest(intake.token)['frame_id'],newer.frame_id)
        old_digest=new_record['regions'][0]['content_digest']
        new_record['regions'][0]['content_digest']='mutated'
        self.assertEqual(observer.latest(intake.token)['regions'][0]['content_digest'],old_digest)
        intake.new_epoch('crop changed')
        self.assertIsNone(observer.latest(intake.token))
        self.assertIsNone(observer.observe(newer,intake.token))
        fresh=intake.offer(pixels,now_ns=1_060_000_000)
        record=observer.observe(fresh,intake.token)
        self.assertFalse(record['regions'][0]['same_as_previous_source'])
        self.assertEqual(observer.snapshot()['evictions'],1)

    def test_relative_detail_drop_is_a_warning_and_does_not_make_pixels_equal(self):
        _,intake,observer,pixels=self.fixture()
        pixels[::2,:,:]=240;pixels[1::2,:,:]=20
        sharp=intake.offer(pixels,now_ns=1_000_000_000)
        a=observer.observe(sharp,intake.token)
        blurred=cv2.GaussianBlur(pixels,(9,9),2)
        # Retain a broad gradient, so this is not the uniform-image warning.
        blurred[:,0:10]=50
        b=intake.offer(blurred,now_ns=1_020_000_000)
        current=observer.observe(b,intake.token)
        self.assertEqual(current['regions'][0]['detail_state'],'detail_decreased')
        self.assertFalse(same_as_completed_inference(current,inference_fingerprints(a)))
        self.assertIsNone(observer.snapshot()['readability_probability'])

    def test_source_change_during_region_copy_cannot_publish_old_quality(self):
        _,intake,observer,pixels=self.fixture()
        packet=intake.offer(pixels,now_ns=1_000_000_000)
        entered,release=threading.Event(),threading.Event()
        real_hash=hashlib.blake2b;result=[]
        def delayed(*args,**kwargs):
            entered.set();release.wait(1)
            return real_hash(*args,**kwargs)
        with patch('blackjack_lab.vision.region_observer.hashlib.blake2b',side_effect=delayed):
            worker=threading.Thread(target=lambda:result.append(observer.observe(packet,intake.token)))
            worker.start();self.assertTrue(entered.wait(1));intake.new_epoch('changed')
            release.set();worker.join(1)
        self.assertEqual(result,[None])
        self.assertEqual(observer.snapshot()['samples'],0)
        self.assertIsNone(observer.latest(intake.token))

    def test_session_recovers_real_change_hidden_by_sampled_repeat_flag(self):
        style,_,_,pixels=self.fixture()
        class Source:
            error=None;finished=False
            def __init__(self):self.intake=FrameIntake('session-region',target_fps=120)
            def start(self):self.intake.mark_started()
            def latest(self):return self.intake.latest()
            def token(self):return self.intake.token()
            def report(self):return self.intake.report()
            def stop(self):self.intake.mark_stopped()
        class Adapter:
            model_id='test';digest='test'
            def __init__(self):self.calls=0
            def recognize(self,loaded,layout,source_declaration,*,timings):
                self.calls+=1;t=time.perf_counter_ns()
                timings.update(detection_start_ns=t,detection_end_ns=t,classification_start_ns=t,classification_end_ns=t)
                rank='3' if loaded.rgb[(5*loaded.width+5)*3]==101 else '2'
                observation=CardObservation(observation_id=loaded.sha256,asset_sha256=loaded.sha256,
                    crop_sha256=hashlib.sha256(loaded.rgb).hexdigest(),bbox={'x':4,'y':4,'w':12,'h':16},
                    region_id='table',layout_profile_id=layout.layout_profile_id,model_id='test',model_digest='test',
                    recognition_schema_version=RECOGNITION_SCHEMA_VERSION,reject_reason=None,
                    rank_candidates=[RankHypothesis(rank,1.,rank)],face_state_candidate=FACE_SHOWN,
                    source_declaration=source_declaration)
                return RecognitionResult(loaded.sha256,str(loaded.path),layout.layout_profile_id,'test','test',
                    observations=[observation])
        source=Source();adapter=Adapter();session=RealtimePreviewSession(source,style,adapter,recognition_fps=60)
        def wait_for(predicate):
            deadline=time.monotonic()+2
            while not predicate() and time.monotonic()<deadline:time.sleep(.005)
            self.assertTrue(predicate(),session.error)
        try:
            with patch('blackjack_lab.capture.frame_intake.frame_signature',return_value='same-sampled-signature'):
                session.start();source.intake.offer(pixels,now_ns=1_000_000_000);wait_for(lambda:session.processed==1)
                self.assertEqual(session.latest_result().tracks[0]['observed_rank'],'2')
                source.intake.offer(pixels,now_ns=1_050_000_000);wait_for(lambda:session.unchanged_regions_skipped==1)
                self.assertEqual(adapter.calls,1)
                pixels[5,5,2]=101
                changed=source.intake.offer(pixels,now_ns=1_100_000_000);self.assertTrue(changed.is_repeat)
                wait_for(lambda:session.processed==2)
                self.assertEqual(adapter.calls,2)
                self.assertEqual(session.latest_result().tracks[0]['observed_rank'],'3')
                previous_id=session.latest_result().tracks[0]['track_id']
                source.intake.offer(np.zeros_like(pixels),now_ns=1_150_000_000)
                wait_for(lambda:session.repeat_or_black==1)
                self.assertIsNone(session.latest_result())
                source.intake.offer(pixels,now_ns=1_200_000_000)
                wait_for(lambda:session.processed==3)
                self.assertNotEqual(session.latest_result().tracks[0]['track_id'],previous_id)
                previous_id=session.latest_result().tracks[0]['track_id']
                source.intake.offer(pixels,now_ns=1_900_000_000)
                wait_for(lambda:session.processed==4)
                self.assertEqual(session.input_gaps,1)
                self.assertNotEqual(session.latest_result().tracks[0]['track_id'],previous_id)
        finally:
            session.stop();session._thread.join(2)
        self.assertFalse(session._thread.is_alive())

    def test_gap_invalidation_does_not_recycle_preview_ids(self):
        tracker=TemporalPreviewTracker();tracker.sequence=7;tracker._last_sample='old'
        tracker.tracks.append(object());tracker.invalidate_observation_gap()
        self.assertEqual(tracker.sequence,7)
        self.assertEqual(tracker.tracks,[])
        self.assertIsNone(tracker._last_sample)
