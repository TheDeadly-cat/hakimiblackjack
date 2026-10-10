"""Offline identity and backup boundaries; frozen EXE checks remain separate."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'packaging/windows_trial'))
sys.path.insert(0, str(REPO / 'packaging/windows_offline_trial'))
from offline_common import COMMIT, PACKAGE_ID, installation_data
from offline_tools import backup, snapshot_files
from build_runtime import runtime_source_data
from trial_common import instance_lock


class OfflinePackagingHelpersTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def test_chinese_namespace_binding_does_not_need_installers_environment(self):
        base = self.base / '中文 空格'
        root = base / 'HakimiBJTrial' / PACKAGE_ID
        data = base / 'HakimiBJTrialData' / PACKAGE_ID
        info = dict(package_id=PACKAGE_ID, commit=COMMIT,
            install_root=str(root), data_root=str(data))
        with patch.dict('os.environ', {'LOCALAPPDATA': str(self.base / 'another-shell')}):
            self.assertEqual(installation_data(root, info), data.resolve())

    def test_snapshot_sources_are_available_at_frozen_module_paths(self):
        from blackjack_lab.storage.opening_snapshots import SOURCES as opening
        from blackjack_lab.storage.sidebet_snapshots import SOURCES as sidebet
        manifest = dict(files={name: {} for name in (*opening, *sidebet, 'README.md')})
        data = runtime_source_data(REPO, manifest)
        installed = {Path(destination, Path(source).name).as_posix() for source, destination in data}
        self.assertEqual(installed, set(opening) | set(sidebet))
        self.assertNotIn('README.md', installed)

    def test_moved_root_or_foreign_data_is_rejected(self):
        root = self.base / 'HakimiBJTrial' / PACKAGE_ID
        data = self.base / 'HakimiBJTrialData' / PACKAGE_ID
        info = dict(package_id=PACKAGE_ID, commit=COMMIT, install_root=str(root), data_root=str(data))
        with self.assertRaises(ValueError):
            installation_data(self.base / 'elsewhere' / 'HakimiBJTrial' / PACKAGE_ID, info)
        with self.assertRaises(ValueError):
            installation_data(root, dict(info, data_root=str(root / 'data')))

    def data(self):
        data = self.base / 'data'
        data.mkdir()
        connection = sqlite3.connect(data / 'session.db')
        connection.execute('CREATE TABLE test (value TEXT)')
        connection.execute('INSERT INTO test VALUES (?)', ('原资料',))
        connection.commit()
        connection.close()
        sidecar = data / 'session.db.analysis'
        sidecar.mkdir()
        (sidecar / 'value.json').write_text('{"original":true}', encoding='utf-8')
        return data

    def test_whole_backup_includes_sidecars_and_keeps_restore_unproven(self):
        data = self.data()
        before = snapshot_files(data)
        target = self.base / 'backup'
        receipt = backup(data, target)
        self.assertEqual(snapshot_files(data), before)
        self.assertEqual(snapshot_files(target / 'data'), before)
        self.assertEqual(receipt['package_id'], PACKAGE_ID)
        self.assertFalse(receipt['application_restore_tested'])

    def test_busy_instance_and_existing_destination_refuse_backup(self):
        data = self.data()
        with instance_lock(data):
            with self.assertRaises(RuntimeError):
                backup(data, self.base / 'busy-backup')
        destination = self.base / 'existing'
        destination.mkdir()
        with self.assertRaises(ValueError):
            backup(data, destination)

    def test_mutation_during_copy_marks_partial_backup_without_rewriting_original(self):
        data = self.data()
        import offline_tools
        original = offline_tools.shutil.copy2
        def changed(source, target):
            result = original(source, target)
            if Path(source).name == 'value.json':
                Path(source).write_text('{"changed":true}', encoding='utf-8')
            return result
        destination = self.base / 'partial'
        with patch.object(offline_tools.shutil, 'copy2', side_effect=changed):
            with self.assertRaises(ValueError):
                backup(data, destination)
        self.assertTrue((destination / 'BACKUP_INCOMPLETE.json').is_file())
        self.assertFalse((destination / 'BACKUP_MANIFEST.json').exists())


if __name__ == '__main__':
    unittest.main()
