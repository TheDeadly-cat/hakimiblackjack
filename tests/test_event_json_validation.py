"""Event JSON rejection and concurrent use of the production validator."""
from concurrent.futures import ThreadPoolExecutor
import json
import unittest

from blackjack_lab.ledger.events import Event, SESSION_STARTED


class EventJSONValidationTests(unittest.TestCase):
    def test_json_errors_match_fresh_validation_and_do_not_poison_next_event(self):
        cycle = {}
        cycle['self'] = cycle
        for value in (float('nan'), float('inf'), float('-inf'), object(), cycle):
            payload = {'note': value}
            with self.subTest(value=type(value).__name__):
                try:
                    json.dumps(payload, allow_nan=False)
                except Exception as error:
                    expected = type(error), str(error)
                else:
                    self.fail('Invalid test payload unexpectedly serialized')
                with self.assertRaises(expected[0]) as actual:
                    Event(SESSION_STARTED, payload)
                self.assertEqual(str(actual.exception), expected[1])
                next_payload = {'note': {'中文': [None, True, 1, 1.5, (2, 3)]}}
                event = Event(SESSION_STARTED, next_payload)
                self.assertIs(event.payload, next_payload)

    def test_concurrent_valid_and_invalid_payloads_keep_independent_markers(self):
        def check(index):
            value = [index, {'nested': [None, False, '中文']}]
            payload = {'note': value}
            event = Event(SESSION_STARTED, payload)
            self.assertIs(event.payload, payload)
            circular = []
            circular.append(circular)
            with self.assertRaisesRegex(ValueError, 'Circular reference detected'):
                Event(SESSION_STARTED, {'note': circular})
            another = Event(SESSION_STARTED, payload)
            self.assertIs(another.payload, payload)
            return index
        with ThreadPoolExecutor(max_workers=8) as executor:
            self.assertEqual(list(executor.map(check, range(80))), list(range(80)))


if __name__ == '__main__':
    unittest.main()
