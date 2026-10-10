"""Command preparation must preserve deep isolation and custom copy behavior."""
import unittest
from unittest.mock import patch

from blackjack_lab.analysis.split_contracts import both_initial_das_rules
from blackjack_lab.ledger.command import CommandCandidate
from blackjack_lab.ledger.ledger import EventLedger, LedgerError
from blackjack_lab.ledger.events import Event
from blackjack_lab.ui.read_snapshot import PrefixSnapshot


class CommandCopyIsolationTests(unittest.TestCase):
    def ledger(self):
        ledger = EventLedger('command-copy-regression')
        ledger.start_session()
        ledger.create_shoe(both_initial_das_rules(8))
        ledger.start_round(['玩家1'])
        ledger.deal('玩家1', '8')
        return ledger

    def test_nested_participants_and_card_payload_are_owned_and_mutation_is_rejected(self):
        ledger = self.ledger()
        baseline = PrefixSnapshot.capture(ledger)
        candidate = CommandCandidate(ledger, baseline)
        self.assertEqual(candidate.to_list(), ledger.to_list())
        self.assertIsNot(candidate.events[2].payload['participants'], ledger.events[2].payload['participants'])
        candidate.events[2].payload['participants'].append('玩家2')
        candidate.events[3].payload['rank'] = '9'
        self.assertEqual(ledger.events[2].payload['participants'], ['玩家1'])
        self.assertEqual(ledger.events[3].payload['rank'], '8')
        with self.assertRaises(LedgerError):
            candidate.validated_suffix()
        self.assertEqual(PrefixSnapshot.capture(ledger).content, baseline.content)

    def test_shared_builtin_payload_keeps_alias_only_inside_isolated_candidate(self):
        ledger = self.ledger()
        ledger.burn(1)
        ledger.burn(1)
        ledger.events[-1].payload = ledger.events[-2].payload
        candidate = CommandCandidate(ledger, PrefixSnapshot.capture(ledger))
        self.assertIs(candidate.events[-1].payload, candidate.events[-2].payload)
        self.assertIsNot(candidate.events[-1].payload, ledger.events[-1].payload)
        candidate.events[-1].payload['count'] = 2
        self.assertEqual(ledger.events[-1].payload['count'], 1)
        with self.assertRaises(LedgerError):
            candidate.validated_suffix()

    def test_custom_text_copy_protocol_is_preserved(self):
        calls = []

        class Text(str):
            def __deepcopy__(self, memo):
                calls.append(str(self))
                copied = Text(str(self))
                copied.notes = list(self.notes)
                return copied

        ledger = self.ledger()
        source = Text('手动录入')
        source.notes = ['original']
        ledger.events[-1].source = source
        candidate = CommandCandidate(ledger, PrefixSnapshot.capture(ledger))
        self.assertEqual(calls, ['手动录入'])
        self.assertIsInstance(candidate.events[-1].source, Text)
        candidate.events[-1].source.notes.append('candidate')
        self.assertEqual(source.notes, ['original'])
        self.assertEqual(candidate.to_list(), ledger.to_list())

    def test_event_new_argument_protocol_uses_original_deepcopy(self):
        ledger = self.ledger()
        baseline = PrefixSnapshot.capture(ledger)
        calls = []

        def new_arguments(event):
            calls.append(event.event_id)
            return ()

        with patch.object(Event, '__getnewargs__', new_arguments, create=True):
            candidate = CommandCandidate(ledger, baseline)
        self.assertEqual(calls, [event.event_id for event in ledger.events])
        self.assertEqual(candidate.to_list(), ledger.to_list())


if __name__ == '__main__':
    unittest.main()
