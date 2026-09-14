import copy
import unittest
from types import SimpleNamespace
from scripts.prepare_rank_review_update import select_rows,as_crop_observation
from blackjack_lab.vision.glyph_dataset import sample_origin_id


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


if __name__=='__main__':unittest.main()
