"""Offline tests of the wrapper only; not Windows installation acceptance."""
from __future__ import annotations
import ast
from contextlib import closing
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))
import trial_common as common
from trial_tools import backup


class InstallerHelpers(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def make_archive(self, files=None):
        files = files or {'README.txt': (b'trial\n', '100644'),
                          'folder/main.py': (b'print(1)\n', '100644')}
        archive = self.root / ('fixture-' + str(len(list(self.root.glob('*.zip')))) + '.zip')
        with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
            for path, (data, mode) in files.items():
                item = zipfile.ZipInfo('repo-fixed/' + path)
                item.external_attr = (stat.S_IFREG | (0o755 if mode == '100755' else 0o644)) << 16
                zf.writestr(item, data)
        tree = common.tree_hash({p: (mode, common.git_hash('blob', data)) for p, (data, mode) in files.items()})
        return archive, tree

    def test_git_blob_compatible_with_git(self):
        expected = subprocess.check_output(['git', 'hash-object', '--stdin'], input=b'trial\r\n').decode().strip()
        self.assertEqual(common.git_hash('blob', b'trial\r\n'), expected)

    def test_tree_and_git_archive_match_real_git(self):
        repo = self.root / 'repo'; repo.mkdir()
        def git(*args, **kwargs):
            return subprocess.check_output(['git', '-C', str(repo), *args], **kwargs).strip()
        git('init', '-q')
        git('config', 'user.name', 'Synthetic Test')
        git('config', 'user.email', 'synthetic@example.invalid')
        git('config', 'core.autocrlf', 'false')
        (repo / 'a').mkdir()
        (repo / 'a/file.py').write_bytes(b'print(1)\r\n')
        (repo / 'a.c').write_bytes(b'hello\n')
        (repo / '中文.txt').write_bytes('试用版\n'.encode())
        run = repo / 'run.sh'; run.write_bytes(b'#!/bin/sh\ntrue\n'); run.chmod(0o755)
        git('add', '.')
        # Windows chmod cannot set Git's executable bit. Set the fixture's
        # index mode explicitly while retaining the 100755 assertion below.
        git('update-index', '--chmod=+x', 'run.sh')
        git('commit', '-q', '-m', 'synthetic fixture')
        expected = git('rev-parse', 'HEAD^{tree}').decode()
        archive = self.root / 'git-archive.zip'
        git('archive', '--format=zip', '--prefix=fixture/', '--output=' + str(archive), 'HEAD')
        manifest = common.inspect_archive(archive, expected)
        self.assertEqual(manifest['git_tree'], expected)
        self.assertEqual(manifest['files']['run.sh']['git_mode'], '100755')

    def test_archive_extracts_only_after_validation(self):
        archive, expected = self.make_archive()
        manifest = common.inspect_archive(archive, expected)
        out = self.root / 'output'
        common.extract_verified(archive, out, manifest)
        self.assertEqual((out / 'folder/main.py').read_bytes(), b'print(1)\n')
        self.assertEqual(common.sha256_file(out / 'README.txt'), manifest['files']['README.txt']['sha256'])

    def test_wrong_tree_rejected(self):
        archive, _ = self.make_archive()
        with self.assertRaisesRegex(ValueError, '源码树不匹配'):
            common.inspect_archive(archive, '0' * 40)

    def test_source_changed_between_verify_and_extract_rejected(self):
        archive, expected = self.make_archive()
        manifest = common.inspect_archive(archive, expected)
        with zipfile.ZipFile(archive, 'w') as zf:
            zf.writestr('repo-fixed/README.txt', b'changed')
            zf.writestr('repo-fixed/folder/main.py', b'print(1)\n')
        with self.assertRaisesRegex(ValueError, '发生变化'):
            common.extract_verified(archive, self.root / 'out', manifest)

    def test_zip_traversal_rejected(self):
        for unsafe in ('../escape.txt', '/absolute', 'a/../../escape', 'a\\b', 'a/C:foo', 'a/NUL.txt', 'a/end.'):
            with self.subTest(path=unsafe):
                archive = self.root / 'bad.zip'
                with zipfile.ZipFile(archive, 'w') as zf:
                    zf.writestr('repo/' + unsafe, 'x')
                with self.assertRaises(ValueError):
                    common.inspect_archive(archive, '0' * 40)
        self.assertFalse((self.root / 'escape.txt').exists())

    def test_case_collisions_rejected(self):
        for files in (
            {'A/a.py': ('100644', '0'*40), 'a/b.py': ('100644', '1'*40)},
            {'file.py': ('100644', '0'*40), 'FILE.py': ('100644', '1'*40)}):
            with self.assertRaises(ValueError):
                common.tree_hash(files)

    def test_symlink_archive_rejected(self):
        archive = self.root / 'link.zip'
        with zipfile.ZipFile(archive, 'w') as zf:
            item = zipfile.ZipInfo('repo/link'); item.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(item, '/outside')
        with self.assertRaisesRegex(ValueError, '特殊文件'):
            common.inspect_archive(archive, '0' * 40)

    def test_duplicate_zip_member_rejected(self):
        import warnings
        archive = self.root / 'dup.zip'
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            with zipfile.ZipFile(archive, 'w') as zf:
                zf.writestr('repo/a.txt', 'x'); zf.writestr('repo/a.txt', 'x')
        with self.assertRaisesRegex(ValueError, '重复'):
            common.inspect_archive(archive, '0' * 40)

    def test_multiple_root_directories_rejected(self):
        archive = self.root / 'roots.zip'
        with zipfile.ZipFile(archive, 'w') as zf:
            zf.writestr('one/a', 'x'); zf.writestr('two/b', 'x')
        with self.assertRaisesRegex(ValueError, '唯一'):
            common.inspect_archive(archive, '0' * 40)

    def test_expanded_size_limit_rejected(self):
        archive, tree = self.make_archive({'large.txt': (b'x'*100, '100644')})
        with patch.object(common, 'MAX_EXPANDED_BYTES', 20), self.assertRaisesRegex(ValueError, '上限'):
            common.inspect_archive(archive, tree)

    def test_existing_destination_never_overwritten(self):
        archive, tree = self.make_archive()
        manifest = common.inspect_archive(archive, tree)
        out = self.root / 'out'; out.mkdir(); (out / 'keep').write_text('old')
        with self.assertRaises(FileExistsError):
            common.extract_verified(archive, out, manifest)
        self.assertEqual((out / 'keep').read_text(), 'old')

    def test_installed_source_manifest_and_tamper_guard(self):
        archive, tree = self.make_archive({'blackjack_lab/main.py': (b'x=1\n', '100644')})
        manifest = common.inspect_archive(archive, tree)
        out = self.root / 'out'; common.extract_verified(archive, out, manifest)
        with patch.object(common, 'TREE', tree):
            common.verify_installed_source(out, manifest)
            (out / 'blackjack_lab/main.py').write_text('x=2\n')
            with self.assertRaises(ValueError):
                common.verify_installed_source(out, manifest)

    def test_unexpected_python_code_rejected(self):
        archive, tree = self.make_archive({'blackjack_lab/main.py': (b'x=1\n', '100644')})
        manifest = common.inspect_archive(archive, tree)
        out = self.root / 'out'; common.extract_verified(archive, out, manifest)
        (out / 'blackjack_lab/extra.py').write_text('x=1')
        with patch.object(common, 'TREE', tree), self.assertRaisesRegex(ValueError, '额外代码'):
            common.verify_installed_source(out, manifest)

    def test_json_receipt_never_overwrites_existing(self):
        path = self.root / 'receipt.json'
        common.write_json_new(path, {'status': 'original'})
        with self.assertRaises(FileExistsError):
            common.write_json_new(path, {'status': 'changed'})
        self.assertEqual(json.loads(path.read_text())['status'], 'original')

    def test_linked_directory_rejected(self):
        (self.root / 'real').mkdir()
        (self.root / 'link').symlink_to(self.root / 'real', target_is_directory=True)
        with self.assertRaises(ValueError):
            common.assert_no_links(self.root / 'link/subfolder')

    def test_instance_lock_blocks_concurrent_process_and_releases(self):
        data = self.root / 'data'
        code = "from pathlib import Path; from trial_common import instance_lock; import sys\nwith instance_lock(Path(sys.argv[1])): print('LOCKED')"
        with common.instance_lock(data):
            failed = subprocess.run([sys.executable, '-c', code, str(data)], cwd=KIT, capture_output=True)
            self.assertNotEqual(failed.returncode, 0)
        passed = subprocess.run([sys.executable, '-c', code, str(data)], cwd=KIT, capture_output=True)
        self.assertEqual(passed.returncode, 0, passed.stderr)

    def make_data(self):
        data = self.root / 'data'; data.mkdir()
        with closing(sqlite3.connect(data / 'session.db')) as db, db:
            db.execute('CREATE TABLE synthetic (v INTEGER)'); db.execute('INSERT INTO synthetic VALUES (1)')
        for relative in ('session.db.analysis/one.json', 'session.db.table-modes.json.history/old.json',
                         'session.db.recording-failures/raw.json', 'session.db.bclc-sidebet-profile.json'):
            path = data / relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_text('{"synthetic":true}')
        return data

    def test_backup_copies_sidecars_and_preserves_originals(self):
        data = self.make_data(); destination = self.root / 'backup'
        before = {p.relative_to(data).as_posix():p.read_bytes() for p in data.rglob('*') if p.is_file()}
        receipt = backup(data, destination)
        self.assertEqual(len(receipt['files']), len(before))
        self.assertFalse(receipt['application_restore_tested'])
        for path, contents in before.items():
            self.assertEqual((destination / 'data' / path).read_bytes(), contents)
            self.assertEqual((data / path).read_bytes(), contents)

    def test_backup_rejects_existing_or_nested_target(self):
        data = self.make_data(); existing = self.root / 'existing'; existing.mkdir()
        for target in (existing, data / 'bad'):
            with self.assertRaises(ValueError):
                backup(data, target)

    def test_backup_detects_source_change_without_false_success(self):
        data = self.make_data(); destination = self.root / 'backup'
        original = shutil.copy2
        def changing(src, dst, **kwargs):
            result = original(src, dst, **kwargs)
            if str(src).endswith('raw.json'):
                Path(src).write_text('changed')
            return result
        with patch('trial_tools.shutil.copy2', side_effect=changing), self.assertRaises(RuntimeError):
            backup(data, destination)
        self.assertTrue((destination / 'BACKUP_INCOMPLETE.json').is_file())
        self.assertFalse((destination / 'BACKUP_MANIFEST.json').exists())

    def test_install_state_requires_exact_identity_and_success(self):
        value = dict(package_id=common.PACKAGE_ID, commit=common.COMMIT, install_root=str(self.root))
        common.write_json_new(self.root / 'install.json', value)
        with self.assertRaises(ValueError):
            common.load_install(self.root)
        self.assertEqual(common.load_install(self.root, False)['commit'], common.COMMIT)
        common.write_json_new(self.root / 'INSTALL_SUCCESS.json', {'package_id':common.PACKAGE_ID,'commit':common.COMMIT,'local_preflight_passed':True})
        self.assertEqual(common.load_install(self.root)['package_id'], common.PACKAGE_ID)

    def test_launcher_explicit_scope_and_trial_title(self):
        source = (KIT / 'launch_trial.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                 and node.func.id == 'TrialApp']
        self.assertEqual(len(calls), 1)
        arguments = {kw.arg: ast.literal_eval(kw.value) for kw in calls[0].keywords}
        self.assertEqual(arguments, dict(auto_analysis=True, background_recording=True,
                                        recording_process=True, sidebet_research=False))
        self.assertIn('sys.path.insert(0, str(ROOT))', source)
        self.assertIn('试用版', common.TITLE)
        self.assertIn('freeze_support()', source)
        factories = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                     and isinstance(node.func, ast.Name) and node.func.id == 'trial_app_class']
        self.assertEqual(len(factories), 1)
        self.assertEqual(ast.unparse(factories[0].args[0]), 'BlackjackLabApp')

    def test_trial_identity_survives_constructor_view_switch_and_wm_alias(self):
        from launch_trial import trial_app_class

        class UpstreamWindow:
            def __init__(self):
                self.caption = ''
                self.title('恢复上次记录')

            def title(self, string=None):
                if string is None:
                    return self.caption
                self.caption = string
                return ''

            wm_title = title

        window = trial_app_class(UpstreamWindow)()
        prefix = f'{common.TITLE} · {common.COMMIT[:7]} · '
        self.assertEqual(window.title(), prefix + '恢复上次记录')
        window.title('研究工作台')
        self.assertEqual(window.title(), prefix + '研究工作台')
        window.wm_title('当前手牌')
        self.assertEqual(window.title(), prefix + '当前手牌')
        window.title(window.title())
        self.assertEqual(window.title(), prefix + '当前手牌')

    def test_manifest_does_not_claim_offline_or_windows_acceptance(self):
        package = json.loads((KIT / 'PACKAGE.json').read_text(encoding='utf-8'))
        self.assertFalse(package['self_contained_offline_executable'])
        self.assertFalse(package['native_windows_installer_tested'])
        self.assertFalse(package['bundled_upstream_source'])
        self.assertFalse(package['real_time_acceptance'])
        self.assertTrue(package['main_analysis'])
        self.assertFalse(package['sidebet_research'])

    def test_python_launcher_cannot_install_on_demand_from_inherited_flags(self):
        batch = (KIT / 'INSTALL_TRIAL.cmd').read_text(encoding='utf-8')
        self.assertIn('set PYTHON_MANAGER_AUTOMATIC_INSTALL=false', batch)
        self.assertIn('set PYLAUNCHER_ALLOW_INSTALL=\n', batch)
        self.assertIn('set PYLAUNCHER_ALWAYS_INSTALL=\n', batch)
        self.assertLess(batch.index('set PYTHON_MANAGER_AUTOMATIC_INSTALL=false'), batch.index('py -3.14'))

    def test_isolated_launcher_can_import_its_own_helpers(self):
        code = "import runpy,sys; runpy.run_path(sys.argv[1],run_name='packaging_import_only')"
        result = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(KIT / 'launch_trial.py')], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_invalid_install_success_receipt_is_rejected(self):
        value = dict(package_id=common.PACKAGE_ID, commit=common.COMMIT, install_root=str(self.root))
        common.write_json_new(self.root / 'install.json', value)
        common.write_json_new(self.root / 'INSTALL_SUCCESS.json', {'local_preflight_passed':True})
        with self.assertRaises(ValueError):
            common.load_install(self.root)

    def test_cli_help_without_installing(self):
        result = subprocess.run([sys.executable, str(KIT / 'install_trial.py'), '--help'], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b'--source-zip', result.stdout)

    @unittest.skipUnless(os.name == 'nt', 'Requires native CMD batch execution')
    def test_uninstall_cmd_keeps_success_after_deleting_its_own_directory(self):
        from install_trial import uninstall_batch
        program = self.root / 'program 中文 space'
        program.mkdir()
        (program / 'trial_tools.py').write_text(
            "from pathlib import Path\nimport shutil\n"
            "root=Path(__file__).resolve().parent\n"
            "assert root.name=='program 中文 space'\n"
            "shutil.rmtree(root)\n", encoding='utf-8')
        entry = program / 'UNINSTALL_TRIAL.cmd'
        uninstall_batch(entry, self.root / 'uninstall runner 中文.cmd', sys.executable)
        result = subprocess.run([os.environ['COMSPEC'], '/d', '/c', str(entry)],
                                cwd=self.root, input=b'\r\n', capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(program.exists())

    @unittest.skipUnless(os.name == 'nt', 'Requires native CMD batch execution')
    def test_uninstall_cmd_preserves_failure_and_program_directory(self):
        from install_trial import uninstall_batch
        program = self.root / 'program 中文 space'
        program.mkdir()
        (program / 'trial_tools.py').write_text('raise SystemExit(1)\n', encoding='utf-8')
        entry = program / 'UNINSTALL_TRIAL.cmd'
        uninstall_batch(entry, self.root / 'uninstall runner 中文.cmd', sys.executable)
        result = subprocess.run([os.environ['COMSPEC'], '/d', '/c', str(entry)],
                                cwd=self.root, input=b'\r\n', capture_output=True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertTrue(entry.is_file())


if __name__ == '__main__':
    unittest.main(verbosity=2)
