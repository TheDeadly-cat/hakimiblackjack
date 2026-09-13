"""Focused asynchronous preview contracts, without a desktop or ledger."""
import threading
import time
import unittest
from unittest.mock import patch

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
    def test_copy_finishing_after_stop_or_epoch_change_cannot_repopulate_intake(self):
        for change in (lambda i:i.mark_stopped(),lambda i:i.new_epoch('changed')):
            intake=FrameIntake('test')
            entered,release=threading.Event(),threading.Event()
            def signature(_):entered.set();release.wait(1);return 'pixels'
            output=[]
            with patch('blackjack_lab.capture.frame_intake.frame_signature',side_effect=signature):
                worker=threading.Thread(target=lambda:output.append(intake.offer(np.full((20,30,3),100,dtype=np.uint8))))
                worker.start();self.assertTrue(entered.wait(1));change(intake);release.set();worker.join(1)
            self.assertEqual(output,[None])
            self.assertIsNone(intake.preview())
            self.assertIsNone(intake.latest())
            self.assertEqual(intake.stats.dropped_by_generation,1)

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
        source=Source();session=RealtimePreviewSession(source,style,Adapter(),recognition_fps=60,observe_regions=False)
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

    def test_render_evidence_preserves_actual_state_and_records_expiry(self):
        from types import SimpleNamespace
        intake=FrameIntake('render',target_fps=100)
        packet=intake.offer(np.full((10,10,3),100,dtype=np.uint8),now_ns=1_000_000_000)
        source=SimpleNamespace(token=intake.token,report=intake.report)
        adapter=SimpleNamespace(model_id='fixture',digest='fixture')
        style=LiveStyle('test',{'all':NormalizedBox(0,0,1,1)})
        session=RealtimePreviewSession(source,style,adapter,evidence_limit=2)
        session.note_source_display(packet,1_500_000_000)
        shown=session.source_displays[-1]
        self.assertEqual(shown['frame_content_signature'],packet.frame_content_signature)
        self.assertEqual(shown['image_size'],[10,10])
        self.assertNotIn('pixels',shown)
        session.records.append({'row_id':1,'display_submitted_ns':None})
        tracks=[{'track_id':'one','observed_rank':'Q','stable_rank':'Q',
                 'identity_state':'temporally_associated','current':True,'bbox':{'x':1}}]
        session.note_rendered_state(1,tracks,packet,1_600_000_000,1.)
        tracks[0]['bbox']['x']=99
        session.note_rendered_state(1,tracks,packet,1_610_000_000,1.)
        self.assertEqual(len(session.display_updates),1)
        self.assertEqual(session.records[0]['displayed_tracks'][0]['bbox']['x'],1)
        tracks[0].update(stable_rank=None,observed_rank=None,identity_state='expired',current=False)
        session.note_rendered_state(1,tracks,packet,1_700_000_000,1.)
        self.assertEqual(len(session.display_updates),2)
        self.assertEqual(session.display_updates[-1]['tracks'][0]['identity_state'],'expired')
        session.note_rendered_state(2,tracks,packet,1_710_000_000,1.)
        self.assertEqual(session.display_update_evictions,1)
        intake.new_epoch('changed')
        session.note_rendered_state(3,tracks,packet,1_720_000_000,1.)
        self.assertEqual(len(session.display_updates),2)
        self.assertEqual(session.display_updates[-1]['row_id'],2)


if __name__=='__main__':unittest.main()
