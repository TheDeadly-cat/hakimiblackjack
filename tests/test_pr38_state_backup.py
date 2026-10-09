"""Folder-copy recovery includes mode history and unresolved original inputs."""
import hashlib
from pathlib import Path
import shutil
import unittest

from blackjack_lab.ui.app import BlackjackLabApp
from blackjack_lab.ui.table_modes import BCLC, PRAGMATIC, ModeSettings, TableModeStore, legacy_bclc_draft_rules
from tests import test_recording_background_ui as fixture


def hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


class NewStateBackupTests(unittest.TestCase):
    use_process = True
    setUp = fixture.RecordingBackgroundUITests.setUp
    pump = fixture.RecordingBackgroundUITests.pump

    def close(self):
        if self.app:
            self.app.on_close()
            self.pump(lambda: self.app.exit_flow.phase == 'needs_recording')
            self.app.exit_flow.discard()
            self.pump(lambda: self.app.exit_flow.phase == 'finished')
            self.app = None

    def test_all_new_state_and_failure_originals_survive_independent_folder_restore(self):
        app = self.app
        app.select_table_mode(BCLC); app.act_new_shoe(); app.act_new_round()
        app.sidebets.apply_profile(app.sidebets.profile)
        store = TableModeStore(self.db)
        store.save_switch(PRAGMATIC, store.get(PRAGMATIC), BCLC,
                          ModeSettings(legacy_bclc_draft_rules(), 'reverse', False))
        original_mode = store.path.read_bytes()
        store.save_switch(BCLC, store.get(BCLC), BCLC, store.get(BCLC))
        history = list(store.path.with_name(store.path.name + '.history').glob('*.json'))
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].read_bytes(), original_mode)
        app._key_rank('Z'); app._key_rank('6')
        self.pump(lambda: not app.recording_busy and app._recording_owner.stopped)
        expected_events = app.ctrl.ledger.to_list()
        statuses = [f['status'] for f in app._recording_faults]
        self.assertEqual(statuses, ['failed_before_commit', 'not_executed'])
        app.on_close(); self.pump(lambda: app.exit_flow.phase == 'needs_recording')
        app.exit_flow.discard(); self.pump(lambda: app.exit_flow.phase == 'finished')
        self.app = None
        root = Path(self.tmp.name)
        source = root / 'source'; source.mkdir()
        # Only this test's new files are grouped; user files are never enumerated.
        for path in list(root.glob('background-ui.db*')):
            if path.is_dir():
                shutil.copytree(path, source / path.name)
            else:
                shutil.copy2(path, source / path.name)
        before = hashes(source)
        for suffix in ('.table-modes.json', '.bclc-sidebet-profile.json'):
            self.assertIn(self.db.name + suffix, before)
        for suffix in ('.table-modes.json.history/', '.recording-failures/'):
            self.assertTrue(any(name.startswith(self.db.name + suffix) for name in before), suffix)
        backup, restored = root / 'backup', root / 'restored'
        shutil.copytree(source, backup); shutil.copytree(backup, restored)
        self.assertEqual(hashes(backup), before)
        self.assertEqual(hashes(restored), before)
        recovered_db = restored / self.db.name
        self.app = BlackjackLabApp(recovered_db, auto_analysis=False,
                                   background_recording=True, recording_process=True)
        self.assertEqual(self.app.ctrl.ledger.to_list(), expected_events)
        self.assertEqual([f['status'] for f in self.app._recording_faults], statuses)
        self.assertFalse(self.app.recording_busy)
        self.app._key_rank('8'); self.assertFalse(self.app.recording_busy)
        self.assertIn('当前建议暂停', self.app.compact_panel.message.get())
        for name, digest in before.items():
            if '.recording-failures/' in name or '.table-modes.json.history/' in name:
                self.assertEqual(hashlib.sha256((restored / name).read_bytes()).hexdigest(), digest)
        self.assertEqual(hashes(source), before)
        self.assertEqual(hashes(backup), before)
