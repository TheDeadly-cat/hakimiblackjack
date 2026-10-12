"""Custom install isolation/recovery and visible asynchronous installer flow."""
from pathlib import Path
import json,sys,tempfile,time,tkinter as tk,unittest
from unittest.mock import patch
KIT=Path(__file__).resolve().parents[1]/'packaging'
sys.path.insert(0,str(KIT/'windows_trial'));sys.path.insert(0,str(KIT/'windows_offline_trial_o3'))
import offline_o3_setup as setup
from offline_o3_common import create_install_layout
from offline_o3_setup_ui import InstallerWindow

class InstallerFlowTests(unittest.TestCase):
    def test_two_custom_updates_and_failed_program_restore_keep_distinct_data(self):
        with tempfile.TemporaryDirectory() as temp:
            plans=[create_install_layout(Path(temp)/name) for name in ('桌面 甲','目录 乙')]
            for plan in plans:
                root,data=plan['root'],plan['data'];root.mkdir(parents=True);data.mkdir()
                (data/'records.txt').write_text(plan['instance_id'])
                (root/'program.bin').write_bytes(b'old-program')
                (root/'install.json').write_text(json.dumps(dict(package_id=setup.PACKAGE_ID,commit=setup.COMMIT,
                    install_root=str(root),data_root=str(data),**plan['info_fields'])))
                (root/'INSTALL_SUCCESS.json').write_text(json.dumps(dict(package_id=setup.PACKAGE_ID,
                    commit=setup.COMMIT,preflight_passed=True)))
            first=plans[0];planned=setup.plan_install(first['base'])
            self.assertEqual(planned['instance_id'],first['instance_id'])
            logs=first['logs_root']/'owned-failure-one';logs.mkdir(parents=True)
            with self.assertRaisesRegex(RuntimeError,'owned failure'):
                with setup.program_replacement(first['root'],first['data'],logs,True,layout=planned):
                    first['root'].mkdir();(first['root']/'partial.bin').write_bytes(b'partial')
                    raise RuntimeError('owned failure')
            self.assertEqual((first['root']/'program.bin').read_bytes(),b'old-program')
            self.assertTrue((logs/'UPDATE_ROLLED_BACK.json').exists())
            for plan in plans:self.assertEqual((plan['data']/'records.txt').read_text(),plan['instance_id'])
            self.assertEqual((plans[1]['root']/'program.bin').read_bytes(),b'old-program')

    def test_unrecognized_nonempty_selection_is_rejected_without_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'existing';root.mkdir();(root/'user.txt').write_bytes(b'original')
            with self.assertRaises(ValueError):setup.plan_install(root)
            self.assertEqual(list(root.iterdir()),[root/'user.txt'])

    def test_visible_window_keeps_heartbeat_during_work_and_launch_is_explicit(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'app';root.mkdir();logs=Path(temp)/'logs'
            from threading import Event
            release=Event();entered=Event()
            def installer(reuse,destination,progress):
                progress('检查运行环境与程序');entered.set();release.wait(2);return root,logs
            window=InstallerWindow(installer,Path(temp)/'可选目录')
            try:
                window.update();self.assertTrue(window.winfo_viewable());window.start()
                deadline=time.monotonic()+1
                while not entered.is_set() and time.monotonic()<deadline:window.update();time.sleep(.005)
                self.assertTrue(entered.is_set());self.assertTrue(window.busy)
                window.close();window.update();self.assertTrue(window.winfo_exists())
                release.set();deadline=time.monotonic()+2
                while window.result is None and time.monotonic()<deadline:window.update();time.sleep(.01)
                self.assertEqual(window.result,(root,logs));self.assertEqual(window.stage.get(),'安装完成')
                with patch('offline_o3_setup_ui.subprocess.Popen') as launch:
                    launch.assert_not_called();window.launch();launch.assert_called_once()
                    self.assertEqual(launch.call_args.args[0],[str(root/setup.EXECUTABLE)])
            finally:release.set();window.destroy()

    def test_install_error_returns_to_location_selection_without_launch(self):
        def installer(*args,**kwargs):raise RuntimeError('owned refusal')
        window=InstallerWindow(installer,Path(tempfile.gettempdir())/'owned-ui-error')
        try:
            window.start();deadline=time.monotonic()+2
            while window.busy and time.monotonic()<deadline:window.update();time.sleep(.01)
            self.assertFalse(window.busy);self.assertIsNone(window.result)
            self.assertIn('owned refusal',window.detail.get());self.assertEqual(str(window.entry['state']),'normal')
            with patch('offline_o3_setup_ui.subprocess.Popen') as launch:window.launch();launch.assert_not_called()
        finally:window.destroy()

    def test_yes_command_uses_no_tk_and_passes_exact_destination(self):
        with patch.object(sys,'argv',['setup','--yes','--install-dir',str(Path(tempfile.gettempdir())/'owned-cli')]),patch.object(setup,'install') as install:
            self.assertEqual(setup.main(),0)
            install.assert_called_once_with(False,Path(tempfile.gettempdir())/'owned-cli')
