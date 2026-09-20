"""Window selection and capture-to-review contracts; no WGC backend in CI."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from tests import test_realtime_review as review_fixture
from blackjack_lab.capture.frame_intake import FrameIntake
from blackjack_lab.realtime_preview import RealtimePreviewSession,PreviewResult,RealtimeWgcSource
from blackjack_lab.ui.realtime_review import freeze_window_result,load_video_review_snapshot
from blackjack_lab.ui.vision_bridge import VisionBridgeError,ConfirmDecision,OP_LINK,OP_NEW
from blackjack_lab.vision.live_input import frame_to_loaded,SOURCE_LIVE_CAPTURE

np=review_fixture.np


def window_fixture():
    old,old_row,_=review_fixture.fixture()
    intake=FrameIntake('window:123',layout_version=old.style.layout_version(100,80),target_fps=1000)
    packet=intake.offer(old_row.packet.pixels,media_time_ns=98_000_000_000_000)
    loaded=frame_to_loaded(packet)
    recognition=deepcopy(old_row.recognition);recognition.asset_sha256=loaded.sha256
    for obs in recognition.observations:
        obs.asset_sha256=loaded.sha256;obs.source_declaration=SOURCE_LIVE_CAPTURE
    source=SimpleNamespace(is_live=True,hwnd=123,expected_process_id=321,
        window_info={'hwnd':123,'process_id':321,'title':'Authorized fixture'},
        verify_selected_window=lambda:None,finished=True,error=None,
        token=intake.token,report=intake.report,stop=intake.mark_stopped)
    owner=RealtimePreviewSession(source,old.style,old.adapter);owner.finished=True
    row=PreviewResult(packet,recognition,dict(old_row.timings),1)
    owner.records.append(row.metadata())
    return owner,row,intake


@unittest.skipIf(np is None,'optional numpy dependency')
class WindowSnapshotTests(unittest.TestCase):
    def test_window_clock_is_not_a_video_index_and_reload_keeps_original_predictions(self):
        owner,row,_=window_fixture();original=deepcopy(row.recognition.as_dict())
        snapshot=freeze_window_result(owner,row,123,321)
        self.assertIsNone(snapshot.frame_index);self.assertIsNone(snapshot.video_time_ms)
        with tempfile.TemporaryDirectory() as directory:
            folder=snapshot.save(directory)
            restored,root=load_video_review_snapshot(folder/'handoff.json')
            self.assertTrue(root.samefile(directory))
            self.assertEqual(restored.loaded.rgb,snapshot.loaded.rgb)
            self.assertEqual(row.recognition.as_dict(),original)
            self.assertNotIn('source_frame_index',restored.metadata['packet'])
            self.assertEqual(restored.metadata['packet']['media_time_ns'],98_000_000_000_000)
            self.assertIsNone(restored.frame_index)
            self.assertEqual(restored.result.observations[0].rank_candidates[0].rank,'8')
            self.assertEqual(len({o.observation_id for o in restored.result.observations}),2)
            for obs in restored.result.observations:
                self.assertIsNone(obs.frame_index);self.assertIsNone(obs.relative_time_ms)
                self.assertIsNone(obs.as_dict()['track_id']);self.assertNotIn(obs.observation_id,('input-0','input-1'))
            metadata=json.loads((folder/'handoff.json').read_text(encoding='utf-8'))
            metadata['packet']['source_frame_index']=1
            (folder/'handoff.json').write_text(json.dumps(metadata),encoding='utf-8')
            with self.assertRaises(VisionBridgeError):load_video_review_snapshot(folder/'handoff.json')

    def test_changed_source_process_generation_or_invented_video_index_is_rejected(self):
        for change in ('hwnd','process','selected','epoch','video_index','stopped'):
            with self.subTest(change=change):
                owner,row,intake=window_fixture()
                if change=='hwnd':owner.source.hwnd=456
                elif change=='process':owner.source.expected_process_id=654
                elif change=='selected':owner.source.window_info['process_id']=654
                elif change=='epoch':intake.new_epoch('changed')
                elif change=='video_index':row.packet.source_frame_index=1
                elif change=='stopped':owner.stop()
                with self.assertRaises(VisionBridgeError):freeze_window_result(owner,row,123,321)

    def test_selected_process_guard_survives_restart_and_refuses_reused_handle(self):
        owner,_,_=window_fixture()
        source=RealtimeWgcSource(123,owner.style,expected_process_id=321)
        self.assertEqual(source.clone().expected_process_id,321)
        wrong=SimpleNamespace(process_id=654,minimized=False)
        with patch('blackjack_lab.capture.window_list.describe_window',return_value=wrong):
            with self.assertRaises(ValueError):source.verify_selected_window()
        self.assertIsNone(source.backend)


@unittest.skipIf(np is None,'optional numpy dependency')
class WindowSnapshotTkTests(unittest.TestCase):
    close_app=review_fixture.VideoSnapshotTkTests.close_app
    pump=review_fixture.VideoSnapshotTkTests.pump

    def setUp(self):
        review_fixture.VideoSnapshotTkTests.setUp(self)
        self.owner,self.row,self.intake=window_fixture()
        self.win.video_reader=self.win.tracker=None
        self.win.selected_window=SimpleNamespace(hwnd=123,process_id=321)
        self.win._source_key='window:123:321:selection-fixture'
        self.win.runtime.select_model(self.owner.adapter)

    def test_capture_handoff_and_reopen_preserve_link_and_do_not_write_ledger(self):
        event=self.app.ctrl.deal_shown('玩家1','8')
        before=self.app.ctrl.ledger.to_list();revision=self.app.ctrl.commit_revision
        self.win.accept_realtime_snapshot(self.owner,self.row,self.win._preview_context());self.pump()
        self.assertIsNone(self.win.tracker)
        self.assertIn('采集序号',self.win.var_video.get())
        obs=self.win.session.result.observations[0]
        self.assertEqual(obs.source_declaration,SOURCE_LIVE_CAPTURE)
        self.win.session.confirm(ConfirmDecision(obs.observation_id,OP_LINK,target_event_id=event.event_id))
        saved=Path(self.win.session.result.image_path).parent/'handoff.json'
        self.win.open_review_snapshot_path(saved)
        self.assertIn(obs.observation_id,self.win.session.links)
        self.assertIsNone(self.win.selected_window)
        with self.assertRaises(VisionBridgeError):
            self.win.session.confirm(ConfirmDecision(obs.observation_id,OP_NEW,seat='玩家1',confirmed_rank='8'))
        self.assertEqual(self.app.ctrl.ledger.to_list(),before)
        self.assertEqual(self.app.ctrl.commit_revision,revision)

    def test_window_switch_during_freeze_preserves_current_ledger_and_rejects_old_import(self):
        context=self.win._preview_context();before=self.app.ctrl.commit_revision
        self.win.accept_realtime_snapshot(self.owner,self.row,context)
        self.win._withdraw('selected source changed')
        self.win.selected_window=SimpleNamespace(hwnd=456,process_id=654)
        self.pump()
        self.assertIsNone(self.win.session)
        self.assertEqual(self.app.ctrl.commit_revision,before)
        with self.assertRaises(VisionBridgeError):self.win.accept_realtime_snapshot(self.owner,self.row,context)

    def test_picker_requires_explicit_selection_and_checks_live_process_before_discarding_review(self):
        from blackjack_lab.ui.window_source_picker import WindowSourcePicker
        current=SimpleNamespace(hwnd=123,process_id=321,minimized=False,title='Fixture',process_name='fixture.exe',width=640,height=480)
        calls=[]
        with patch('blackjack_lab.capture.window_list.list_capturable_windows',return_value=[current]):
            picker=WindowSourcePicker(self.win,calls.append);picker.withdraw()
        picker.select();self.assertFalse(calls)
        picker.table.selection_set('123');picker.select();self.assertEqual(calls,[current])
        self.win.accept_realtime_snapshot(self.owner,self.row,self.win._preview_context());self.pump()
        previous=self.win.session
        replaced=SimpleNamespace(process_id=654,minimized=False)
        with patch('blackjack_lab.capture.window_list.describe_window',return_value=replaced):
            with self.assertRaises(VisionBridgeError):self.win.select_window_source(current)
        self.assertIs(self.win.session,previous)


if __name__=='__main__':unittest.main()
