"""Run byte-preserved review assertions with cooperative-exit fixture cleanup."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.tk_lifecycle import close_app


def main():
    directory=ROOT/'review_tests';name='test_v02a_review_regressions.py';path=directory/name
    provenance=json.loads((directory/'source-provenance.json').read_text(encoding='utf-8'))
    assert hashlib.sha256(path.read_bytes()).hexdigest()==provenance['files'][name]
    spec=importlib.util.spec_from_file_location('original_handoff_preserved',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    def cleanup(self):
        if self.app is not None:
            close_app(self.app)
            self.app=None
    # No test method, assertion, card input, reference output or skip changes.
    module.TestAnalysisLifecycleReview.close_app=cleanup
    print('Original review source/assertions preserved; cleanup pumps cooperative shutdown.',flush=True)
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(module))
    return 0 if result.wasSuccessful() else 1


if __name__=='__main__':raise SystemExit(main())
