"""Real parser and config model; the merge tests deliberately do not compute EV."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.run_experiment import main
from blackjack_lab.experiments.contracts import KIND_HISTORY, KIND_SYNTHETIC


FILE_CONFIG = dict(n_decks='8', template='das', player_ranks='8,8',
                   dealer_up='6', extra_removed='K', seat='玩家3')


class TestExperimentCliConfig(unittest.TestCase):
    def invoke(self, data=None, extra=(), use_file=True):
        captured = []
        def stub_run(config, output):
            captured.append(config)
            output.mkdir()
            return dict(json=output/'stub.json', csv=output/'stub.csv',
                        record={'items': [{'status': 'available'}]})
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            args = ['--output', str(root/'out')]
            if use_file:
                source = root/'config.json'
                source.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
                args += ['--config', str(source)]
            stream = io.StringIO()
            with patch('scripts.run_experiment.ExperimentRunner.run', side_effect=stub_run), redirect_stdout(stream):
                self.assertEqual(main([*args, *extra]), 0)
            response = json.loads(stream.getvalue())
            saved = json.loads((root/'out/effective_config.json').read_text(encoding='utf-8'))
            self.assertEqual(saved, response['effective_config'])
            for key, value in saved.items():
                expected = getattr(captured[0], key)
                self.assertEqual(value, list(expected) if type(expected) is tuple else expected, key)
            self.assertEqual(response['experiment_id'], captured[0].experiment_id)
            return captured[0], saved

    def test_defaults_without_file(self):
        cfg, _ = self.invoke(use_file=False)
        self.assertEqual((cfg.kind, cfg.n_decks, cfg.template, cfg.player_ranks, cfg.dealer_up,
                          cfg.extra_removed, cfg.seat),
                         (KIND_SYNTHETIC, (6, 7, 8), 'single', ('10', '6'), '10', (), '玩家1'))

    def test_json_alone_drives_all_conditions(self):
        cfg, _ = self.invoke(FILE_CONFIG)
        self.assertEqual((cfg.n_decks, cfg.template, cfg.player_ranks, cfg.dealer_up, cfg.extra_removed, cfg.seat),
                         ((8,), 'das', ('8', '8'), '6', ('K',), '玩家3'))

    def test_one_explicit_override_preserves_other_conditions(self):
        cfg, _ = self.invoke(FILE_CONFIG, ('--player', '9,9'))
        self.assertEqual((cfg.n_decks, cfg.template, cfg.player_ranks, cfg.dealer_up, cfg.extra_removed, cfg.seat),
                         ((8,), 'das', ('9', '9'), '6', ('K',), '玩家3'))

    def test_all_explicit_overrides(self):
        cfg, _ = self.invoke(FILE_CONFIG, ('--mode', 'synthetic', '--decks', '6', '--template', 'split',
                                          '--player', '9,9', '--up', '5', '--removed', 'A', '--seat', '玩家2'))
        self.assertEqual((cfg.n_decks, cfg.template, cfg.player_ranks, cfg.dealer_up, cfg.extra_removed, cfg.seat),
                         ((6,), 'split', ('9', '9'), '5', ('A',), '玩家2'))

    def test_explicit_empty_removal_and_file_alias_cannot_refill(self):
        for data in (FILE_CONFIG, dict(decks='8', template='das', player='8,8', up='6', removed='K', seat='玩家3')):
            cfg, saved = self.invoke(data, ('--removed', ''))
            self.assertEqual(cfg.extra_removed, ())
            self.assertEqual(cfg.n_decks, (8,))
            self.assertEqual(saved['extra_removed'], [])

    def test_history_target_from_json_and_explicit_clear_do_not_fall_back(self):
        data = dict(FILE_CONFIG, kind=KIND_HISTORY, db_path='isolated.db', session_id='test-session', through_seq=17)
        cfg, _ = self.invoke(data)
        self.assertEqual((cfg.kind, cfg.db_path, cfg.session_id, cfg.through_seq),
                         (KIND_HISTORY, 'isolated.db', 'test-session', 17))
        cfg, _ = self.invoke(data, ('--session', '', '--through-seq', '19'))
        self.assertEqual((cfg.session_id, cfg.through_seq), ('', 19))

    def test_effective_config_is_reusable_without_metadata_overrides(self):
        _, saved = self.invoke(FILE_CONFIG)
        _, roundtrip = self.invoke(saved)
        self.assertEqual(saved, roundtrip)

    def test_invalid_roots_unknown_fields_and_types_never_start_runner(self):
        invalid = [[], [['n_decks', '8']], None, 8, 'text', True,
                   dict(FILE_CONFIG, templat='single'), dict(FILE_CONFIG, removed='A'),
                   dict(FILE_CONFIG, n_decks=[6.9]), dict(FILE_CONFIG, n_decks=True),
                   dict(FILE_CONFIG, template=[]), dict(FILE_CONFIG, player_ranks={'8': 2}),
                   dict(FILE_CONFIG, player_ranks=[True, 8]), dict(FILE_CONFIG, dealer_up=False),
                   dict(FILE_CONFIG, extra_removed=False), dict(FILE_CONFIG, seat=['玩家1']),
                   dict(FILE_CONFIG, note={}), dict(FILE_CONFIG, db_path=3),
                   dict(FILE_CONFIG, session_id=[]), dict(FILE_CONFIG, through_seq=4.2),
                   dict(FILE_CONFIG, peek_negative=[])]
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder)/'bad.json'
            for data in invalid:
                with self.subTest(data=data):
                    source.write_text(json.dumps(data), encoding='utf-8')
                    with patch('scripts.run_experiment.ExperimentRunner.run') as runner, redirect_stderr(io.StringIO()):
                        with self.assertRaises(SystemExit) as error:
                            main(['--config', str(source), '--output', str(Path(folder)/'out')])
                        self.assertEqual(error.exception.code, 2)
                        runner.assert_not_called()
                    self.assertFalse((Path(folder)/'out').exists())


if __name__ == '__main__':
    unittest.main()
