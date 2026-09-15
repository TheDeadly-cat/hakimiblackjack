import unittest

from scripts.train_rgb_corner_detector import select_training_sessions
from blackjack_lab.vision.rgb_corner_model import detector_training_sources


class ExpandedDetectorSourceTests(unittest.TestCase):
    def fixture(self):
        return {'sessions':[{'title':'first','source_sha256':'source-a'},
                            {'title':'second','source_sha256':'source-b'},
                            {'title':'validation','source_sha256':'source-c'}]}

    def test_explicit_multiple_training_sources_retain_order_and_separate_validation(self):
        training,validation=select_training_sessions(self.fixture(),['first','second'],'validation','reserved')
        self.assertEqual([s['source_sha256'] for s in training],['source-a','source-b'])
        self.assertEqual(validation['source_sha256'],'source-c')

    def test_additional_source_cannot_leak_into_validation_or_reserved_under_an_alias(self):
        for mode in ['same_validation','reserved','alias','duplicate_title','unknown','empty_source']:
            bundle=self.fixture();titles=['first','second'];validation='validation';reserved='reserved'
            if mode=='same_validation':validation='second'
            elif mode=='reserved':reserved='source-b'
            elif mode=='alias':bundle['sessions'][2]['source_sha256']='source-b'
            elif mode=='duplicate_title':titles.append('second')
            elif mode=='unknown':titles.append('missing')
            elif mode=='empty_source':bundle['sessions'][1]['source_sha256']=''
            with self.subTest(mode=mode),self.assertRaises(ValueError):
                select_training_sessions(bundle,titles,validation,reserved)

    def test_evaluation_training_identity_includes_every_source_and_accepts_legacy(self):
        self.assertEqual(detector_training_sources({'source_sha256':'source-a'}),{'source-a'})
        self.assertEqual(detector_training_sources({'source_sha256':None,
                         'training_source_sha256s':['source-a','source-b']}),{'source-a','source-b'})
        for manifest in [{'source_sha256':'source-a','training_source_sha256s':['source-b']},
                         {'source_sha256':None,'training_source_sha256s':[]},
                         {'source_sha256':None,'training_source_sha256s':['source-a','source-a']},
                         {'source_sha256':None,'training_source_sha256s':'source-a'},{}]:
            with self.subTest(manifest=manifest),self.assertRaises(ValueError):detector_training_sources(manifest)


if __name__=='__main__':unittest.main()
