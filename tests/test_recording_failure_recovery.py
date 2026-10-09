"""Corrupt or incomplete failure-review evidence never silently clears input."""
import json
from pathlib import Path
import unittest

from tests import test_recording_owner as fixture
from blackjack_lab.ui.recording_failures import acknowledge_review, load_failures


class RecordingFailureRecoveryTests(unittest.TestCase):
    setUp = fixture.TestRecordingOwner.setUp
    close_owner = fixture.TestRecordingOwner.close_owner
    results = fixture.TestRecordingOwner.results

    def reject(self):
        self.owner.submit(self.base, 'deal_shown', '玩家1', 'Z')
        receipt = self.results(1)[0]
        return Path(receipt.failure_path)

    def test_explicit_review_preserves_original_and_does_not_retry(self):
        path = self.reject(); before = path.read_bytes()
        faults = load_failures(self.path, self.ctrl.session_id)
        acknowledge_review(faults, self.base)
        self.assertFalse(load_failures(self.path, self.ctrl.session_id))
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.ctrl.store.load_ledger(self.ctrl.session_id).to_list(), self.base.to_list())

    def test_incomplete_review_marker_cannot_clear_failure(self):
        path = self.reject()
        faults = load_failures(self.path, self.ctrl.session_id)
        marker = dict(schema='blackjack-recording-review-v1', session_id=self.ctrl.session_id,
                      failure_sha256=faults[0]['failure_sha256'])
        path.with_suffix('.reviewed.json').write_text(json.dumps(marker), encoding='utf-8')
        self.assertEqual(len(load_failures(self.path, self.ctrl.session_id)), 1)
        path.with_suffix('.reviewed.json').write_text('[]', encoding='utf-8')
        self.assertEqual(len(load_failures(self.path, self.ctrl.session_id)), 1)

    def test_changed_or_damaged_original_requires_review_again(self):
        path = self.reject()
        acknowledge_review(load_failures(self.path, self.ctrl.session_id), self.base)
        data = json.loads(path.read_bytes()); data['error'] = 'changed original diagnostic'
        path.write_text(json.dumps(data), encoding='utf-8')
        self.assertEqual(len(load_failures(self.path, self.ctrl.session_id)), 1)
        path.write_text('[]', encoding='utf-8')
        faults = load_failures(self.path, self.ctrl.session_id)
        self.assertEqual(faults[0]['status'], 'unknown_commit_outcome')


if __name__ == '__main__':
    unittest.main()
