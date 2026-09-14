import copy
import unittest
from scripts.prepare_rank_review_update import select_rows


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


if __name__=='__main__':unittest.main()
