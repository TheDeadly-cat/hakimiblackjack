"""Native crop ownership, coordinate mapping and duplicate suppression."""
import unittest
from blackjack_lab.vision.rgb_corner_model import native_tiles,predict_boxes_tiled
try:
    import numpy as np
    import torch
except ImportError:torch=None


class TileOwnershipTests(unittest.TestCase):
    def test_fixed_grid_covers_every_pixel_once_without_cropping_or_resizing_source(self):
        tiles=native_tiles(1850,520)
        self.assertEqual(len(tiles),33)
        for tile in tiles:
            self.assertTrue(0<=tile['x']<=1530 and 0<=tile['y']<=200)
            left,top,right,bottom=tile['owner']
            self.assertTrue(tile['x']<=left<right<=tile['x']+320)
            self.assertTrue(tile['y']<=top<bottom<=tile['y']+320)
        self.assertEqual(sum((t['owner'][2]-t['owner'][0])*(t['owner'][3]-t['owner'][1]) for t in tiles),1850*520)
        for x in (0,239.9,240,399.9,400,1645,1849.9):
            for y in (0,239.9,240,339.9,340,519.9):
                self.assertEqual(sum(l<=x<r and top<=y<b for l,top,r,b in [t['owner'] for t in tiles]),1)
        with self.assertRaises(ValueError):native_tiles(1180,282)

    @unittest.skipIf(torch is None,'optional local PyTorch dependency')
    def test_overlapping_native_tiles_return_one_global_box_per_marker(self):
        class MarkerModel(torch.nn.Module):
            def forward(self,x):
                centers=torch.nn.functional.max_pool2d(x[:,0:1],4)*24-12
                shape=(x.shape[0],2,80,80)
                return centers,torch.full(shape,float(np.log(4))),torch.zeros(shape)
        rgb=np.zeros((520,640,3),dtype=np.uint8)
        markers=[(238,238),(402,338),(550,430)]
        for x,y in markers:rgb[y,x,0]=255
        original=rgb.copy();stats={}
        boxes=predict_boxes_tiled(MarkerModel(),rgb,device='cpu',stats=stats)
        self.assertEqual(len(boxes),3)
        self.assertEqual({(b['bbox']['x']+b['bbox']['w']/2,b['bbox']['y']+b['bbox']['h']/2) for b in boxes},set(markers))
        self.assertTrue(np.array_equal(rgb,original))
        self.assertFalse(stats['detector_whole_frame_resized'])
        self.assertEqual(stats['detector_truncated_candidates'],0)
