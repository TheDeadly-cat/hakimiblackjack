"""Synthetic wiring/identity tests; not real-video recognition acceptance."""
import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from blackjack_lab.vision.contracts import LayoutProfile, RegionBox, REVIEW_PENDING
from blackjack_lab.vision.deps import ImageRejected, cv2_available, load_cv2, load_numpy
from blackjack_lab.vision.image_io import LoadedImage, sha256_bytes, write_png_rgb
from blackjack_lab.vision.live_input import LiveStyle, NormalizedBox, recognize_frame
from blackjack_lab.vision.model_adapter import RecognitionRuntime, TrainedModelAdapter
from blackjack_lab.vision.pipeline import recognize_loaded, recognize_path
from blackjack_lab.vision.rank_classifier import RankClassifier
from blackjack_lab.vision.real_cards import extract_glyphs
from blackjack_lab.vision.tracker import FrameTracker


def scene():
    cv2, np = load_cv2(), load_numpy()
    bgr = np.full((130, 240, 3), (60, 40, 30), dtype=np.uint8)
    for x in (10, 130):
        cv2.rectangle(bgr, (x, 10), (x + 85, 115), (250, 250, 250), -1)
        cv2.putText(bgr, "8", (x + 14, 50), cv2.FONT_HERSHEY_SIMPLEX,
                    1.0, (0, 0, 0), 3, cv2.LINE_8)
    rgb = np.ascontiguousarray(bgr[:, :, ::-1]).tobytes()
    return bgr, LoadedImage(Path("synthetic.png"), 240, 130, sha256_bytes(rgb),
                            rgb, len(rgb), "synthetic")


def layout():
    return LayoutProfile("test-style", "test-style", 240, 130,
                         {"seat": RegionBox(0, 0, 240, 130, "玩家1")}, felt_kind="navy")


def packet(bgr, *, frame_id=1, source_id="window:测试", stream_epoch=0):
    return SimpleNamespace(
        pixels=bgr, width=240, height=130, frame_id=frame_id,
        source_id=source_id, stream_epoch=stream_epoch, observed_at=12345.5,
        layout_version=1, frame_content_signature=sha256_bytes(bgr.tobytes()),
        is_repeat=False, is_black=False)


@unittest.skipUnless(cv2_available(), "optional vision dependencies unavailable")
class TestModelAdapter(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bgr, self.loaded = scene()
        glyphs = extract_glyphs(self.bgr)
        self.assertEqual(len(glyphs), 2)
        np = load_numpy()
        junk = np.zeros((24, 24), dtype=np.uint8)
        junk[9:15, :] = 255
        RankClassifier(k=1, min_vote=1, min_margin=0.01, min_similarity=0.9).fit(
            [(glyphs[0].mask, "8"), (junk, "junk")], angles=(0,),
            style_id="test-style", label_review_status="synthetic").save(self.root / "model")
        self.adapter = TrainedModelAdapter(self.root / "model", style_id="test-style")

    def test_offline_live_replay_use_same_loaded_model_and_candidates(self):
        image = self.root / "frame.png"
        write_png_rgb(image, self.loaded.width, self.loaded.height, self.loaded.rgb)
        offline = recognize_path(image, layout=layout(), adapter=self.adapter)
        style = LiveStyle("test-style", {"seat": NormalizedBox(0, 0, 1, 1, "玩家1")})
        live = recognize_frame(packet(self.bgr), style, adapter=self.adapter)
        replay = RecognitionRuntime(self.adapter).recognize_loaded(
            self.loaded, layout=layout(), source_key="video:synthetic")
        def candidates(result):
            return [(o.bbox, o.accepted_rank(), [h.as_dict() for h in o.rank_candidates],
                     o.model_id, o.model_digest) for o in result.observations]
        self.assertEqual(candidates(offline), candidates(live))
        self.assertEqual(candidates(offline), candidates(replay))
        self.assertEqual([o.accepted_rank() for o in live.observations], ["8", "8"])
        self.assertEqual(len({o.observation_id for o in live.observations}), 2)
        self.assertTrue(all(o.captured_at == 12345.5 for o in live.observations))
        self.assertEqual(live.review_status, REVIEW_PENDING)
        self.assertFalse(live.as_dict()["writes_ledger"])
        self.assertIn("未通过独立人工真值验收", " ".join(live.warnings))

    def test_wrong_style_and_tampered_model_are_explicit_errors(self):
        with self.assertRaises(ImageRejected):
            TrainedModelAdapter(self.root / "model", style_id="another-style")
        wrong = copy.deepcopy(layout())
        object.__setattr__(wrong, "style_id", "another-style")
        with self.assertRaises(ImageRejected):
            recognize_loaded(self.loaded, layout=wrong, adapter=self.adapter)
        manifest_path = self.root / "model" / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["min_margin"] = 0.9
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(ImageRejected):
            TrainedModelAdapter(self.root / "model", style_id="test-style")

    def test_model_change_withdraws_inflight_result(self):
        self._assert_inflight_withdrawn(lambda rt: rt.select_model(None))

    def test_source_change_withdraws_inflight_result(self):
        self._assert_inflight_withdrawn(lambda rt: rt.invalidate("切换窗口"))

    def test_roi_change_withdraws_inflight_result(self):
        self._assert_inflight_withdrawn(lambda rt: rt.invalidate("重新标定 ROI"))

    def _assert_inflight_withdrawn(self, switch):
        runtime = RecognitionRuntime(self.adapter)
        initial = runtime.recognize_loaded(self.loaded, layout=layout(), source_key="one")
        self.assertIs(runtime.current_result, initial)
        started, release = threading.Event(), threading.Event()
        output, errors = [], []
        original = self.adapter.recognize
        def slow(*args):
            started.set()
            self.assertTrue(release.wait(5))
            return original(*args)
        def run():
            try:
                output.append(runtime.recognize_loaded(self.loaded, layout=layout(), source_key="one"))
            except BaseException as exc:
                errors.append(exc)
        with patch.object(self.adapter, "recognize", side_effect=slow):
            worker = threading.Thread(target=run)
            worker.start()
            try:
                self.assertTrue(started.wait(5))
                old_generation = runtime.generation
                switch(runtime)
                self.assertGreater(runtime.generation, old_generation)
                self.assertIsNone(runtime.current_result)
            finally:
                release.set()
                worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(output, [None])
        self.assertIsNone(runtime.current_result)

    def test_actual_roi_and_source_context_changes_advance_generation(self):
        runtime = RecognitionRuntime(self.adapter)
        runtime.recognize_loaded(self.loaded, layout=layout(), source_key="one")
        first = runtime.generation
        runtime.recognize_loaded(self.loaded, layout=layout(), source_key="two")
        self.assertGreater(runtime.generation, first)
        second = runtime.generation
        smaller = layout()
        smaller.regions["seat"] = RegionBox(0, 0, 120, 130, "玩家1")
        result = runtime.recognize_loaded(self.loaded, layout=smaller, source_key="two")
        self.assertGreater(runtime.generation, second)
        self.assertEqual(len(result.observations), 2, "Unassigned outputs cannot disappear from review")
        self.assertEqual(result.observations[1].region_id, "unassigned")
        self.assertIsNone(result.observations[1].seat_hint)

    def test_repeated_frames_and_two_equal_ranks_preserve_distinct_tracks(self):
        tracker = FrameTracker()
        ids = None
        for index in range(40):
            result = recognize_loaded(self.loaded, layout=layout(), adapter=self.adapter)
            tracker.apply_to_result(result, index, index * 100)
            current = [o.observation_id for o in result.observations]
            self.assertEqual(len(set(current)), 2)
            if ids is None:
                ids = current
                tracker.mark_committed(ids[0])
                tracker.mark_committed(ids[1])
            self.assertEqual(current, ids)
        self.assertEqual(len(tracker.tracks), 2)
        self.assertEqual(list(tracker.ledger_candidates()), [])

    def test_forty_frames_manual_confirmation_debits_two_cards_only(self):
        from blackjack_lab.analysis.contracts import research_rules
        from blackjack_lab.ui.controller import SessionController
        from blackjack_lab.ui.vision_bridge import ConfirmDecision, OP_NEW, VisionReviewSession
        ctrl = SessionController(self.root / "manual.db")
        self.addCleanup(ctrl.close)
        ctrl.new_shoe(research_rules(6))
        ctrl.start_round(["玩家1"])
        tracker, session = FrameTracker(), None
        for index in range(40):
            result = recognize_loaded(self.loaded, layout=layout(), adapter=self.adapter)
            tracker.apply_to_result(result, index, index * 100)
            if session is None:
                session = VisionReviewSession(ctrl, result)
                self.assertEqual(ctrl.state().current.shoe.exact_out["8"], 0)
            else:
                session.present_frame(result)
            for obs in result.observations:
                outcome = session.confirm(ConfirmDecision(
                    obs.observation_id, OP_NEW, seat="玩家1", confirmed_rank="8"))
                self.assertEqual(outcome.status, "committed" if index == 0 else "duplicate")
                tracker.mark_committed(obs.observation_id)
        self.assertEqual(ctrl.state().current.shoe.exact_out["8"], 2)
        self.assertEqual(ctrl.state().current.table.players["玩家1"].hands[0].ranks, ["8", "8"])

    def test_new_round_same_positions_debits_new_cards_once(self):
        from blackjack_lab.analysis.contracts import research_rules
        from blackjack_lab.ui.controller import SessionController
        from blackjack_lab.ui.vision_bridge import ConfirmDecision, OP_NEW, VisionReviewSession
        ctrl = SessionController(self.root / "rounds.db")
        self.addCleanup(ctrl.close)
        ctrl.new_shoe(research_rules(6))
        tracker, first_ids = FrameTracker(), None
        for round_index in range(2):
            ctrl.start_round(["玩家1"])
            key = ctrl.state().current.round_id
            self.assertTrue(tracker.confirm_round_boundary(key))
            self.assertFalse(tracker.confirm_round_boundary(key))
            result = recognize_loaded(self.loaded, layout=layout(), adapter=self.adapter)
            tracker.apply_to_result(result, 10 + round_index * 1000, round_index * 100000)
            ids = [obs.observation_id for obs in result.observations]
            if first_ids is None:
                first_ids = ids
            else:
                self.assertTrue(set(first_ids).isdisjoint(ids))
            session = VisionReviewSession(ctrl, result)
            for obs in result.observations:
                outcome = session.confirm(ConfirmDecision(
                    obs.observation_id, OP_NEW, seat="玩家1", confirmed_rank="8"))
                self.assertEqual(outcome.status, "committed")
                tracker.mark_committed(obs.observation_id)
            tracker.ingest(11 + round_index * 1000, round_index * 100000 + 1000, [])
            self.assertEqual(ctrl.state().current.shoe.exact_out["8"], (round_index + 1) * 2)
            ctrl.end_round_unsettled("人工确认新轮边界测试", "complete")

    def test_tk_bind_new_controller_round_switches_tracks_explicitly(self):
        from blackjack_lab.analysis.contracts import research_rules
        from blackjack_lab.ui.app import BlackjackLabApp
        from blackjack_lab.ui.vision_bridge import VisionReviewSession
        app = BlackjackLabApp(self.root / "ui-round.db")
        self.addCleanup(app.on_close)
        app.ctrl.new_shoe(research_rules(6))
        app.ctrl.start_round(["玩家1"])
        app.act_open_vision()
        win = app._vision_win
        win._source_key = "video:synthetic"
        win.tracker = FrameTracker()
        win.tracker.confirm_round_boundary(win._tracker_round_key({
            "session_id": app.ctrl.session_id, "shoe_id": app.ctrl.state().current.shoe_id,
            "round_id": app.ctrl.state().current.round_id}))
        result = recognize_loaded(self.loaded, layout=layout(), adapter=self.adapter)
        win.tracker.apply_to_result(result, 0, 0)
        previous = VisionReviewSession(app.ctrl, result)
        win.session = app.vision_session = previous
        old_ids = [obs.observation_id for obs in result.observations]
        for oid in old_ids:
            win.tracker.mark_committed(oid)
        win.video_reader = SimpleNamespace(close=lambda: None)
        app.ctrl.end_round_unsettled("测试新轮", "complete")
        app.ctrl.start_round(["玩家1"])
        with patch.object(win, "recognize_current_frame") as recognize:
            win.rebind()
        recognize.assert_called_once()
        self.assertTrue(previous.invalidated_reason)
        self.assertEqual(win.tracker.tracks, [])
        next_frame = recognize_loaded(self.loaded, layout=layout(), adapter=self.adapter)
        win.tracker.apply_to_result(next_frame, 100, 100000)
        self.assertTrue(set(old_ids).isdisjoint(o.observation_id for o in next_frame.observations))

    def test_tk_model_selection_withdraws_review_and_displays_identity(self):
        from blackjack_lab.ui.app import BlackjackLabApp
        from blackjack_lab.ui.vision_bridge import VisionReviewSession
        app = BlackjackLabApp(self.root / "ui.db")
        self.addCleanup(app.on_close)
        app.act_open_vision()
        win = app._vision_win
        style_file = self.root / "style.json"
        style_file.write_text(json.dumps({
            "layout_profile_id": "test-style", "style_id": "test-style",
            "canvas": {"width": 240, "height": 130}, "felt_kind": "navy",
            "regions": {"seat": {"x": 0, "y": 0, "w": 240, "h": 130, "seat_hint": "玩家1"}}
        }), encoding="utf-8")
        win.select_style_path(style_file)
        win.select_model_path(self.root / "model")
        self.assertIn(self.adapter.model_id, win.var_model.get())
        self.assertIn("开发模型 / 仅候选", win.var_model.get())
        result = win.runtime.recognize_loaded(self.loaded, layout=layout(), source_key="test")
        previous = VisionReviewSession(app.ctrl, result)
        win.session = app.vision_session = previous
        win.loaded = self.loaded
        win._render_observations()
        app.update()
        self.assertEqual(len(win.cmb_obs.cget("values")), 2)
        generation = win.runtime.generation
        win.use_templates()
        self.assertGreater(win.runtime.generation, generation)
        self.assertIsNone(win.session)
        self.assertIsNone(app.vision_session)
        self.assertIsNone(win.runtime.current_result)
        self.assertFalse(previous.has_pending())
        self.assertTrue(previous.invalidated_reason)
        self.assertEqual(win.var_obs.get(), "")


class TestReviewWithdrawal(unittest.TestCase):
    def test_empty_frames_do_not_automatically_reset_committed_identity(self):
        from tests.test_vision_bridge import _obs, _result
        tracker = FrameTracker()
        original = tracker.apply_to_result(_result(_obs("f" * 32)), 0, 0)
        oid = original.observations[0].observation_id
        tracker.mark_committed(oid)
        tracker.ingest(1, 1000, [])
        restored = tracker.apply_to_result(_result(_obs("a" * 32)), 100, 100000)
        self.assertEqual(restored.observations[0].observation_id, oid)
        self.assertEqual(list(tracker.ledger_candidates()), [])
        tracker.confirm_round_boundary("explicit-new-round")
        new = tracker.apply_to_result(_result(_obs("a" * 32)), 100, 100000)
        self.assertNotEqual(new.observations[0].observation_id, oid)
        tracker.confirm_round_boundary("initial")
        self.assertEqual(tracker.tracks[0].observation_id, oid)
        self.assertTrue(tracker.tracks[0].committed)

    def test_stale_session_cannot_confirm_after_model_or_source_switch(self):
        from blackjack_lab.ui.vision_bridge import ConfirmDecision, OP_NEW, VisionBridgeError, VisionReviewSession
        from tests.test_vision_bridge import _obs, _result
        ctrl = SimpleNamespace(session_id="s", state=lambda: SimpleNamespace(current=None))
        session = VisionReviewSession(ctrl, _result(_obs("f" * 32)))
        session.invalidate("切换模型")
        self.assertFalse(session.has_pending())
        with self.assertRaisesRegex(VisionBridgeError, "旧候选已撤回"):
            session.confirm(ConfirmDecision("f" * 32, OP_NEW, seat="玩家1", confirmed_rank="8"))

    def test_previous_frame_candidate_is_not_confirmable_using_current_crop(self):
        from blackjack_lab.ui.vision_bridge import ConfirmDecision, OP_NEW, VisionBridgeError, VisionReviewSession
        from tests.test_vision_bridge import _obs, _result
        ctrl = SimpleNamespace(session_id="s", state=lambda: SimpleNamespace(current=None))
        session = VisionReviewSession(ctrl, _result(_obs("f" * 32)))
        session.present_frame(_result(_obs("a" * 32)))
        with self.assertRaisesRegex(VisionBridgeError, "未知观察"):
            session.confirm(ConfirmDecision("f" * 32, OP_NEW, seat="玩家1", confirmed_rank="8"))


if __name__ == "__main__":
    unittest.main()
