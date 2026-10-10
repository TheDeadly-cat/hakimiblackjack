from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest

from blackjack_lab.storage.safe_files import atomic_write


class TestAtomicNewFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'immutable-result.json'

    def test_existing_result_is_never_replaced(self):
        self.path.write_bytes(b'original evidence')
        with self.assertRaises(FileExistsError):
            atomic_write(self.path, b'new evidence', overwrite=False)
        self.assertEqual(self.path.read_bytes(), b'original evidence')

    def test_concurrent_writers_publish_exactly_one_complete_result(self):
        values = [b'a'*8192, b'b'*8192]
        def publish(value):
            try:
                atomic_write(self.path, value, overwrite=False)
                return True
            except FileExistsError:
                return False
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(publish, values))
        self.assertEqual(sum(results), 1)
        self.assertIn(self.path.read_bytes(), values)
        self.assertEqual(list(self.path.parent.glob('.hakimi-output-*')), [])


if __name__ == '__main__':
    unittest.main()
