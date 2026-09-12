"""Bounded geometry rescue, separate review evidence, and unchanged rank model."""
import copy
import json
import unittest
from dataclasses import replace
from unittest.mock import patch

from blackjack_lab.vision.contracts import ContractError, result_from_dict
from blackjack_lab.vision.corner_policy import (
    LEGACY_UPPER_CORNER_POLICY, UPPER_CORNER_POLICY, UpperCornerSelector)
from blackjack_lab.vision.deps import cv2_available, load_cv2, load_numpy
from blackjack_lab.vision.model_adapter import TrainedModelAdapter
from blackjack_lab.vision.pipeline import recognize_loaded
from blackjack_lab.vision.tracker import FrameTracker
from tests import test_vision_corner_diagnostics as fixtures
from tests import test_vision_model_adapter as adapter_fixtures


@unittest.skipUnless(cv2_available(), "optional vision dependencies unavailable")
class LocalCornerGeometryTests(unittest.TestCase):
    def test_joined_white_body_above_does_not_hide_visible_left_corner(self):
        image = load_numpy().zeros((150,200,3),dtype="uint8")
        image[20:120,20:160] = 245
        box=[25,25,20,15]
        self.assertTrue(UpperCornerSelector(image,version=LEGACY_UPPER_CORNER_POLICY).assess(box)["keep"])
        image[0:20,20:160] = 245
        self.assertFalse(UpperCornerSelector(image,version=LEGACY_UPPER_CORNER_POLICY).assess(box)["keep"])
        decision = UpperCornerSelector(image).assess(box)
        self.assertEqual(decision["state"], "upper")
        self.assertEqual(decision["reason"], "upper_local_left_continuation")
        self.assertIn("local_rays_px",decision)

    def test_clear_bottom_right_excluded_but_bottom_left_ambiguity_never_auto_kept(self):
        image = load_numpy().zeros((150,200,3),dtype="uint8")
        image[20:120,20:160]=245
        selector=UpperCornerSelector(image)
        self.assertEqual(selector.assess([130,101,20,15])["state"],"lower")
        uncertain=selector.assess([25,101,20,15])
        self.assertEqual(uncertain["state"],"uncertain")
        self.assertFalse(uncertain["keep"])
        with self.assertRaises(ValueError):
            UpperCornerSelector(image,version="invented-version")


@unittest.skipUnless(cv2_available(), "optional vision dependencies unavailable")
class SeparateGeometryReviewTests(unittest.TestCase):
    setUp = fixtures.CornerStageTests.setUp

    def result(self):
        bgr=adapter_fixtures.scene()[0]
        cv2=load_cv2()
        # A left/bottom glyph is ambiguous; a right/bottom glyph is clearly lower.
        for x in (10,130):
            cv2.putText(bgr,"8",(x+14,112),cv2.FONT_HERSHEY_SIMPLEX,1,(0,0,0),3,cv2.LINE_8)
        rgb=bgr[:,:,::-1].copy().tobytes()
        loaded=replace(self.loaded,rgb=rgb,sha256=adapter_fixtures.sha256_bytes(rgb))
        return loaded,recognize_loaded(loaded,layout=adapter_fixtures.layout(),adapter=self.adapter)

    def test_policy_versions_change_adapter_digest_without_changing_model(self):
        old=TrainedModelAdapter(self.model,style_id="test-style",corner_policy_version=LEGACY_UPPER_CORNER_POLICY)
        self.assertEqual(old.model_id,self.adapter.model_id)
        self.assertNotEqual(old.digest,self.adapter.digest)
        self.assertEqual(self.adapter.corner_policy_version,UPPER_CORNER_POLICY)

    def test_uncertain_never_classified_tracked_or_accepted_in_result_json(self):
        with patch.object(self.adapter.model,"predict_mask",wraps=self.adapter.model.predict_mask) as predict:
            loaded,result=self.result()
        self.assertEqual(len(result.observations),2)
        self.assertEqual(len(result.geometry_review),2)
        self.assertEqual(predict.call_count,2)
        restored=result_from_dict(json.loads(result.to_json()))
        self.assertEqual(restored.geometry_review,result.geometry_review)
        tracker=FrameTracker()
        tracker.apply_to_result(result,0,0)
        self.assertEqual(len(tracker.tracks),2)
        for key,value in (("rank","8"),("accepted",True),("model_digest","other"),
                          ("classification_performed",True),("writes_ledger",True)):
            data=restored.as_dict()
            data["geometry_review"][0][key]=value
            with self.subTest(key=key),self.assertRaises(ContractError):
                result_from_dict(data)

    def test_existing_tk_panel_can_view_uncertain_and_withdraw_it_on_source_change(self):
        from blackjack_lab.ui.app import BlackjackLabApp
        from blackjack_lab.ui.vision_bridge import VisionReviewSession
        from blackjack_lab.analysis.contracts import research_rules
        app=BlackjackLabApp(self.root/"ui.db")
        self.addCleanup(app.on_close)
        app.ctrl.new_shoe(research_rules(6))
        app.ctrl.start_round(["玩家1"])
        app.act_open_vision()
        win=app._vision_win
        loaded,result=self.result()
        win.loaded=loaded
        win.session=VisionReviewSession(app.ctrl,result)
        win._render_observations()
        win.show_geometry_review()
        self.assertIsNotNone(win._geometry_dialog)
        ids=win.cmb_obs.cget("values")
        self.assertTrue(all(row["candidate_id"] not in ids for row in result.geometry_review))
        self.assertEqual(app.ctrl.state().current.shoe.exact_out["8"],0)
        win._withdraw("fixture source changed")
        self.assertIsNone(win._geometry_dialog)
        self.assertEqual(win.cmb_obs.cget("values"),"")
