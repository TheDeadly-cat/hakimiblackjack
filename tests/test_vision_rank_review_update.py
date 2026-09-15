import copy
import unittest
from types import SimpleNamespace
from scripts.prepare_rank_review_update import select_rows,as_crop_observation,geometry_corrections,expand_reviewed_crop
from blackjack_lab.vision.glyph_dataset import sample_origin_id
from blackjack_lab.vision.deps import cv2_available


class ReviewedRankSelectionTests(unittest.TestCase):
    def fixture(self):
        def row(identifier,x,label):
            return dict(crop_id=identifier,frame='one.png',bbox=[x,20,20,16],label=label,
                        label_provenance='human_reviewed',source_sha256='source',frame_sha256='frame')
        rows=[('base',row('upper',20,'A')),('base',row('lower',70,'A')),
              ('base',row('junk',110,'junk')),('supplement',row('new-upper',150,'Q'))]
        selected={('one.png',(20,20,20,16)):('A','frame'),('one.png',(150,20,20,16)):('Q','frame')}
        return rows,selected

    def test_completed_supplements_are_included_and_lower_crop_is_excluded_without_relabelling(self):
        rows,selected=self.fixture();before=copy.deepcopy(rows)
        kept,excluded=select_rows(rows,selected,'source')
        self.assertEqual([r['crop_id'] for _,r in kept],['upper','junk','new-upper'])
        self.assertEqual([(r['crop_id'],r['original_label']) for r in excluded],[('lower','A')])
        self.assertEqual(rows,before)

    def test_missing_conflicting_duplicate_and_unreviewed_material_fail(self):
        for mode in ('missing','wrong_rank','wrong_frame','wrong_source','unreviewed','duplicate'):
            rows,selected=self.fixture()
            if mode=='missing':rows.pop()
            elif mode=='duplicate':rows.append(copy.deepcopy(rows[-1]))
            else:
                field,value={'wrong_rank':('label','K'),'wrong_frame':('frame_sha256','other'),
                    'wrong_source':('source_sha256','other'),'unreviewed':('label_provenance','assistant_proposed')}[mode]
                rows[-1][1][field]=value
            with self.subTest(mode=mode),self.assertRaises(ValueError):select_rows(rows,selected,'source')

    def test_crop_only_projection_does_not_use_rank_as_identity_or_split_origin_aliases(self):
        first=dict(crop_id='dealer-k',origin_crop_id='dealer-k',physical_card_id='K',label='K')
        second=dict(crop_id='player-k',origin_crop_id='player-k',physical_card_id='K',label='K')
        a,b=as_crop_observation(first),as_crop_observation(second)
        self.assertEqual(first['physical_card_id'],'K')
        self.assertEqual(a['source_declared_physical_card_id'],'K')
        self.assertEqual(a['label'],'K')
        self.assertEqual(a['physical_card_id'],'')
        self.assertNotEqual(sample_origin_id(SimpleNamespace(**a)),sample_origin_id(SimpleNamespace(**b)))
        copied=as_crop_observation(dict(first,crop_id='same-crop-copy'))
        self.assertEqual(sample_origin_id(SimpleNamespace(**a)),sample_origin_id(SimpleNamespace(**copied)))
        with self.assertRaises(ValueError):as_crop_observation(dict(first,physical_identity_confirmed=True))


class CropGeometryReviewTests(unittest.TestCase):
    def fixture(self):
        row=dict(crop_id='partial-k',origin_crop_id='original-observation',physical_card_id='',
                 frame='one.png',bbox=[14,5,10,16],label='K',label_provenance='human_reviewed',
                 source_sha256='source',frame_sha256='frame')
        decision=dict(crop_id='partial-k',label='K',frame='one.png',frame_sha256='frame',
                      original_bbox=[14,5,10,16],bbox=[6,5,18,16],reason='Include the visible missing left stem')
        document=dict(schema='rank-crop-geometry-review-1',provenance='assistant_visual_review',
                      human_confirmed=False,source_sha256='source',queue_sha256s={'base':'queue'},decisions=[decision])
        return row,document

    def test_expansion_preserves_input_and_requires_exact_review_context(self):
        row,document=self.fixture();before=copy.deepcopy(row)
        self.assertIn('partial-k',geometry_corrections([row],document,{'base':'queue'},'source'))
        self.assertEqual(row,before)
        for field,value in [('source_sha256','other'),('queue_sha256s',{}),('human_confirmed',True)]:
            with self.subTest(field=field),self.assertRaises(ValueError):
                geometry_corrections([row],dict(document,**{field:value}),{'base':'queue'},'source')

    def test_relabel_shrink_shift_duplicate_and_changed_frame_are_rejected(self):
        for change in ({'label':'A'},{'bbox':[16,5,8,16]},{'bbox':[26,5,18,16]},
                       {'bbox':[6,5,True,16]},{'frame_sha256':'other'},{'original_bbox':[1,2,3,4]}):
            row,document=self.fixture();document['decisions'][0].update(change)
            with self.subTest(change=change),self.assertRaises(ValueError):
                geometry_corrections([row],document,{'base':'queue'},'source')
        row,document=self.fixture();document['decisions']*=2
        with self.assertRaises(ValueError):geometry_corrections([row],document,{'base':'queue'},'source')

    @unittest.skipUnless(cv2_available(),'optional OpenCV/numpy dependency')
    def test_recrop_uses_source_pixels_preserves_origin_and_checks_source_digest(self):
        import tempfile,hashlib
        from pathlib import Path
        import cv2,numpy as np
        row,document=self.fixture();decision=document['decisions'][0]
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);frame=root/'one.png'
            bgr=np.full((30,35,3),255,dtype=np.uint8);bgr[7:19,7:9]=0;bgr[7:19,18:21]=0
            ok,encoded=cv2.imencode('.png',bgr);self.assertTrue(ok);frame.write_bytes(encoded.tobytes())
            raw=frame.read_bytes();row['frame_sha256']=hashlib.sha256(raw).hexdigest()
            updated=expand_reviewed_crop(row,decision,frame,root/'derived')
            self.assertEqual(updated['label'],'K');self.assertFalse(updated['geometry_human_confirmed'])
            self.assertEqual(updated['source_bbox'],row['bbox'])
            self.assertEqual(sample_origin_id(SimpleNamespace(**row)),sample_origin_id(SimpleNamespace(**updated)))
            crop=cv2.imread(updated['crop_file'])
            self.assertTrue(np.array_equal(crop,bgr[5:21,6:24]))
            self.assertEqual(frame.read_bytes(),raw)
            with self.assertRaises(ValueError):
                expand_reviewed_crop(dict(row,frame_sha256='changed'),decision,frame,root/'bad')


if __name__=='__main__':unittest.main()
