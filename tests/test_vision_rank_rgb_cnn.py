import importlib.util
from pathlib import Path
import tempfile
import unittest

from blackjack_lab.vision.deps import cv2_available,ImageRejected
from blackjack_lab.vision.rank_rgb_cnn import RankRgbCnnClassifier,rgb_to_patch,create_network

HAS_TORCH=importlib.util.find_spec('torch') is not None and importlib.util.find_spec('torchvision') is not None


@unittest.skipUnless(cv2_available(),'optional OpenCV/numpy dependency')
class NativeRgbInputTests(unittest.TestCase):
    def test_native_queue_crops_verified_frame_and_preserves_label_origin(self):
        import json,hashlib
        import numpy as np
        from blackjack_lab.vision.image_io import write_png_rgb
        from scripts.prepare_native_rgb_queue import prepare
        from blackjack_lab.vision.glyph_dataset import load_queue
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'frames').mkdir()
            rgb=np.full((30,50,3),245,np.uint8);rgb[5:15,10:30]=[210,170,160]
            frame=root/'frames/frame.png';write_png_rgb(frame,50,30,rgb.tobytes())
            context=root/'context.png';write_png_rgb(context,50,30,rgb.tobytes())
            mask=root/'mask.png';write_png_rgb(mask,20,10,rgb[5:15,10:30].tobytes())
            sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
            row=dict(crop_id='a1',session='s',frame='frame.png',frame_sha256=sha(frame),source_sha256='1'*64,
                split='train',bbox=[10,5,20,10],ink='red',crop_file='context.png',crop_sha256=sha(context),
                mask_file='mask.png',mask_sha256=sha(mask),label='K',label_provenance='human_reviewed',origin_crop_id='original')
            queue=root/'queue.jsonl';queue.write_text(json.dumps(row)+'\n',encoding='utf-8')
            bundle=root/'bundle.json';bundle.write_text(json.dumps({'sessions':[{'session':str(root),'source_sha256':'1'*64}]}))
            receipt=prepare(queue,bundle,root/'new');item=load_queue(root/'new/queue.jsonl')[0]
            from blackjack_lab.vision.rank_rgb_cnn import read_native_rgb_item
            self.assertTrue(np.array_equal(read_native_rgb_item(item),rgb[5:15,10:30]))
            self.assertEqual((item.label,item.bbox,item.origin_crop_id),('K',(10,5,20,10),'original'))
            self.assertEqual(sha(context),row['crop_sha256']);self.assertTrue(receipt['labels_boxes_origins_unchanged'])
            queue.write_text(json.dumps(dict(row,crop_id='../escaped'))+'\n',encoding='utf-8')
            with self.assertRaises(ValueError):prepare(queue,bundle,root/'escaped-output')
            self.assertFalse((root/'escaped-output').exists())
            queue.write_text((json.dumps(row)+'\n')*2,encoding='utf-8')
            with self.assertRaises(ValueError):prepare(queue,bundle,root/'duplicate-output')
            self.assertFalse((root/'duplicate-output').exists())
            queue.write_text(json.dumps(row)+'\n',encoding='utf-8')
            frame.write_bytes(b'changed')
            with self.assertRaises(ValueError):prepare(queue,bundle,root/'tampered')
            self.assertFalse((root/'tampered').exists())

    def test_adapter_delivers_exact_rgb_without_a_binary_mask(self):
        import numpy as np
        from unittest.mock import patch
        from blackjack_lab.vision.rgb_corner_adapter import extract_index_glyph,RAW_RGB_PREPROCESSING
        from blackjack_lab.vision.model_adapter import TrainedModelAdapter
        from blackjack_lab.vision.rank_classifier import RankGuess
        from blackjack_lab.vision.contracts import LayoutProfile,RegionBox,REVIEW_PENDING
        from blackjack_lab.vision.image_io import LoadedImage,sha256_bytes
        rgb=np.full((30,70,3),245,np.uint8);rgb[8:15,10:16]=[210,170,170]
        raw=rgb.tobytes();loaded=LoadedImage(Path('synthetic.png'),70,30,sha256_bytes(raw),raw,len(raw),'synthetic')
        class RawModel:
            input_kind='native_rgb_crop'
            def predict_rgb_crops(inner,crops,**kwargs):
                self.assertEqual(len(crops),2)
                self.assertTrue(np.array_equal(crops[0],rgb[5:20,5:25]))
                return [RankGuess(raw_label='K',rank='K',score=.99,margin=.98,accepted=True)]*2
            def predict_masks(inner,*args,**kwargs):self.fail('RGB passed through mask input')
        adapter=object.__new__(TrainedModelAdapter);adapter.model=RawModel()
        adapter.model_id='synthetic-rgb';adapter.digest='1'*64;adapter.style_id='synthetic'
        adapter.feature_version='native-rgb';adapter.extraction_version='native-rgb';adapter.training_digest='2'*64
        with patch('blackjack_lab.vision.real_cards.manual_glyph',side_effect=AssertionError('mask extraction called')):
            glyphs=[extract_index_glyph(rgb[:,:,::-1],box,preprocessing=RAW_RGB_PREPROCESSING)
                    for box in ([5,5,20,15],[40,5,20,15])]
        self.assertTrue(all(g.mask is None for g in glyphs))
        layout=LayoutProfile('synthetic','synthetic',70,30,{'seat':RegionBox(0,0,70,30,'玩家1')})
        result=adapter._recognize_glyphs(loaded,layout,'observer_video',glyphs)
        self.assertEqual([o.accepted_rank() for o in result.observations],['K','K'])
        self.assertEqual(len({o.observation_id for o in result.observations}),2)
        self.assertEqual(result.review_status,REVIEW_PENDING)
        self.assertFalse(result.as_dict()['writes_ledger'])

    def test_faint_ink_and_color_survive_without_modifying_source(self):
        import numpy as np
        rgb=np.full((20,30,3),245,dtype=np.uint8);rgb[5:15,10:13]=[190,190,190]
        rgb[6:12,20:23]=[230,160,160];before=rgb.copy();patch=rgb_to_patch(rgb)
        self.assertEqual(patch.shape,(3,64,64));self.assertEqual(patch.dtype,np.float32)
        self.assertFalse(np.array_equal(patch,rgb_to_patch(np.full_like(rgb,245))))
        self.assertTrue(np.array_equal(rgb,before))
        with self.assertRaises(ImageRejected):rgb_to_patch(rgb[:,:,0])
        with self.assertRaises(ImageRejected):rgb_to_patch(rgb.astype(np.float32))

    def test_crop_evaluation_reads_rgb_and_keeps_missing_inputs_in_denominator(self):
        from types import SimpleNamespace
        import numpy as np
        from blackjack_lab.vision.image_io import write_png_rgb
        from blackjack_lab.vision.rank_classifier import evaluate_items,RankGuess
        class RawModel:
            input_kind='native_rgb_crop'
            def predict_rgb_crops(inner,crops):
                self.assertEqual(crops[0][0,0].tolist(),[210,170,160])
                return [RankGuess(raw_label='K',rank='K',score=.99,margin=.98,accepted=True)]
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);rgb=np.full((20,30,3),[210,170,160],np.uint8)
            write_png_rgb(root/'crop.png',30,20,rgb.tobytes())
            rows=[SimpleNamespace(label='K',crop_id=str(i),session='synthetic',round_id='one',
                  bbox=(0,0,30,20),crop_file=name,mask_file='deliberately-no-mask.png') for i,name in enumerate(['crop.png','missing.png'])]
            report=evaluate_items(RawModel(),rows,root)
            self.assertEqual(report['n_identifiable'],2);self.assertEqual(report['accepted_correct'],1)
            self.assertEqual(report['n_invalid'],1);self.assertFalse(report['valid'])
            self.assertEqual(report['rows'][1]['error'],'missing_rgb_crop')
            rows[0].bbox=(0,0,12,10)
            report=evaluate_items(RawModel(),rows[:1],root)
            self.assertEqual(report['n_invalid'],1)
            self.assertEqual(report['rows'][0]['error'],'prediction_error:ImageRejected')


@unittest.skipUnless(HAS_TORCH and cv2_available(),'optional PyTorch/torchvision/vision dependencies')
class NativeRgbModelTests(unittest.TestCase):
    def model(self,network):
        return RankRgbCnnClassifier(network,style_id='navy-live-felt-v1',training_digest='1'*64,
                                   plan_digest='2'*64,initialization_digest='3'*64,label_review_status='synthetic')

    def test_raw_rgb_entry_rejects_masks_constant_pixels_and_nonfinite_scores(self):
        import numpy as np,torch
        from blackjack_lab.vision.glyph_dataset import LABEL_RANKS
        class Biased(torch.nn.Module):
            def forward(self,x):
                y=torch.full((len(x),len(LABEL_RANKS)),-10.,device=x.device);y[:,LABEL_RANKS.index('K')]=10.;return y
        model=self.model(Biased());rgb=np.full((20,30,3),245,np.uint8);rgb[5:15,10:14]=180
        guesses=model.predict_rgb_crops([rgb,np.full_like(rgb,245)])
        self.assertEqual(guesses[0].rank,'K');self.assertTrue(guesses[0].accepted)
        self.assertFalse(guesses[1].accepted)
        with self.assertRaises(ImageRejected):model.predict_masks([rgb[:,:,0]])
        class Nonfinite(torch.nn.Module):
            def forward(self,x):return torch.full((len(x),len(LABEL_RANKS)),float('nan'))
        with self.assertRaises(ImageRejected):self.model(Nonfinite()).predict_rgb_crops([rgb])

    def test_artifact_roundtrip_keeps_batchnorm_buffers_and_detects_changed_weights(self):
        import torch
        torch.set_num_threads(2)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'model';model=self.model(create_network(pretrained=False));model.save(root)
            restored=RankRgbCnnClassifier.load(root)
            self.assertEqual(restored.model_id,model.model_id)
            for name,value in model.network.state_dict().items():
                self.assertTrue(torch.equal(value,restored.network.state_dict()[name]),name)
            with self.assertRaises(FileExistsError):model.save(root)
            blob=root/'model.npz';raw=blob.read_bytes();blob.write_bytes(raw[:-1]+bytes([raw[-1]^1]))
            with self.assertRaises(ImageRejected):RankRgbCnnClassifier.load(root)


if __name__=='__main__':unittest.main()
