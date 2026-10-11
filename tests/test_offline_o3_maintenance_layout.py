"""Synthetic maintenance boundaries for independent Windows install containers."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

REPO = Path(__file__).resolve().parents[1]
WRAPPER = REPO / 'packaging/windows_offline_trial_o3'
sys.path.insert(0, str(REPO / 'packaging/windows_trial'))
sys.path.insert(0, str(WRAPPER))
import offline_o3_common as common
import offline_o3_tools as tools
from trial_common import sha256_file

spec = importlib.util.spec_from_file_location('o3_maintenance_build_runtime', WRAPPER / 'build_runtime.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class OfflineO3MaintenanceLayoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()

    def install(self, name, *, legacy=False, identity='a' * 32):
        layout = common.create_install_layout(None if legacy else self.base / name,
                                             local=self.base / name, instance_id=identity)
        for key in ('root', 'data', 'logs_root', 'rollbacks_root', 'backups_root'):
            layout[key].mkdir(parents=True)
        info = dict(package_id=common.PACKAGE_ID, commit=common.COMMIT,
                    install_root=str(layout['root']), data_root=str(layout['data']),
                    **layout['info_fields'])
        (layout['root'] / 'install.json').write_text(json.dumps(info), encoding='utf-8')
        (layout['root'] / tools.EXECUTABLE).write_bytes(b'synthetic-not-a-runnable-executable')
        (layout['data'] / 'history.txt').write_text('原资料', encoding='utf-8')
        sidecar = layout['data'] / 'session.db.analysis'
        sidecar.mkdir()
        (sidecar / 'original.json').write_bytes(b'{"preserve":true}')
        (layout['base'] / 'INSTALL_INSTANCE.json').write_text(json.dumps(info), encoding='utf-8')
        return layout

    def run_maintenance(self, root, action, *, receipt=None):
        with patch.object(tools, 'program_root', return_value=root), \
             patch.object(tools, 'verify_bundle'), \
             patch.object(tools, '_prepare_origin', return_value=dict(prepare_pid=123, prepare_creation_ticks=456)), \
             patch.object(tools, '_dispatch_uninstall', return_value=SimpleNamespace(pid=789)) as dispatch, \
             patch.object(tools, '_wait_for_prepare_exit'):
            code = tools.maintain(action, yes=True, receipt_path=receipt)
        return code, dispatch

    def prepare(self, layout):
        code, dispatch = self.run_maintenance(layout['root'], 'prepare-uninstall')
        self.assertEqual(code, 0)
        dispatch.assert_called_once()
        runner = dispatch.call_args.args[0]
        return runner

    def test_custom_verify_and_backup_stay_in_their_instance_roots(self):
        first = self.install('甲 中文 空格')
        second = self.install('乙 中文 空格', identity='b' * 32)
        second_before = tools.snapshot_files(second['base'])
        original = tools.snapshot_files(first['data'])
        self.assertEqual(self.run_maintenance(first['root'], 'verify')[0], 0)
        self.assertEqual(self.run_maintenance(first['root'], 'backup')[0], 0)
        receipts = [json.loads(p.read_text(encoding='utf-8')) for p in first['logs_root'].glob('*.json')]
        backup = next(r for r in receipts if r['action'] == 'backup')
        destination = Path(backup['backup_directory'])
        self.assertEqual(destination.parent, first['backups_root'])
        self.assertEqual(tools.snapshot_files(destination / 'data'), original)
        self.assertFalse(backup['application_restore_tested'])
        self.assertEqual(tools.snapshot_files(first['data']), original)
        self.assertEqual(tools.snapshot_files(second['base']), second_before)

    def test_prepare_binds_all_instance_fields_and_dispatches_once_with_yes(self):
        layout = self.install('单次 派发')
        runner = self.prepare(layout)
        reference = json.loads((runner / 'UNINSTALL_TARGET.json').read_text(encoding='utf-8'))
        self.assertEqual(reference['target'], str(layout['root']))
        self.assertEqual(reference['data_root'], str(layout['data']))
        self.assertEqual(reference['container_root'], str(layout['base']))
        self.assertEqual(reference['layout'], common.CUSTOM_LAYOUT)
        self.assertEqual(reference['instance_id'], layout['instance_id'])
        self.assertEqual(reference['install_info_sha256'], sha256_file(layout['root'] / 'install.json'))
        self.assertEqual(runner.parent, layout['logs_root'])
        self.assertEqual(tools._uninstall_layout(runner, reference)['data'], layout['data'])
        with patch.object(tools.subprocess, 'Popen', return_value=SimpleNamespace(pid=1)) as popen:
            tools._dispatch_uninstall(runner, layout['logs_root'], yes=True)
        command = popen.call_args.args[0]
        self.assertEqual(command.count('uninstall'), 1)
        self.assertIn('--yes', command)
        self.assertEqual(popen.call_args.kwargs['cwd'], layout['logs_root'])
        self.assertNotIn('prepare-uninstall', command)
        with patch.object(tools.subprocess, 'Popen') as popen:
            tools._dispatch_uninstall(runner, layout['logs_root'], yes=False)
        self.assertNotIn('--yes', popen.call_args.args[0])

    def test_custom_uninstall_removes_only_app_and_preserves_data_container_and_other_instance(self):
        first = self.install('卸载 甲')
        second = self.install('卸载 乙', identity='b' * 32)
        first_data = tools.snapshot_files(first['data'])
        second_before = tools.snapshot_files(second['base'])
        runner = self.prepare(first)
        self.assertEqual(self.run_maintenance(runner, 'uninstall')[0], 0)
        self.assertFalse(first['root'].exists())
        self.assertEqual(tools.snapshot_files(first['data']), first_data)
        self.assertTrue((first['base'] / 'INSTALL_INSTANCE.json').is_file())
        self.assertTrue(runner.is_dir())
        self.assertEqual(tools.snapshot_files(second['base']), second_before)

    def test_changed_reference_or_changed_target_info_refuses_recursive_deletion(self):
        first = self.install('保护 甲')
        second = self.install('保护 乙', identity='b' * 32)
        runner = self.prepare(first)
        path = runner / 'UNINSTALL_TARGET.json'
        original = json.loads(path.read_text(encoding='utf-8'))
        variants = [dict(original, instance_id='c' * 32),
                    dict(original, data_root=str(second['data'])),
                    dict(original, container_root=str(second['base'])),
                    dict(original, target=str(second['root']), installation_base=str(second['base']),
                         container_root=str(second['base']), data_root=str(second['data']),
                         instance_id=second['instance_id'],
                         install_info_sha256=sha256_file(second['root'] / 'install.json')),
                    dict(original, target='../app')]
        variants.append({key: value for key, value in original.items()
                         if key not in ('prepare_pid', 'prepare_creation_ticks')})
        for reference in variants:
            with self.subTest(reference=reference):
                path.write_text(json.dumps(reference), encoding='utf-8')
                with patch.object(tools.shutil, 'rmtree') as remove:
                    self.assertEqual(self.run_maintenance(runner, 'uninstall')[0], 1)
                remove.assert_not_called()
        path.write_text(json.dumps(original), encoding='utf-8')
        info_path = first['root'] / 'install.json'
        info = json.loads(info_path.read_text(encoding='utf-8'))
        info['instance_id'] = 'd' * 32
        info_path.write_text(json.dumps(info), encoding='utf-8')
        with patch.object(tools.shutil, 'rmtree') as remove:
            self.assertEqual(self.run_maintenance(runner, 'uninstall')[0], 1)
        remove.assert_not_called()
        self.assertTrue(first['root'].is_dir())
        self.assertTrue(second['root'].is_dir())

    def test_moved_target_and_receipt_inside_data_are_rejected(self):
        layout = self.install('不可 搬移')
        runner = self.prepare(layout)
        moved = layout['base'] / 'moved-app'
        layout['root'].rename(moved)
        self.assertEqual(self.run_maintenance(runner, 'uninstall')[0], 1)
        moved.rename(layout['root'])
        before = tools.snapshot_files(layout['data'])
        self.assertEqual(self.run_maintenance(runner, 'uninstall',
                                             receipt=layout['data'] / 'result.json')[0], 1)
        self.assertTrue(layout['root'].is_dir())
        # The rejected receipt itself must not be written into retained data.
        self.assertEqual(tools.snapshot_files(layout['data']), before)

    def test_legacy_three_field_runner_reference_remains_usable(self):
        layout = self.install('旧 默认', legacy=True)
        runner = layout['logs_root'] / ('offline-uninstall-runner-' + 'e' * 32)
        tools.shutil.copytree(layout['root'], runner)
        reference = dict(package_id=common.PACKAGE_ID, target=str(layout['root']),
                         installation_base=str(layout['base']))
        (runner / 'UNINSTALL_TARGET.json').write_text(json.dumps(reference), encoding='utf-8')
        before = tools.snapshot_files(layout['data'])
        self.assertEqual(self.run_maintenance(runner, 'uninstall')[0], 0)
        self.assertFalse(layout['root'].exists())
        self.assertEqual(tools.snapshot_files(layout['data']), before)

    def test_uninstall_cmd_has_no_hardcoded_namespace_and_prepares_once(self):
        body = builder.runtime_batch_files()['UNINSTALL_TRIAL.cmd']
        self.assertEqual(body.count('--maintenance prepare-uninstall'), 1)
        self.assertIn('prepare-uninstall %*', body)
        self.assertIn('cd /d "%TEMP%"', body)
        self.assertNotIn('HakimiBJTrialInstallLogs', body)
        self.assertNotIn('-uninstall.cmd', body)

    def test_identity_wait_timeout_preserves_app_and_reused_pid_is_not_waited(self):
        reference = dict(prepare_pid=123, prepare_creation_ticks=456)
        kernel = Mock()
        kernel.OpenProcess.return_value = 42
        kernel.WaitForSingleObject.return_value = 258
        with patch.object(tools, '_process_api', return_value=kernel), \
             patch.object(tools, '_process_creation', return_value=456), \
             patch.object(tools.os, 'getpid', return_value=987):
            with self.assertRaisesRegex(RuntimeError, '保留程序'):
                tools._wait_for_prepare_exit(reference)
        kernel.WaitForSingleObject.assert_called_once_with(42, 15000)
        kernel.CloseHandle.assert_called_once_with(42)
        kernel.reset_mock()
        with patch.object(tools, '_process_api', return_value=kernel), \
             patch.object(tools, '_process_creation', return_value=999), \
             patch.object(tools.os, 'getpid', return_value=987):
            tools._wait_for_prepare_exit(reference)
        kernel.WaitForSingleObject.assert_not_called()
        layout = self.install('原进程 未退出')
        runner = self.prepare(layout)
        with patch.object(tools, 'program_root', return_value=runner), \
             patch.object(tools, 'verify_bundle'), \
             patch.object(tools, '_wait_for_prepare_exit', side_effect=RuntimeError('synthetic timeout')):
            self.assertEqual(tools.maintain('uninstall', yes=True), 1)
        self.assertTrue(layout['root'].is_dir())


if __name__ == '__main__':
    unittest.main()
