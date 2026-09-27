import copy
import json
from pathlib import Path
import tempfile
import unittest

from blackjack_lab.storage.history_catalog import metadata_page
from tests import test_sidebet_history as fixture


class CatalogTests(unittest.TestCase):
    def test_pages_are_bounded_disjoint_and_do_not_claim_result_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for i in range(205):
                sid=f'{i:032x}'
                (root/(sid+'.json')).write_text(json.dumps(dict(snapshot_id=sid,saved_at=i,
                    result=dict(input=dict(through_seq=12,seat='玩家1'),status='available'),
                    content_digest='intentionally invalid')),encoding='utf-8')
            pages=[metadata_page(root,offset=i) for i in (0,100,200)]
            self.assertEqual([len(p['entries']) for p in pages],[100,100,5])
            rows=[r for p in pages for r in p['entries']]
            self.assertEqual(len({r['snapshot_id'] for r in rows}),205)
            self.assertTrue(all(r['metadata_only'] and 'result' not in r for r in rows))
            self.assertEqual(metadata_page(root,cancelled=lambda:True)['entries'],[])


class CatalogVerificationUITests(unittest.TestCase):
    setUp=fixture.SidebetUITests.setUp
    close=fixture.SidebetUITests.close
    start=fixture.SidebetUITests.start
    forecast=fixture.SidebetUITests.forecast
    pump=fixture.SidebetUITests.pump

    def test_forged_record_metadata_never_enables_recompute_or_shows_forged_ev(self):
        self.start();saved=self.forecast();path=self.view.store.directory/(saved['saved_id']+'.json')
        bad=json.loads(path.read_text(encoding='utf-8'));bad['result']['output']['bets']['perfect_pairs']['ev']=999
        path.write_text(json.dumps(bad),encoding='utf-8');original=path.read_bytes()
        self.view.show_history();h=self.view.history
        self.pump(lambda:'内容摘要不符' in h.status.get())
        self.assertEqual(len(h.entries),1);self.assertIsNone(h.verified)
        self.assertTrue(h.recompute_button.instate(['disabled']))
        self.assertNotIn('999',h.text.get('1.0','end'))
        h.recompute();self.assertEqual(path.read_bytes(),original)
