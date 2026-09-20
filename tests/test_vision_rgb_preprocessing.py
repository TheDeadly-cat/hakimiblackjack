import unittest
from blackjack_lab.vision.deps import cv2_available,ImageRejected
from blackjack_lab.vision.rgb_corner_adapter import (
    RgbCornerAdapter,extract_index_glyph,CROP_PREPROCESSING,NAVY_OTSU_PREPROCESSING,INPUT_PROFILE)
from blackjack_lab.vision.rgb_corner_model import ARCHITECTURE,TILED_POLICY


@unittest.skipUnless(cv2_available(),'optional OpenCV/numpy dependency')
class NativeCropPreprocessingTests(unittest.TestCase):
    def test_experimental_mask_excludes_navy_and_retains_faint_ink_without_changing_source(self):
        import numpy as np
        for ink in [(190,190,190),(180,180,240),(30,30,30)]:
            bgr=np.full((20,30,3),245,dtype=np.uint8)
            bgr[:,0:3]=[90,30,15];bgr[5:15,12:16]=ink;before=bgr.copy()
            g=extract_index_glyph(bgr,[0,0,30,20],preprocessing=NAVY_OTSU_PREPROCESSING)
            self.assertEqual(g.bbox,(0,0,30,20))
            self.assertEqual(int(g.mask[:,0:3].sum()),0)
            self.assertTrue(np.all(g.mask[5:15,12:16]>0))
            self.assertTrue(np.array_equal(before,bgr))

    def test_default_is_unchanged_and_white_crop_stays_empty(self):
        import numpy as np
        from blackjack_lab.vision.real_cards import manual_glyph
        bgr=np.full((20,30,3),245,dtype=np.uint8);bgr[:,0:3]=[90,30,15]
        default=extract_index_glyph(bgr,[0,0,30,20])
        self.assertTrue(np.array_equal(default.mask,manual_glyph(bgr,[0,0,30,20]).mask))
        self.assertTrue(np.all(default.mask[:,0:3]>0))
        white=np.full_like(bgr,245)
        self.assertEqual(extract_index_glyph(white,[0,0,30,20],preprocessing=NAVY_OTSU_PREPROCESSING).area,0)
        with self.assertRaises(ImageRejected):extract_index_glyph(bgr,[29,0,30,20])


class PreprocessingIdentityTests(unittest.TestCase):
    def test_explicit_policy_has_separate_digest_and_can_restore_original_identity(self):
        original=RgbCornerAdapter.__new__(RgbCornerAdapter)
        original.style_id='navy-live-felt-v1';original.inference_policy=TILED_POLICY
        original.crop_preprocessing=CROP_PREPROCESSING
        original._base_extraction_version=ARCHITECTURE+'+'+CROP_PREPROCESSING+'+'+INPUT_PROFILE
        original.extraction_version=original._base_extraction_version+'+'+TILED_POLICY
        original._detector_identity_bytes=b'unchanged-detector';original.rank_model_digest='unchanged-rank'
        original.detector=object();original.model=object();original.digest=original._combined_digest()
        changed=original.with_crop_preprocessing(NAVY_OTSU_PREPROCESSING)
        self.assertNotEqual(changed.digest,original.digest)
        self.assertIs(changed.detector,original.detector);self.assertIs(changed.model,original.model)
        self.assertEqual(original.crop_preprocessing,CROP_PREPROCESSING)
        self.assertEqual(changed.with_crop_preprocessing(CROP_PREPROCESSING).digest,original.digest)
        with self.assertRaises(ImageRejected):original.with_crop_preprocessing('unknown')
