"""Windows split-accelerator precheck and restricted-environment failure paths."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
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

    def test_write_probe_uses_unique_temp_file_and_does_not_leave_it(self):
        from blackjack_lab.analysis import native_backend as backend
        parent = backend.native_cache_directory().parent
        fixed = parent / '.hakimi-write-probe'
        existed = fixed.exists()
        before = {path.name for path in parent.glob('.hakimi-write-*')}
        report = inspect_environment()
        after = {path.name for path in parent.glob('.hakimi-write-*')}
        self.assertTrue(report['cache_writable'])
        self.assertEqual(after, before)
        self.assertEqual(fixed.exists(), existed)

    def test_array_receipt_fails_closed_and_keeps_files(self):
        from blackjack_lab.analysis import native_backend as backend
        ledger = example(cards=('8', '8'), up='6', rules=split_research_rules())
        with tempfile.TemporaryDirectory() as directory:
            source = _isolated_source(directory, b't2b-array-receipt')
            cache, executable, sentinel = _corrupt_artifact(source)
            (cache / 'build.json').write_text('[]', encoding='utf-8')
            user_db = Path(directory) / 'data' / 'blackjack_lab.db'
            user_db.parent.mkdir()
            user_db.write_text('user-records', encoding='utf-8')
            with patch.object(backend, 'SOURCE', source):
                with self.assertRaises(RuntimeError) as error:
                    backend.build_native()
                report = inspect_environment()
                result = calculate(build_input(ledger, '玩家1'))
            self.assertNotIn('has no attribute', str(error.exception).lower())
            self.assertIn('构建回执', str(error.exception))
            self.assertFalse(report['ready'])
            self.assertTrue(any(item['code'] == 'ARTIFACT_UNREADABLE' for item in report['failures']))
            self.assertNotEqual(result['status'], 'available')
            self.assertEqual(result['actions'], {})
            self.assertIsNone(result['highest_ev_action'])
            self.assertEqual((cache / 'build.json').read_text(encoding='utf-8'), '[]')
            self.assertEqual(executable.read_bytes(), b'not-a-real-split-engine')
            self.assertTrue(sentinel.exists())
            self.assertEqual(user_db.read_text(encoding='utf-8'), 'user-records')


class TestBuildReceiptParsing(unittest.TestCase):
    """Receipt-root parsing with isolated files. Does not execute the C# binary."""

    def check(self, contents):
        from blackjack_lab.analysis import environment
        from blackjack_lab.analysis import native_backend as backend
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'lab/analysis/native/SplitEngine.cs'
            source.parent.mkdir(parents=True)
            source.write_bytes(b'// synthetic non-executed source\n')
            source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            cache = root / 'lab/.local-native' / source_hash
            cache.mkdir(parents=True)
            binary = cache / 'SplitEngine.exe'
            binary.write_bytes(b'synthetic non-executable bytes')
            receipt = cache / 'build.json'
            flags = list(backend.FLAGS)
            good = dict(source_sha256=source_hash, flags=flags,
                        binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest())
            text = json.dumps(good) if contents is None else contents
            receipt.write_text(text, encoding='utf-8')
            original = receipt.read_bytes(), binary.read_bytes()
            doubled = SimpleNamespace(SOURCE=source, FLAGS=tuple(flags), NO_WINDOW=0x08000000,
                compiler_path=lambda: root / 'not-installed-csc.exe',
                native_cache_directory=lambda: cache, source_digest=lambda: source_hash,
                parse_build_receipt=backend.parse_build_receipt,
                BuildReceiptError=backend.BuildReceiptError)
            tk = ModuleType('tkinter')
            tk.TkVersion = 8.6
            tk.Tk = lambda: SimpleNamespace(tk=SimpleNamespace(eval=lambda _: 'synthetic-Tcl'),
                                            destroy=lambda: None)
            with patch.object(environment, 'backend', doubled), patch.dict('sys.modules', {'tkinter': tk}):
                report = environment.inspect_environment(prepare=False)
            self.assertEqual((receipt.read_bytes(), binary.read_bytes()), original)
            return report

    def assert_receipt_failure(self, contents, code='ARTIFACT_UNREADABLE'):
        report = self.check(contents)
        self.assertFalse(report['ready'])
        self.assertTrue(any(item['code'] == code for item in report['failures']), report['failures'])
        self.assertTrue(any(item['code'].startswith('ARTIFACT_') for item in report['failures']))

    def test_valid_receipt_parses_control(self):
        report = self.check(None)
        self.assertTrue(report['artifact']['valid'])

    def test_syntax_error_is_controlled(self):
        self.assert_receipt_failure('{')

    def test_empty_object_is_controlled(self):
        self.assert_receipt_failure('{}')

    def test_array_root_is_controlled(self):
        self.assert_receipt_failure('[]')

    def test_null_root_is_controlled(self):
        self.assert_receipt_failure('null')

    def test_string_root_is_controlled(self):
        self.assert_receipt_failure('"invalid-receipt"')

    def test_parse_build_receipt_rejects_bad_roots(self):
        from blackjack_lab.analysis.native_backend import parse_build_receipt, BuildReceiptError
        for text in ('[]', 'null', '"invalid-receipt"', '{}', '{'):
            with self.subTest(text=text):
                with self.assertRaises(BuildReceiptError):
                    parse_build_receipt(text)


if __name__ == '__main__':
    unittest.main()
