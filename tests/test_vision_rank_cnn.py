"""Optional CNN artifact and rejection contracts; fixtures are not training evidence."""
from pathlib import Path
import tempfile
import unittest

try:
    import numpy as np
    import torch
except ImportError:
    torch=None

from blackjack_lab.vision.deps import ImageRejected
from blackjack_lab.vision.glyph_dataset import LABEL_RANKS
from blackjack_lab.vision.rank_cnn import RankCnnClassifier,create_network


@unittest.skipIf(torch is None,'optional local PyTorch dependency')
class CnnClassifierTests(unittest.TestCase):
    def make_model(self,label='8',logit=10.):
        network=create_network()
        with torch.no_grad():
            for parameter in network.parameters():parameter.zero_()
            network[-1].bias[LABEL_RANKS.index(label)]=logit
        return RankCnnClassifier(network,style_id='fixture',training_digest='a'*64,plan_digest='b'*64)

    def test_batch_outputs_are_independent_and_do_not_claim_probability_or_identity(self):
        model=self.make_model();mask=np.full((20,12),255,dtype=np.uint8)
        original=mask.copy();results=model.predict_masks([mask,mask])
        self.assertEqual([r.rank for r in results],['8','8'])
        self.assertTrue(np.array_equal(mask,original))
        results[0].rank='K';self.assertEqual(results[1].rank,'8')
        self.assertFalse(results[1].score_is_calibrated_probability)
        self.assertEqual(results[1].independent_votes,0)
        with self.assertRaises(ImageRejected):model.predict_mask(mask,angles=(180,))

    def test_junk_uncertainty_and_nonfinite_outputs_are_rejected(self):
        mask=np.full((20,12),255,dtype=np.uint8)
        self.assertEqual(self.make_model('junk').predict_mask(mask).rejection_reason,'junk')
        self.assertEqual(self.make_model(logit=.1).predict_mask(mask).rejection_reason,'cnn_low_score')
        self.assertEqual(self.make_model().predict_mask(np.zeros_like(mask)).rejection_reason,'empty_ink')
        model=self.make_model()
        with torch.no_grad():model.network[-1].bias[0]=float('nan')
        with self.assertRaises(ImageRejected):model.predict_mask(mask)

    def test_saved_weights_are_verified_and_existing_model_is_not_overwritten(self):
        from blackjack_lab.vision.model_adapter import TrainedModelAdapter
        mask=np.full((20,12),255,dtype=np.uint8)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'model';model=self.make_model();model.save(path)
            restored=RankCnnClassifier.load(path)
            self.assertEqual(restored.model_id,model.model_id)
            self.assertEqual(restored.predict_mask(mask).rank,'8')
            selected=TrainedModelAdapter(path,style_id='fixture')
            self.assertEqual(selected.model_id,model.model_id)
            with self.assertRaises(ImageRejected):TrainedModelAdapter(path,style_id='different')
            saved=(path/'model.npz').read_bytes()
            with self.assertRaises(FileExistsError):model.save(path)
            self.assertEqual((path/'model.npz').read_bytes(),saved)
            (path/'model.npz').write_bytes(saved+b'changed')
            with self.assertRaises(ImageRejected):RankCnnClassifier.load(path)
