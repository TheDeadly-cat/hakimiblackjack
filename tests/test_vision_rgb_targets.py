import unittest
from blackjack_lab.vision.deps import cv2_available
from blackjack_lab.vision.rgb_corner_model import make_targets


@unittest.skipUnless(cv2_available(),'optional numpy dependency')
class PartialDetectorTargetsTests(unittest.TestCase):
    def test_known_small_window_is_rejected_as_uncalibrated_input(self):
        from blackjack_lab.vision.rgb_corner_adapter import require_calibrated_input
        from blackjack_lab.vision.deps import ImageRejected
        require_calibrated_input(1850,520)
        with self.assertRaisesRegex(ImageRejected,'1180×282'):
            require_calibrated_input(1180,282)

    def test_unlabelled_card_region_never_receives_background_loss(self):
        t=make_targets(128,128,[[20,20,24,20]],[[70,20,16,16]])
        self.assertEqual(t['heatmap'][0,7,8],1)
        self.assertEqual(t['valid'][0,7,8],1)
        self.assertEqual(t['valid'][0,6,19],1)
        self.assertEqual(t['valid'][0,24,24],0)
        self.assertEqual(float(t['regression'].sum()),1)

    def test_only_explicit_complete_empty_frame_is_all_background(self):
        self.assertEqual(float(make_targets(64,64,[])['valid'].sum()),0)
        self.assertEqual(float(make_targets(64,64,[],complete=True)['valid'].sum()),256)

    def test_duplicate_label_centers_are_not_silently_overwritten(self):
        with self.assertRaises(ValueError):make_targets(64,64,[[20,20,24,20],[20,20,24,20]])


if __name__=='__main__':unittest.main()
