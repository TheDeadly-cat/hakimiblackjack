"""Windows split-accelerator precheck and restricted-environment failure paths."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from blackjack_lab.analysis.environment import inspect_environment
from blackjack_lab.analysis.information import build_input
from blackjack_lab.analysis.service import calculate
from blackjack_lab.analysis.split_contracts import split_research_rules
from tests.test_analysis_integration import example

ROOT = Path(__file__).resolve().parents[1]


def _isolated_source(directory, tag=b't2-env'):
    from blackjack_lab.analysis import native_backend as backend
    source = Path(directory) / 'lab/analysis/native/SplitEngine.cs'
    source.parent.mkdir(parents=True)
    source.write_bytes(backend.SOURCE.read_bytes() + b'\n// ' + tag + b'\n')
    return source


def _corrupt_artifact(source):
    from blackjack_lab.analysis import native_backend as backend
    key = hashlib.sha256(source.read_bytes()).hexdigest()
    cache = source.parents[2] / '.local-native' / key
    cache.mkdir(parents=True, exist_ok=True)
    executable = cache / 'SplitEngine.exe'
    receipt = cache / 'build.json'
    executable.write_bytes(b'not-a-real-split-engine')
    receipt.write_text(json.dumps(dict(source_sha256=key, flags=list(backend.FLAGS),
                                       binary_sha256='0' * 64, compiler='t2-corrupt')),
                       encoding='utf-8')
    sentinel = cache / 'do-not-delete.txt'
    sentinel.write_text('keep-this-diagnostic-file', encoding='utf-8')
    return cache, executable, sentinel


@unittest.skipUnless(os.name == 'nt', 'Windows split accelerator')
class TestSplitEnvironment(unittest.TestCase):
    def test_this_machine_is_ready_without_installing_software(self):
        report = inspect_environment()
        self.assertTrue(report['ready'], report['failures'])
        self.assertEqual(report['bits'], 64)
        self.assertTrue(report['compiler_present'])
        self.assertTrue(report['cache_writable'])
        self.assertTrue(report['artifact'] and report['artifact']['valid'])
        self.assertFalse(report['installs_software'])
        self.assertFalse(report['changes_execution_policy'])
        self.assertFalse(report['requires_elevation'])

    def test_prepared_artifact_is_reused_without_compiling(self):
        from blackjack_lab.analysis import native_backend as backend
        calls = []
        original = backend._compile_source

        def counting(frozen, output, timeout):
            calls.append(1)
            return original(frozen, output, timeout)

        with patch.object(backend, '_compile_source', counting):
            report = inspect_environment(prepare=True)
        self.assertTrue(report['ready'], report['failures'])
        self.assertEqual(calls, [])
        self.assertIsNotNone(report['prepare_seconds'])
        self.assertTrue(report['artifact']['valid'])

    def test_first_compile_is_separate_from_later_reuse(self):
        from blackjack_lab.analysis import native_backend as backend
        calls = []
        original = backend._compile_source

        def counting(frozen, output, timeout):
            calls.append(1)
            return original(frozen, output, timeout)

        with tempfile.TemporaryDirectory() as directory:
            source = _isolated_source(directory, b't2-cold-vs-prepared')
            with patch.object(backend, 'SOURCE', source), patch.object(backend, '_compile_source', counting):
                first = backend.build_native()
                second = backend.build_native()
                self.assertEqual(len(calls), 1)
                self.assertEqual(first, second)
                self.assertTrue(first.is_file())
                self.assertTrue(first.with_name('build.json').is_file())

    def test_missing_compiler_fails_analysis_but_recording_still_works(self):
        from blackjack_lab.analysis import native_backend as backend
        ledger = example(cards=('8', '8'), up='6', rules=split_research_rules())
        before = ledger.to_list()
        with tempfile.TemporaryDirectory() as directory:
            source = _isolated_source(directory, b't2-missing-compiler')
            missing = Path(directory) / 'no-csc.exe'
            with patch.object(backend, 'SOURCE', source), patch.object(backend, 'compiler_path', lambda: missing):
                report = inspect_environment()
                result = calculate(build_input(ledger, '玩家1'))
        self.assertFalse(report['ready'])
        self.assertTrue(any(item['code'] == 'COMPILER_MISSING' for item in report['failures']))
        self.assertNotEqual(result['status'], 'available')
        self.assertEqual(result['actions'], {})
        self.assertIsNone(result['probabilities'])
        self.assertIsNone(result['highest_ev_action'])
        self.assertIn('编译器', result['reason'])
        hand_id = build_input(ledger, '玩家1').hand_id
        ledger.player_action('玩家1', hand_id, '分牌')
        self.assertGreater(len(ledger.to_list()), len(before))

    def test_hash_mismatch_keeps_files_and_does_not_publish_placeholder_ev(self):
        from blackjack_lab.analysis import native_backend as backend
        ledger = example(cards=('8', '8'), up='6', rules=split_research_rules())
        with tempfile.TemporaryDirectory() as directory:
            source = _isolated_source(directory, b't2-hash-mismatch')
            cache, executable, sentinel = _corrupt_artifact(source)
            user_db = Path(directory) / 'data' / 'blackjack_lab.db'
            user_db.parent.mkdir()
            user_db.write_text('user-records', encoding='utf-8')
            with patch.object(backend, 'SOURCE', source):
                with self.assertRaises(RuntimeError) as error:
                    backend.build_native()
                report = inspect_environment(prepare=True)
                result = calculate(build_input(ledger, '玩家1'))
            self.assertIn('摘要不匹配', str(error.exception))
            self.assertFalse(report['ready'])
            self.assertTrue(any(item['code'] == 'ARTIFACT_HASH_MISMATCH' for item in report['failures']))
            self.assertTrue(any(item['code'] == 'PREPARE_FAILED' for item in report['failures']))
            self.assertNotEqual(result['status'], 'available')
            self.assertEqual(result['actions'], {})
            self.assertIsNone(result['highest_ev_action'])
            self.assertIn('摘要不匹配', result['reason'])
            self.assertTrue(executable.exists())
            self.assertEqual(executable.read_bytes(), b'not-a-real-split-engine')
            self.assertTrue(sentinel.exists())
            self.assertEqual(user_db.read_text(encoding='utf-8'), 'user-records')
            self.assertTrue(cache.exists())

    def test_check_environment_cli_and_main_flag_report_ready(self):
        script = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'check_environment.py'), '--json'],
                                cwd=str(ROOT), capture_output=True, text=True, check=False)
        self.assertEqual(script.returncode, 0, script.stderr or script.stdout)
        report = json.loads(script.stdout)
        self.assertTrue(report['ready'])
        self.assertFalse(report['installs_software'])
        main = subprocess.run([sys.executable, '-m', 'blackjack_lab.main', '--check-environment'],
                              cwd=str(ROOT), capture_output=True, text=True, check=False)
        self.assertEqual(main.returncode, 0, main.stderr or main.stdout)
        self.assertIn('ready=True', main.stdout)


if __name__ == '__main__':
    unittest.main()
