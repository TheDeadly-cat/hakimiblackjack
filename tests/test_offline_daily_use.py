"""Focused checks for same-version updates and one-pass source verification."""
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

KIT = Path(__file__).resolve().parents[1] / 'packaging'
sys.path.insert(0, str(KIT / 'windows_trial'))
sys.path.insert(0, str(KIT / 'windows_offline_trial_o3'))
import offline_o3_common as common
import offline_o3_setup as setup
from trial_common import git_hash, instance_lock, tree_hash


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


class DailyUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.local = Path(self.temp.name)
        self.root = self.local / 'HakimiBJTrial' / setup.PACKAGE_ID
        self.data = self.local / 'HakimiBJTrialData' / setup.PACKAGE_ID
        self.logs = self.local / 'HakimiBJTrialInstallLogs' / 'test-update-one'
        self.root.mkdir(parents=True)
        self.data.mkdir(parents=True)
        self.logs.mkdir(parents=True)
        write_json(self.root / 'install.json', dict(
            package_id=setup.PACKAGE_ID, commit=setup.COMMIT,
            install_root=str(self.root), data_root=str(self.data)))
        write_json(self.root / 'INSTALL_SUCCESS.json', dict(
            package_id=setup.PACKAGE_ID, commit=setup.COMMIT, preflight_passed=True))
        (self.root / 'program.bin').write_bytes(b'previous-program')
        (self.data / 'session.db').write_bytes(b'original-records')
        (self.data / 'session.db.table-modes.json').write_bytes(b'original-settings')

    def assert_data_preserved(self):
        self.assertEqual((self.data / 'session.db').read_bytes(), b'original-records')
        self.assertEqual((self.data / 'session.db.table-modes.json').read_bytes(),
                         b'original-settings')

    def test_same_version_update_retains_previous_program_and_data(self):
        self.assertTrue(setup.existing_install(self.root, self.data))
        with setup.program_replacement(self.root, self.data, self.logs, True):
            self.assertFalse(self.root.exists())
            self.root.mkdir()
            (self.root / 'program.bin').write_bytes(b'updated-program')
        receipt = json.loads((self.logs / 'UPDATE_COMPLETED.json').read_text())
        previous = Path(receipt['previous_program'])
        self.assertEqual((previous / 'program.bin').read_bytes(), b'previous-program')
        self.assertEqual((self.root / 'program.bin').read_bytes(), b'updated-program')
        self.assert_data_preserved()

    def test_failed_update_restores_program_and_keeps_failed_copy(self):
        with self.assertRaisesRegex(RuntimeError, 'synthetic install failure'):
            with setup.program_replacement(self.root, self.data, self.logs, True):
                self.root.mkdir()
                (self.root / 'partial.bin').write_bytes(b'failed-build')
                raise RuntimeError('synthetic install failure')
        self.assertEqual((self.root / 'program.bin').read_bytes(), b'previous-program')
        receipt = json.loads((self.logs / 'UPDATE_ROLLED_BACK.json').read_text())
        self.assertEqual((Path(receipt['failed_program']) / 'partial.bin').read_bytes(),
                         b'failed-build')
        self.assert_data_preserved()

    def test_running_instance_blocks_update_before_program_move(self):
        with instance_lock(self.data):
            with self.assertRaisesRegex(RuntimeError, '仍在运行'):
                with setup.program_replacement(self.root, self.data, self.logs, True):
                    self.fail('running app must prevent replacement')
        self.assertEqual((self.root / 'program.bin').read_bytes(), b'previous-program')
        self.assert_data_preserved()

    def test_mismatched_data_binding_is_not_adopted(self):
        info = json.loads((self.root / 'install.json').read_text())
        info['data_root'] = str(self.local / 'unrelated')
        write_json(self.root / 'install.json', info)
        with self.assertRaises(ValueError):
            setup.existing_install(self.root, self.data)
        self.assertEqual((self.root / 'program.bin').read_bytes(), b'previous-program')
        self.assert_data_preserved()

    def test_equivalent_data_path_is_recognized(self):
        alias = self.data / '..' / self.data.name
        self.assertTrue(setup.existing_install(self.root, alias))
        self.assert_data_preserved()

    def test_known_previous_source_can_update_but_cannot_run_as_current(self):
        info = json.loads((self.root / 'install.json').read_text())
        info['commit'] = setup.UPGRADE_FROM_COMMITS[0]
        write_json(self.root / 'install.json', info)
        write_json(self.root / 'INSTALL_SUCCESS.json', dict(
            package_id=setup.PACKAGE_ID, commit=info['commit'], preflight_passed=True))
        self.assertTrue(setup.existing_install(self.root, self.data))
        with self.assertRaises(ValueError):
            common.installation_data(self.root, info)
        self.assert_data_preserved()

    def test_unknown_source_or_mismatched_success_cannot_update(self):
        info = json.loads((self.root / 'install.json').read_text())
        for commit in ('0' * 40, setup.UPGRADE_FROM_COMMITS[0]):
            with self.subTest(commit=commit):
                write_json(self.root / 'install.json', dict(info, commit=commit))
                with self.assertRaises(ValueError):
                    setup.existing_install(self.root, self.data)
                self.assert_data_preserved()


class OnePassVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.internal = self.root / '_internal'
        self.app = self.internal / 'app'
        files = {'blackjack_lab/a.py': b'print("fixed")\n', 'README.txt': b'fixed\n'}
        entries = {}
        metadata = {}
        for relative, value in files.items():
            path = self.app / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value)
            blob = git_hash('blob', value)
            entries[relative] = ('100644', blob)
            metadata[relative] = dict(git_mode='100644', git_blob=blob,
                                     sha256=hashlib.sha256(value).hexdigest(), size=len(value))
        self.tree = tree_hash(entries)
        self.manifest = dict(commit=common.COMMIT, git_tree=self.tree, files=metadata)
        write_json(self.internal / 'source-manifest.json', self.manifest)
        self.bundle = dict(package_id=common.PACKAGE_ID, commit=common.COMMIT,
                           source_tree=self.tree, files={})
        for path in self.internal.rglob('*'):
            if path.is_file():
                self.bundle['files'][path.relative_to(self.root).as_posix()] = hashlib.sha256(
                    path.read_bytes()).hexdigest()
        write_json(self.root / 'BUNDLE_MANIFEST.json', self.bundle)
        self.addCleanup(patch.stopall)
        patch.object(common, 'TREE', self.tree).start()
        patch.object(common, 'SOURCE_FILE_COUNT', len(files)).start()
        patch.object(sys, '_MEIPASS', str(self.internal), create=True).start()

    def test_every_source_file_is_read_once_with_both_checks(self):
        counts = Counter()
        original = Path.read_bytes
        def read(path):
            counts[path] += 1
            return original(path)
        with patch.object(Path, 'read_bytes', read):
            common.verify_bundle(self.root)
        for relative in self.manifest['files']:
            self.assertEqual(counts[self.app / relative], 1)

    def test_modified_source_is_rejected_even_when_timestamp_is_restored(self):
        path = self.app / 'README.txt'
        original = path.stat()
        path.write_bytes(b'other\n')
        os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
        with self.assertRaisesRegex(ValueError, '冻结产物发生变化'):
            common.verify_bundle(self.root)

    def test_git_blob_validation_is_retained(self):
        self.manifest['files']['README.txt']['git_blob'] = '0' * 40
        source = self.internal / 'source-manifest.json'
        write_json(source, self.manifest)
        self.bundle['files']['_internal/source-manifest.json'] = hashlib.sha256(
            source.read_bytes()).hexdigest()
        write_json(self.root / 'BUNDLE_MANIFEST.json', self.bundle)
        with self.assertRaisesRegex(ValueError, '源码 Git 摘要不符'):
            common.verify_bundle(self.root)


if __name__ == '__main__':
    unittest.main()
