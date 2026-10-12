"""Independent installation path contracts, using temporary synthetic files."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

KIT = Path(__file__).resolve().parents[1] / 'packaging'
sys.path.insert(0, str(KIT / 'windows_trial'))
sys.path.insert(0, str(KIT / 'windows_offline_trial_o3'))
import offline_o3_common as common


class InstallLayoutTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.local = self.base / 'local'

    def info(self, layout, **changes):
        return dict(package_id=common.PACKAGE_ID, commit=common.COMMIT,
                    install_root=str(layout['root']), data_root=str(layout['data']),
                    **layout['info_fields'], **changes)

    def test_legacy_default_paths_and_existing_metadata_remain_compatible(self):
        first = common.create_install_layout(local=self.local)
        selected = common.create_install_layout(first['root'], local=self.local)
        self.assertEqual(first, selected)
        self.assertEqual(first['layout'], 'legacy-default')
        self.assertIsNone(first['instance_id'])
        self.assertEqual(first['info_fields'], {})
        self.assertEqual(first['root'], self.local / 'HakimiBJTrial' / common.PACKAGE_ID)
        self.assertEqual(first['data'], self.local / 'HakimiBJTrialData' / common.PACKAGE_ID)
        self.assertEqual(first['logs_root'], self.local / 'HakimiBJTrialInstallLogs')
        self.assertEqual(first['rollbacks_root'], self.local / 'HakimiBJTrialRollbacks')
        self.assertEqual(first['backups_root'], self.local / 'HakimiBJTrialBackups')
        info = self.info(first)
        self.assertEqual(common.installation_data(first['root'], info), first['data'])
        self.assertEqual(common.installation_layout(first['root'], info)['base'], self.local)
        old = dict(info, commit='a'*40)
        self.assertEqual(common.installation_data(first['root'], old, expected_commit='a'*40), first['data'])
        with self.assertRaises(ValueError):
            common.installation_data(first['root'], old)
        self.assertFalse(self.local.exists())

    def test_two_Chinese_containers_have_independent_paths_and_new_instance_ids(self):
        layouts = [common.create_install_layout(self.base / name, local=self.local)
                   for name in ('桌面试用一', '桌面试用二')]
        self.assertNotEqual(layouts[0]['instance_id'], layouts[1]['instance_id'])
        for layout in layouts:
            self.assertEqual(layout['layout'], 'custom-folder-v1')
            self.assertRegex(layout['instance_id'], r'^[0-9a-f]{32}$')
            for key, child in (('root', 'app'), ('data', 'data'), ('logs_root', 'install-logs'),
                               ('rollbacks_root', 'rollbacks'), ('backups_root', 'backups')):
                self.assertEqual(layout[key], layout['base'] / child)
                self.assertTrue(layout[key].is_absolute())
                self.assertFalse(layout[key].exists())
            resolved = common.installation_layout(layout['root'], self.info(layout))
            self.assertEqual(resolved, {k: v for k, v in layout.items() if k != 'info_fields'})
        for key in ('root', 'data', 'logs_root', 'rollbacks_root', 'backups_root'):
            self.assertNotEqual(layouts[0][key], layouts[1][key])
        self.assertFalse(any(self.base.iterdir()))

    def test_existing_custom_identity_can_be_reused_without_changing_original_bytes(self):
        first = common.create_install_layout(self.base / 'chosen', local=self.local)
        first['root'].mkdir(parents=True)
        info = self.info(first)
        path = first['root'] / 'install.json'
        raw = (json.dumps(info, ensure_ascii=False, indent=3)+'\n').encode('utf-8')
        path.write_bytes(raw)
        loaded = json.loads(path.read_bytes())
        read = common.installation_layout(first['root'], loaded)
        repeated = common.create_install_layout(read['base'], local=self.local,
                                                instance_id=read['instance_id'])
        self.assertEqual(repeated, first)
        self.assertEqual(common.installation_data(first['root'], loaded), first['data'])
        self.assertEqual(path.read_bytes(), raw)
        moved = self.base / 'moved' / 'app'
        with self.assertRaises(ValueError):
            common.installation_layout(moved, loaded)

    def test_unknown_relative_mismatched_and_overlapping_bindings_are_rejected(self):
        layout = common.create_install_layout(self.base / 'chosen', local=self.local)
        original = self.info(layout)
        bad = [dict(original, installation_layout='custom-folder-v2'),
               dict(original, installation_layout=None), dict(original, instance_id='F'*32),
               dict(original, instance_id='f'*31), dict(original, instance_id=True),
               dict(original, container_root='relative'), dict(original, install_root='app'),
               dict(original, data_root='data'), dict(original, data_root=str(layout['root'])),
               dict(original, data_root=str(layout['root'] / 'data')),
               dict(original, data_root=str(self.base / 'other' / 'data')),
               dict(original, package_id='different'), dict(original, commit='0'*40)]
        for info in bad:
            with self.subTest(info=info):
                with self.assertRaises(ValueError):
                    common.installation_layout(layout['root'], info)
        with self.assertRaises(ValueError):
            common.installation_layout(Path('app'), original)
        for destination in ('relative', Path('relative')):
            with self.assertRaises(ValueError):
                common.create_install_layout(destination, local=self.local)
        for instance_id in ('', 'a'*31, 'a'*33, 'A'*32, None, False):
            if instance_id is None:
                continue  # Omission is the documented fresh UUID request.
            with self.assertRaises(ValueError):
                common.create_install_layout(layout['base'], local=self.local, instance_id=instance_id)
        self.assertFalse(any(self.base.iterdir()))

    def test_directory_links_and_file_destinations_are_rejected_before_resolving(self):
        target = self.base / 'actual'
        target.mkdir()
        alias = self.base / 'alias'
        # No privileged OS command: a directory symlink is enough to exercise
        # the shared ancestor-link guard on hosts where creating one is allowed.
        try:
            alias.symlink_to(target, target_is_directory=True)
        except OSError as error:
            self.skipTest(f'this test host cannot create a temporary symlink: {error}')
        with self.assertRaises(ValueError):
            common.create_install_layout(alias / 'chosen', local=self.local)
        layout = common.create_install_layout(target, local=self.local)
        info = self.info(layout)
        (target / 'data').symlink_to(self.base, target_is_directory=True)
        with self.assertRaises(ValueError):
            common.installation_layout(layout['root'], info)
        with self.assertRaises(ValueError):
            common.create_install_layout(target, local=self.local)
        file_path = self.base / 'program-file'
        file_path.write_bytes(b'original')
        with self.assertRaises(ValueError):
            common.create_install_layout(file_path, local=self.local)
        self.assertEqual(file_path.read_bytes(), b'original')

    def test_nonempty_unrecognized_container_is_only_planned_and_never_adopted(self):
        container = self.base / 'unrecognized'
        container.mkdir()
        original = container / 'unrelated.txt'
        original.write_bytes(b'owned original')
        plan = common.create_install_layout(container, local=self.local)
        self.assertEqual(set(container.iterdir()), {original})
        for info in ({}, dict(package_id=common.PACKAGE_ID, commit=common.COMMIT,
                              install_root=str(plan['root']), data_root=str(plan['data']))):
            with self.assertRaises(ValueError):
                common.installation_layout(plan['root'], info)
        self.assertEqual(original.read_bytes(), b'owned original')
        self.assertFalse((container / 'app').exists())


if __name__ == '__main__':
    unittest.main()
