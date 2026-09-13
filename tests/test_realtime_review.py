"""Selected-frame provenance and real Tk/manual-ledger handoff contracts."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

try:
    import numpy as np
except ImportError:
    np = None

from blackjack_lab.capture.frame_intake import FrameIntake
from blackjack_lab.realtime_preview import PreviewResult, RealtimePreviewSession
from blackjack_lab.ui.realtime_review import freeze_video_result
from blackjack_lab.ui.vision_bridge import ConfirmDecision, OP_NEW, VisionBridgeError, capture_bind_context
from blackjack_lab.vision.contracts import CardObservation, RankHypothesis, RecognitionResult, RECOGNITION_SCHEMA_VERSION
from blackjack_lab.vision.live_input import LiveStyle, NormalizedBox, frame_to_loaded, layout_for_frame
from blackjack_lab.vision.tracker import FrameTracker
from blackjack_lab.vision.video_io import VideoAsset


def fixture(index=120, style=None, adapter=None):
    style = style or LiveStyle('handoff', {'all': NormalizedBox(0, 0, 1, 1)})
    adapter = adapter or SimpleNamespace(model_id='fixture', digest='fixture-digest', identity_text='fixture')
    asset = VideoAsset(Path('fixture.mp4'), 'a'*64, 1, 100, 80, 120., 1200)
    intake = FrameIntake('video:'+asset.sha256, layout_version=style.layout_version(100,80), target_fps=1000)
    pixels = np.full((80,100,3), 120, dtype=np.uint8)
    pixels[10:30,10:30] = 30
    packet = intake.offer(pixels,source_frame_index=index,media_time_ns=round(index/120*1e9))
    loaded = frame_to_loaded(packet)
    layout = layout_for_frame(style,100,80)
    result = RecognitionResult(loaded.sha256,str(loaded.path),layout.layout_profile_id,adapter.model_id,adapter.digest)
    for i,x in enumerate((10,65)):
        result.observations.append(CardObservation(
            observation_id=f'input-{i}',asset_sha256=loaded.sha256,crop_sha256=str(i)*64,
            bbox={'x':x,'y':10,'w':20,'h':25},region_id='all',layout_profile_id=layout.layout_profile_id,
            model_id=adapter.model_id,model_digest=adapter.digest,recognition_schema_version=RECOGNITION_SCHEMA_VERSION,
            rank_candidates=[RankHypothesis('8',.95,'fixture')],reject_reason=None,face_state_candidate='shown',
            source_declaration='旁观录像人工确认',captured_at=packet.observed_at))
    source = SimpleNamespace(asset=asset,is_live=False,finished=True,error=None,
        token=intake.token,report=intake.report,stop=intake.mark_stopped)
    owner = RealtimePreviewSession(source,style,adapter)
    owner.finished = True
    row = PreviewResult(packet,result,{'candidate_ready_ns':time.perf_counter_ns()},1)
    owner.records.append(row.metadata())
    return owner,row,intake


@unittest.skipIf(np is None, 'optional numpy dependency')
class VideoSnapshotTests(unittest.TestCase):
    def test_exact_index_selected_pixels_and_original_predictions_are_preserved(self):
        owner,row,intake = fixture(index=900)
        self.assertEqual(row.packet.frame_id,1)
        self.assertEqual(row.packet.as_dict()['source_frame_index'],900)
        original = deepcopy(row.recognition.as_dict())
        # Latest source has moved; the selected inference frame must still win.
        intake.offer(np.full((80,100,3),240,dtype=np.uint8),source_frame_index=901,
            media_time_ns=round(901/120*1e9),now_ns=row.packet.observed_monotonic_ns+10_000_000)
        snapshot = freeze_video_result(owner,row,'a'*64)
        self.assertEqual(snapshot.frame_index,900)
        self.assertEqual(snapshot.loaded.rgb[0],120)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); tracker=FrameTracker()
            folder=snapshot.save(root,tracker)
            self.assertEqual(row.recognition.as_dict(),original)
            evidence=json.loads((folder/'handoff.json').read_text(encoding='utf-8'))
            self.assertFalse(evidence['writes_ledger'])
            self.assertFalse(evidence['preview_ids_are_ledger_ids'])
            self.assertEqual(evidence['source_rgb_sha256'],hashlib.sha256(snapshot.loaded.rgb).hexdigest())
            self.assertEqual(evidence['original_recognition'],original)
            ids=[o.observation_id for o in snapshot.result.observations]
            self.assertEqual(len(set(ids)),2)
            self.assertNotIn('input-0',ids)
            for obs in snapshot.result.observations:self.assertTrue((root/obs.crop_relpath).is_file())
            saved=(folder/'source.png').read_bytes()
            with self.assertRaises(FileExistsError):snapshot.save(root,tracker)
            self.assertEqual((folder/'source.png').read_bytes(),saved)

    def test_missing_index_source_generation_model_and_pixel_changes_are_rejected(self):
        for change in ('index','timestamp','source','epoch','model','pixels','stopped','foreign'):
            with self.subTest(change=change):
                owner,row,intake=fixture()
                if change=='index':row.packet.source_frame_index=None
                elif change=='timestamp':row.packet.media_time_ns+=1
                elif change=='source':owner.source.asset.sha256='b'*64
                elif change=='epoch':intake.new_epoch('changed')
                elif change=='model':owner.adapter.digest='changed'
                elif change=='pixels':
                    row.region_observation={'detector_context_digest':hashlib.blake2b(row.packet.pixels.tobytes(),digest_size=16).hexdigest()}
                    row.packet.pixels[0,0,0]+=1
                elif change=='stopped':owner.stop()
                elif change=='foreign':row.timings['candidate_ready_ns']+=1
                if change in ('index','timestamp'):
                    owner.records.clear();owner.records.append(row.metadata())
                with self.assertRaises(VisionBridgeError):freeze_video_result(owner,row,'a'*64)


@unittest.skipIf(np is None, 'optional numpy dependency')
class VideoSnapshotTkTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from blackjack_lab.analysis.contracts import research_rules
        from blackjack_lab.ui.app import BlackjackLabApp
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        try:self.app=BlackjackLabApp(Path(self.tmp.name)/'review.db')
        except tk.TclError as exc:self.skipTest(f'Tk unavailable: {exc}')
        self.addCleanup(self.close_app)
        self.app.withdraw()
        self.app.ctrl.new_shoe(research_rules(6));self.app.ctrl.start_round(['玩家1'])
        self.app.act_open_vision();self.win=self.app._vision_win;self.win.withdraw()
        self.owner,self.row,self.intake=fixture()
        self.win.selected_style=self.owner.style
        self.win.runtime.select_model(self.owner.adapter)
        self.win.video_reader=SimpleNamespace(asset=self.owner.source.asset,close=lambda:None)
        self.win._source_key='video:'+'a'*64
        self.win.tracker=FrameTracker()
        self.win.tracker.confirm_round_boundary(self.win._tracker_round_key(capture_bind_context(self.app.ctrl)))
        self.app.update_idletasks()

    def close_app(self):
        import gc
        app=self.app
        if app._vision_win is not None:app._vision_win.on_close()
        app.on_close()
        self.app=self.win=None
        del app
        gc.collect()  # Collect destroyed Tk objects on their owning thread.

    def pump(self):
        deadline=time.monotonic()+3
        while self.win._preview_review_pending and time.monotonic()<deadline:
            self.app.update();time.sleep(.01)
        self.assertFalse(self.win._preview_review_pending)

    def test_freeze_does_not_commit_and_repeat_review_preserves_manual_identity(self):
        before=self.app.ctrl.commit_revision
        self.assertTrue(self.win.accept_realtime_snapshot(self.owner,self.row,self.win._preview_context()))
        self.pump()
        self.assertEqual(self.app.ctrl.commit_revision,before)
        self.assertEqual(self.win.var_frame.get(),120)
        self.assertEqual(self.win.var_op.get(),'')
        self.assertEqual(self.win.var_seat.get(),'')
        with self.assertRaises(VisionBridgeError):self.win._decision()
        first=self.win.session.result.observations[0].observation_id
        d=ConfirmDecision(first,OP_NEW,seat='玩家1',confirmed_rank='8')
        self.win.session.confirm(d);self.win.tracker.mark_committed(first)
        revision=self.app.ctrl.commit_revision
        owner,row,_=fixture(index=121,style=self.owner.style,adapter=self.owner.adapter)
        self.win.accept_realtime_snapshot(owner,row,self.win._preview_context());self.pump()
        self.assertEqual(self.app.ctrl.commit_revision,revision)
        self.assertEqual(self.win.session.result.observations[0].observation_id,first)
        self.assertTrue(self.win.session.confirm(d).already_saved)
        second=self.win.session.result.observations[1].observation_id
        self.assertNotEqual(second,first)
        self.win.session.confirm(ConfirmDecision(second,OP_NEW,seat='玩家1',confirmed_rank='8'))
        self.assertEqual(self.app.ctrl.state().current.shoe.exact_out['8'],2)
        # Candidate changes clear an old correction target and refresh the rank.
        self.win.session.result.observations[1].rank_candidates[0].rank='Q'
        self.win.var_target.set('old-event');self.win.var_obs.set(second);self.win._selected_observation()
        self.assertEqual(self.win.var_rank.get(),'Q');self.assertEqual(self.win.var_target.get(),'')

    def test_changed_launch_and_pending_context_cannot_import_old_snapshot(self):
        context=self.win._preview_context()
        self.win.runtime.invalidate('model changed')
        with self.assertRaises(VisionBridgeError):self.win.accept_realtime_snapshot(self.owner,self.row,context)
        before=self.app.ctrl.commit_revision
        self.win.accept_realtime_snapshot(self.owner,self.row,self.win._preview_context())
        self.win._withdraw('source changed while waiting')
        self.pump()
        self.assertIsNone(self.win.session)
        self.assertEqual(self.app.ctrl.commit_revision,before)

    def test_preview_button_uses_displayed_result_instead_of_newer_worker_result(self):
        from blackjack_lab.ui.realtime_preview import RealtimePreviewWindow
        calls=[]
        self.owner.start=lambda:None
        with patch.object(self.owner,'latest_result',side_effect=AssertionError('must use displayed result')):
            preview=RealtimePreviewWindow(self.win,self.owner,on_review=lambda owner,row:calls.append((owner,row)))
            preview.withdraw();preview._displayed_result=self.row
            preview.review()
            self.assertEqual(calls,[(self.owner,self.row)])
            preview.close()


if __name__=='__main__':unittest.main()
