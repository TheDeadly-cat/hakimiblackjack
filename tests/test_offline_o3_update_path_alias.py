"""Equivalent update path spelling keeps the original bound installation."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'packaging/windows_trial'))
sys.path.insert(0, str(REPO / 'packaging/windows_offline_trial_o3'))
import offline_o3_setup as setup


class UpdatePathAliasTests(unittest.TestCase):
    def test_parent_aliases_update_the_same_instance_and_preserve_original_program_and_data(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder).resolve()
            root = base / 'HakimiBJTrial' / setup.PACKAGE_ID
            data = base / 'HakimiBJTrialData' / setup.PACKAGE_ID
            logs = base / 'HakimiBJTrialInstallLogs' / 'alias-update-one'
            for path in (root, data, logs):
                path.mkdir(parents=True)
            info = dict(package_id=setup.PACKAGE_ID, commit=setup.COMMIT,
                        install_root=str(root), data_root=str(data))
            (root / 'install.json').write_text(json.dumps(info), encoding='utf-8')
            (root / 'INSTALL_SUCCESS.json').write_text(json.dumps(dict(
                package_id=setup.PACKAGE_ID, commit=setup.COMMIT, preflight_passed=True)), encoding='utf-8')
            (root / 'program.bin').write_bytes(b'original-program')
            (data / 'session.db').write_bytes(b'original-data')
            aliases = tuple(path / '..' / path.name for path in (root, data, logs))
            self.assertTrue(all('..' in path.parts for path in aliases))
            with setup.program_replacement(*aliases, True):
                self.assertFalse(root.exists())
                root.mkdir()
                (root / 'program.bin').write_bytes(b'updated-program')
            receipt = json.loads((logs / 'UPDATE_COMPLETED.json').read_text(encoding='utf-8'))
            previous = Path(receipt['previous_program'])
            self.assertEqual(previous.parent, base / 'HakimiBJTrialRollbacks')
            self.assertEqual((previous / 'program.bin').read_bytes(), b'original-program')
            self.assertEqual((root / 'program.bin').read_bytes(), b'updated-program')
            self.assertEqual((data / 'session.db').read_bytes(), b'original-data')
            self.assertEqual(receipt['program_root'], str(root))
            self.assertEqual(receipt['data_root'], str(data))


if __name__ == '__main__':
    unittest.main()
