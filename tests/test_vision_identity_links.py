"""Explicit same-card links preserve ledger facts and survive review reopening."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from blackjack_lab.analysis.contracts import research_rules
from blackjack_lab.ledger.events import SOURCE_SIMULATOR
from blackjack_lab.ui.controller import SessionController
from blackjack_lab.ui.vision_bridge import (ConfirmDecision,OP_LINK,OP_NEW,OP_CORRECT,OP_REJECT,
    VisionBridgeError,VisionReviewSession)
from tests.test_vision_bridge import _obs,_result


class IdentityLinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.ctrl=SessionController(self.root/'ledger.db',recording_source=SOURCE_SIMULATOR)
        self.addCleanup(self.ctrl.close)
        self.ctrl.new_shoe(research_rules(6));self.ctrl.start_round(['玩家1'])
        self.a=self.ctrl.deal_shown('玩家1','A')
        self.b=self.ctrl.deal_shown('玩家1','A')

    def review(self,*observations):
        return VisionReviewSession(self.ctrl,_result(*observations),self.root/'evidence')

    def link(self,sess,oid,event):
        return sess.confirm(ConfirmDecision(oid,OP_LINK,target_event_id=event.event_id))

    def test_link_and_reload_do_not_debit_or_change_predictions(self):
        obs=_obs('new-visual-id','6')
        original=deepcopy(obs.as_dict());sess=self.review(obs)
        events=self.ctrl.ledger.to_list();revision=self.ctrl.commit_revision
        result=self.link(sess,obs.observation_id,self.a)
        self.assertEqual(result.status,'linked');self.assertFalse(result.already_saved)
        self.assertFalse(sess.has_pending());self.assertFalse(sess.has_committed())
        self.assertEqual(obs.as_dict(),original)
        self.assertEqual(self.ctrl.ledger.to_list(),events)
        self.assertEqual(self.ctrl.commit_revision,revision)
        repeated=self.link(sess,obs.observation_id,self.a)
        self.assertTrue(repeated.already_saved);self.assertEqual(repeated.request_id,result.request_id)
        restored=self.review(deepcopy(obs))
        self.assertIn(obs.observation_id,restored.links)
        with self.assertRaises(VisionBridgeError):
            restored.confirm(ConfirmDecision(obs.observation_id,OP_NEW,seat='玩家1',confirmed_rank='A'))
        self.assertEqual(self.ctrl.state().current.shoe.exact_out['A'],2)
        record=json.loads((self.root/'evidence'/'identity-links'/f'{result.request_id}.json').read_text())
        self.assertEqual(record['observation'],original)
        self.assertFalse(record['writes_ledger']);self.assertEqual(record['target']['rank'],'A')
        recovered=SessionController.recover(self.root/'ledger.db',self.ctrl.session_id)
        self.addCleanup(recovered.close)
        recovered_review=VisionReviewSession(recovered,_result(deepcopy(obs)),self.root/'evidence')
        self.assertIn(obs.observation_id,recovered_review.links)
        with self.assertRaises(VisionBridgeError):
            recovered_review.confirm(ConfirmDecision(obs.observation_id,OP_NEW,seat='玩家1',confirmed_rank='A'))

    def test_two_same_rank_candidates_cannot_share_one_current_target(self):
        sess=self.review(_obs('one'),_obs('two'))
        self.link(sess,'one',self.a)
        with self.assertRaises(VisionBridgeError):self.link(sess,'two',self.a)
        self.link(sess,'two',self.b)
        self.assertNotEqual(sess.links['one'].event.event_id,sess.links['two'].event.event_id)
        # An absent old visual ID can be replaced by another observation of the same card.
        sess.present_frame(_result(_obs('returned')))
        self.link(sess,'returned',self.a)
        self.assertEqual(self.ctrl.state().current.shoe.exact_out['A'],2)
        sess.present_frame(_result(_obs('one'),_obs('returned')))
        self.assertTrue(sess.has_pending())

    def test_targets_follow_current_split_hands_without_creating_new_cards(self):
        from blackjack_lab.core.table import ACTION_SPLIT
        self.ctrl.deal_shown('庄家','6');self.ctrl.deal_hidden('庄家')
        hand=self.ctrl.state().current.table.players['玩家1'].hands[0].hand_id
        self.ctrl.player_action('玩家1',hand,ACTION_SPLIT)
        sess=self.review(_obs('left'),_obs('right'))
        targets={t['event_id']:t for t in sess.existing_card_targets()}
        self.assertNotEqual(targets[self.a.event_id]['hand_id'],targets[self.b.event_id]['hand_id'])
        revision=self.ctrl.commit_revision
        self.link(sess,'left',self.a);self.link(sess,'right',self.b)
        self.assertEqual(self.ctrl.commit_revision,revision)
        self.assertEqual(self.ctrl.state().current.shoe.exact_out['A'],2)

    def test_reused_visual_id_on_changed_pixels_needs_fresh_manual_association(self):
        original=_obs('same-id');sess=self.review(original)
        first=self.link(sess,'same-id',self.a)
        changed=_obs('same-id','6');changed.asset_sha256='b'*64;changed.bbox['x']+=60
        sess.present_frame(_result(changed))
        self.assertTrue(sess.has_pending());self.assertFalse(sess.link_evidence_matches('same-id'))
        with self.assertRaises(VisionBridgeError):
            sess.confirm(ConfirmDecision('same-id',OP_CORRECT,seat='玩家1',confirmed_rank='6',target_event_id=self.a.event_id))
        revision=self.ctrl.commit_revision
        second=self.link(sess,'same-id',self.b)
        self.assertNotEqual(first.request_id,second.request_id)
        self.assertTrue(sess.link_evidence_matches('same-id'));self.assertFalse(sess.has_pending())
        self.assertEqual(self.ctrl.commit_revision,revision)
        # Reopening the original image must not silently inherit the later link.
        reopened=self.review(original)
        self.assertTrue(reopened.has_pending())
        with self.assertRaises(VisionBridgeError):
            reopened.confirm(ConfirmDecision('same-id',OP_NEW,seat='玩家1',confirmed_rank='A'))

    def test_replacement_and_rejection_keep_immutable_history(self):
        sess=self.review(_obs('one'))
        first=self.link(sess,'one',self.a)
        path=self.root/'evidence'/'identity-links'/f'{first.request_id}.json';saved=path.read_bytes()
        second=self.link(sess,'one',self.b)
        self.assertNotEqual(first.request_id,second.request_id);self.assertEqual(path.read_bytes(),saved)
        sess.confirm(ConfirmDecision('one',OP_REJECT))
        restored=self.review(_obs('one'))
        self.assertFalse(restored.links)
        self.assertEqual(self.ctrl.state().current.shoe.exact_out['A'],2)
        third=self.link(restored,'one',self.a)
        self.assertNotEqual(third.request_id,first.request_id)
        self.assertEqual(path.read_bytes(),saved)

    def test_voided_wrong_round_and_invalid_context_targets_are_rejected(self):
        sess=self.review(_obs('one'))
        self.link(sess,'one',self.b)
        self.ctrl.undo_last()
        self.assertTrue(sess.has_pending())
        with self.assertRaises(VisionBridgeError):self.link(sess,'one',self.b)
        with self.assertRaises(VisionBridgeError):sess.rebind_current_round()
        self.ctrl.end_round_unsettled('test','complete');self.ctrl.start_round(['玩家1'])
        with self.assertRaises(VisionBridgeError):self.link(sess,'one',self.a)
        new=self.review(_obs('two'))
        with self.assertRaises(VisionBridgeError):self.link(new,'two',self.a)

    def test_link_does_not_enable_a_second_new_card_after_correction(self):
        sess=self.review(_obs('one'))
        self.link(sess,'one',self.a)
        with self.assertRaises(VisionBridgeError):
            sess.confirm(ConfirmDecision('one',OP_LINK,target_event_id=self.a.event_id,confirmed_rank='K'))
        with self.assertRaises(VisionBridgeError):
            sess.confirm(ConfirmDecision('one',OP_CORRECT,seat='玩家1',confirmed_rank='K',target_event_id=self.b.event_id))
        sess.confirm(ConfirmDecision('one',OP_CORRECT,seat='玩家1',confirmed_rank='K',target_event_id=self.a.event_id))
        with self.assertRaises(VisionBridgeError):
            sess.confirm(ConfirmDecision('one',OP_NEW,seat='玩家1',confirmed_rank='K'))
        with self.assertRaises(VisionBridgeError):sess.confirm(ConfirmDecision('one',OP_REJECT))
        self.assertEqual(self.ctrl.state().current.shoe.exact_out['A'],1)
        self.assertEqual(self.ctrl.state().current.shoe.exact_out['K'],1)

    def test_failed_head_save_and_corrupt_evidence_do_not_silently_remove_protection(self):
        sess=self.review(_obs('one'));events=self.ctrl.ledger.to_list()
        with patch('blackjack_lab.ui.vision_bridge.os.replace',side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):self.link(sess,'one',self.a)
        self.assertFalse(sess.links);self.assertEqual(self.ctrl.ledger.to_list(),events)
        linked=self.link(sess,'one',self.a)
        path=self.root/'evidence'/'identity-links'/f'{linked.request_id}.json'
        path.write_text('{}',encoding='utf-8')
        with self.assertRaises(VisionBridgeError):self.review(_obs('one'))
        self.assertEqual(self.ctrl.ledger.to_list(),events)


if __name__=='__main__':unittest.main()
