"""O2 validates its whole source tree while online R4 keeps its fixed identity."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packaging/windows_trial'))
sys.path.insert(0, str(ROOT / 'packaging/windows_offline_trial'))
import offline_common
import trial_common


class OfflineO2IdentityTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        files = {}
        entries = {}
        for relative, data in (('blackjack_lab/__init__.py', b'# fixed synthetic source\n'),
                               ('README.md', '中文固定源码\n'.encode('utf-8'))):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            blob = trial_common.git_hash('blob', data)
            files[relative] = dict(sha256=hashlib.sha256(data).hexdigest(), git_blob=blob, git_mode='100644')
            entries[relative] = '100644', blob
        self.manifest = dict(commit=offline_common.COMMIT, git_tree=trial_common.tree_hash(entries), files=files)
        self.context = patch.multiple(offline_common, TREE=self.manifest['git_tree'], SOURCE_FILE_COUNT=2)
        self.context.start()
        self.addCleanup(self.context.stop)

    def test_valid_complete_tree_does_not_rebind_online_R4(self):
        before = trial_common.COMMIT, trial_common.TREE, trial_common.PACKAGE_ID
        offline_common.verify_installed_source(self.root, self.manifest)
        self.assertEqual((trial_common.COMMIT, trial_common.TREE, trial_common.PACKAGE_ID), before)
        self.assertEqual(trial_common.COMMIT, '3ec2006e82c4bdbc8da01bb0e1212b64c9111d2c')
        self.assertEqual(offline_common.COMMIT, 'dd0882c69cd89cc07f1d5b5365796fdfa9f1a719')

    def test_old_commit_and_incomplete_manifest_are_rejected(self):
        changed = json.loads(json.dumps(self.manifest))
        changed['commit'] = trial_common.COMMIT
        with self.assertRaises(ValueError):
            offline_common.verify_installed_source(self.root, changed)
        changed = json.loads(json.dumps(self.manifest))
        changed['files'].pop('README.md')
        with self.assertRaises(ValueError):
            offline_common.verify_installed_source(self.root, changed)

    def test_resigning_modified_file_cannot_change_the_fixed_Git_tree(self):
        path = self.root / 'README.md'
        path.write_bytes(b'changed source\n')
        changed = json.loads(json.dumps(self.manifest))
        item = changed['files']['README.md']
        item['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        item['git_blob'] = trial_common.git_hash('blob', path.read_bytes())
        with self.assertRaisesRegex(ValueError, '固定源码树'):
            offline_common.verify_installed_source(self.root, changed)

    def test_extra_importable_source_is_rejected(self):
        (self.root / 'blackjack_lab/extra.py').write_bytes(b'# unexpected code\n')
        with self.assertRaisesRegex(ValueError, '额外代码'):
            offline_common.verify_installed_source(self.root, self.manifest)


if __name__ == '__main__':
    unittest.main()
