import copy
import unittest
from types import SimpleNamespace

from scripts.train_rgb_corner_detector import apply_orientation_review


class OrientationSupervisionTests(unittest.TestCase):
    def fixture(self):
        entries=[dict(path='frames/one.png',sha256='frame',source_sha256='source',
                      image=SimpleNamespace(shape=(100,200,3)),
                      positives=[[20,20,20,16],[70,20,20,16]],negatives=[],complete=False)]
        metadata=dict(source_sha256='source',annotation_sha256='labels',positives=2,negatives=0)
        document=dict(schema='detector-orientation-review-1',provenance='assistant_visual_review',
                      human_confirmed=False,source_sha256='source',base_annotation_sha256='labels',
                      decisions=[dict(file='one.png',frame_sha256='frame',bbox=[70,20,20,16],
                                      action='exclude_lower_corner',reason='Visible opposite end of the same card')])
        return entries,metadata,document

    def test_overlay_preserves_originals_and_does_not_claim_human_orientation_truth(self):
        entries,metadata,document=self.fixture()
        document['decisions'].append(dict(file='one.png',frame_sha256='frame',bbox=[120,60,20,16],
                                          action='add_lower_corner_negative',reason='Visible lower index'))
        updated,result=apply_orientation_review(entries,metadata,document)
        self.assertEqual(entries[0]['positives'],[[20,20,20,16],[70,20,20,16]])
        self.assertEqual(entries[0]['negatives'],[])
        self.assertEqual(updated[0]['positives'],[[20,20,20,16]])
        self.assertEqual(updated[0]['negatives'],[[70,20,20,16],[120,60,20,16]])
        self.assertIs(updated[0]['image'],entries[0]['image'])
        self.assertFalse(updated[0]['complete'])
        self.assertFalse(result['orientation_review_human_confirmed'])
        self.assertEqual((result['positives'],result['negatives']),(1,2))
        self.assertEqual(metadata['positives'],2)

    def test_wrong_source_frame_labels_or_provenance_fail_closed(self):
        for field,value in [('source_sha256','other'),('base_annotation_sha256','other'),
                            ('human_confirmed',True),('provenance','human_reviewed')]:
            entries,metadata,document=self.fixture();document[field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):
                apply_orientation_review(entries,metadata,document)
        entries,metadata,document=self.fixture();document['decisions'][0]['frame_sha256']='other'
        with self.assertRaises(ValueError):apply_orientation_review(entries,metadata,document)

    def test_conflicting_or_shifted_decisions_do_not_partially_mutate_input(self):
        for mode in ('duplicate','shifted','overlap','outside','boolean','unsupported'):
            entries,metadata,document=self.fixture()
            second=copy.deepcopy(document['decisions'][0])
            if mode=='shifted':second['bbox'][0]+=1
            if mode=='overlap':second.update(action='add_lower_corner_negative',bbox=[25,20,10,16])
            if mode=='outside':second.update(action='add_lower_corner_negative',bbox=[190,20,20,16])
            if mode=='boolean':second['bbox'][0]=True
            if mode=='unsupported':second['action']='make_positive'
            document['decisions'].append(second)
            with self.subTest(mode=mode),self.assertRaises(ValueError):
                apply_orientation_review(entries,metadata,document)
            self.assertEqual(entries[0]['positives'],[[20,20,20,16],[70,20,20,16]])
            self.assertEqual(entries[0]['negatives'],[])


if __name__=='__main__':unittest.main()
