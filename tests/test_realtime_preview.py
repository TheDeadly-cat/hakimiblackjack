"""Focused asynchronous preview contracts, without a desktop or ledger."""
import threading
import time
import unittest

from blackjack_lab.capture.frame_intake import FrameIntake
from blackjack_lab.vision.live_input import LiveStyle,NormalizedBox
from blackjack_lab.vision.contracts import RecognitionResult
from blackjack_lab.realtime_preview import RealtimePreviewSession

try:
    import numpy as np
except ImportError:
    np=None


@unittest.skipIf(np is None,"optional numpy dependency")
class RealtimePreviewTests(unittest.TestCase):
    def test_preview_does_not_consume_frames_or_repeat_evidence(self):
        intake=FrameIntake('test',target_fps=100)
        pixels=np.full((20,30,3),100,dtype=np.uint8)
        packet=intake.offer(pixels,now_ns=1000000000)
        pixels[:]=200
        self.assertIs(intake.preview(),packet)
        self.assertIs(intake.preview(),packet)
        self.assertIs(intake.latest(),packet)
        self.assertIs(intake.preview(),packet)
        self.assertEqual(packet.pixels[0,0,0],100)
        self.assertEqual(intake.stats.accepted,1)
        self.assertEqual(intake.stats.repeats,0)
        intake.new_epoch('source changed')
        self.assertIsNone(intake.preview())

    def test_stop_and_denial_remove_preview(self):
        for method in ('mark_stopped','mark_denied','mark_source_lost'):
            intake=FrameIntake('test');intake.offer(np.full((10,10,3),100,dtype=np.uint8))
            getattr(intake,method)(*(['denied'] if method=='mark_denied' else []))
            self.assertIsNone(intake.preview())

    def test_old_generation_finishing_late_cannot_publish(self):
        entered,release=threading.Event(),threading.Event()
        style=LiveStyle('test',{'all':NormalizedBox(0,0,1,1)})
        class Source:
            finished=False
            error=None
            def __init__(self):self.intake=FrameIntake('test',target_fps=100)
            def start(self):self.intake.mark_started()
            def latest(self):return self.intake.latest()
            def token(self):return self.intake.token()
            def report(self):return self.intake.report()
            def stop(self):self.intake.mark_stopped()
        class Adapter:
            model_id='test'
            digest='test'
            def recognize(self,loaded,layout,source_declaration,*,timings):
                entered.set();release.wait(2)
                now=time.perf_counter_ns()
                timings.update(detection_start_ns=now,detection_end_ns=now,classification_start_ns=now,classification_end_ns=now)
                return RecognitionResult(loaded.sha256,str(loaded.path),layout.layout_profile_id,'test','test')
        source=Source();session=RealtimePreviewSession(source,style,Adapter(),recognition_fps=60)
        session.start();source.intake.offer(np.full((10,10,3),100,dtype=np.uint8))
        self.assertTrue(entered.wait(1))
        source.intake.new_epoch('crop changed');release.set()
        deadline=time.monotonic()+1
        while not session.stale_results and time.monotonic()<deadline:time.sleep(.005)
        self.assertEqual(session.stale_results,1)
        self.assertIsNone(session.latest_result())
        self.assertEqual(session.snapshot()['rows'],[])
        session.stop();session._thread.join(1)
        self.assertFalse(session._thread.is_alive())


if __name__=='__main__':unittest.main()
