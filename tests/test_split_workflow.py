"""B01-B14: prefix identity, actual recording, shared values and real Tk lifecycle."""
import copy
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from blackjack_lab.analysis.contracts import InputUnavailable, canonical, research_rules
from blackjack_lab.analysis.information import build_input
from blackjack_lab.analysis.service import calculate
from blackjack_lab.analysis.split_contracts import SplitAnalysisInput, split_research_rules
from blackjack_lab.ledger.ledger import EventLedger
from blackjack_lab.storage.export import export_json, import_json, export_csv, import_csv
from blackjack_lab.ui.controller import SessionController
from tests.test_analysis_integration import example
from tests import test_analysis_ui as ui_fixture


def split_example(n=6, pair='8', up='6'):
    ledger=example(n,cards=(pair,pair),up=up,rules=split_research_rules(n))
    hand_id=build_input(ledger,'玩家1').hand_id
    ledger.player_action('玩家1',hand_id,'分牌')
    return ledger,hand_id


class TestSplitWorkflow(unittest.TestCase):
    def test_raw_rank_pair_guard_rejects_forged_ten_bucket_legality(self):
        for cards in (('J','K'),('T','T')):
            snapshot=build_input(example(cards=cards,up='6',rules=split_research_rules()),'玩家1')
            information=json.loads(snapshot.information_json)
            information['action_states']['分牌']['allowed']=True
            forged=replace(snapshot,legal_actions=(*snapshot.legal_actions,'split'),uncertain_actions=(),
                           information_json=canonical(information))
            with self.assertRaises(ValueError):
                forged.validate()
            result=calculate(forged)
            self.assertEqual(result['status'],'failed')
            self.assertEqual(result['actions'],{})

    def test_natural_21_presplit_does_not_display_an_illegal_draw_metric(self):
        from blackjack_lab.ui.analysis_panel import format_result
        snapshot=build_input(example(cards=('A','10'),up='6',rules=split_research_rules()),'玩家1')
        result=calculate(snapshot)
        self.assertEqual(result['status'],'available',result['reason'])
        self.assertEqual(result['actions']['stand']['ev'],1.5)
        self.assertEqual(result['actions']['hit']['status'],'inapplicable')
        self.assertNotIn('爆牌概率',format_result(result))

    def test_b01_three_deck_sizes_compare_all_pre_split_actions(self):
        values=[]
        for decks in (6,7,8):
            snapshot=build_input(example(decks,cards=('8','8'),up='6',rules=split_research_rules(decks)),'玩家1')
            self.assertIsInstance(snapshot,SplitAnalysisInput)
            self.assertEqual(SplitAnalysisInput.from_dict(json.loads(canonical(snapshot.to_dict()))),snapshot)
            result=calculate(snapshot)
            self.assertEqual(result['status'],'available',result['reason'])
            self.assertFalse(result['partial_comparison'])
            self.assertEqual(result['actions']['split']['total_investment'],2)
            self.assertEqual(result['current_investment'],1)
            self.assertEqual(sum(snapshot.counts),decks*52-3)
            values.append(result['actions']['split']['ev'])
        self.assertEqual(len(set(values)),3)

    def test_b02_first_card_removed_once_origin_stable_and_forced_hit(self):
        ledger,first=split_example()
        before=build_input(ledger,'玩家1')
        event=ledger.deal('玩家1','10',hand_id=first)
        after=build_input(ledger,'玩家1')
        self.assertEqual(after.counts[-1],before.counts[-1]-1)
        self.assertEqual(after.physical_remaining,before.physical_remaining-1)
        self.assertEqual(after.hands[1],before.hands[1])
        self.assertEqual(after.hands[0].origin_event_ids,before.hands[0].origin_event_ids)
        self.assertEqual(after.hands[0].card_event_ids[-1],event.event_id)
        ordinary=calculate(after)
        ledger.player_action('玩家1',first,'补牌')
        forced=build_input(ledger,'玩家1')
        self.assertEqual(forced.legal_actions,('deal',))
        result=calculate(forced)
        self.assertEqual(result['status'],'available',result['reason'])
        self.assertAlmostEqual(result['actions']['deal']['ev'],ordinary['actions']['hit']['ev'],delta=1e-10)

    def test_b03_b08_first_bust_second_continues_in_total_units(self):
        ledger,first=split_example()
        ledger.deal('玩家1','10',hand_id=first)
        ledger.deal('玩家1','10',hand_id=first)
        snapshot=build_input(ledger,'玩家1',first)
        self.assertEqual(snapshot.active_index,1)
        result=calculate(snapshot)
        self.assertEqual(result['status'],'available',result['reason'])
        item=result['actions']['deal']
        self.assertAlmostEqual(item['hand_evs'][0],-1,delta=1e-10)
        self.assertGreater(item['ev'],-2)
        self.assertEqual(set(item['net_distribution']),{'-2','-1','0','1','2'})
        self.assertEqual(result['current_investment'],2)
        self.assertEqual(item['additional_investment'],0)

    def test_b04_ace_limit_and_both_closed_are_ordinary_21(self):
        ledger,first=split_example(pair='A')
        ledger.deal('玩家1','10',hand_id=first)
        s=build_input(ledger,'玩家1')
        self.assertEqual(s.active_index,1)
        second=s.hands[1].hand_id
        ledger.deal('玩家1','10',hand_id=second)
        s=build_input(ledger,'玩家1')
        self.assertEqual(s.legal_actions,('complete',))
        result=calculate(s)
        self.assertEqual(result['status'],'available',result['reason'])
        self.assertLessEqual(result['actions']['complete']['ev'],2)
        for target in (first,second):
            states=ledger.replay().current.table.action_states('玩家1',target)
            self.assertFalse(any(item.allowed for item in states.values()))
        with self.assertRaises(Exception):
            ledger.deal('玩家1','2',hand_id=first)

    def test_b05_original_four_hand_rules_are_not_truncated(self):
        ledger=example(cards=('8','8'),up='6',rules=research_rules())
        result=calculate(build_input(ledger,'玩家1'))
        self.assertTrue(result['partial_comparison'])
        self.assertEqual(result['actions']['split']['status'],'unsupported')
        for field,value in [('max_split_hands',4),('double_after_split',True),
                            ('resplit_aces',True),('split_deal_order','both_second_cards_first')]:
            rules=split_research_rules()
            setattr(rules,field,value)
            with self.subTest(field=field),self.assertRaises(InputUnavailable):
                build_input(example(cards=('8','8'),up='6',rules=rules),'玩家1')

    def test_b06_future_second_card_and_hole_do_not_change_original_prefix(self):
        ledger,first=split_example()
        before=build_input(ledger,'玩家1')
        for future in ('2','10'):
            later=copy.deepcopy(ledger)
            later.deal('玩家1','10',hand_id=first)
            later.player_action('玩家1',first,'停牌')
            second=build_input(later,'玩家1').active_hand_id
            later.deal('玩家1',future,hand_id=second)
            hole_id=next(key for key,value in later.replay().current.unresolved.items() if value['seat']=='庄家')
            later.reveal(hole_id,'8' if future=='2' else '9')
            self.assertEqual(build_input(later,'玩家1',through_seq=before.through_seq),before)
        self.assertNotIn('future',canonical(before.to_dict()))

    def test_b11_wrong_order_recorded_then_undo_restores_analysis(self):
        ledger,first=split_example()
        before=build_input(ledger,'玩家1')
        event=ledger.deal('玩家1','9',hand_id=before.hands[1].hand_id)
        self.assertIn(event.event_id,[e.event_id for e in ledger.events])
        with self.assertRaises(InputUnavailable) as error:
            build_input(ledger,'玩家1')
        self.assertEqual(error.exception.code,'SPLIT_DEAL_ORDER')
        ledger.undo_last('撤销合成顺序错误')
        after=build_input(ledger,'玩家1')
        self.assertEqual(after.hands,before.hands)
        self.assertEqual(after.counts,before.counts)

    def test_b11_cross_round_missing_observation_still_blocks_new_template(self):
        ledger,first=split_example()
        ledger.end_round(settle=False,reason='合成缺第二张',observation_status='complete')
        ledger.start_round(['玩家1'])
        ledger.deal('庄家','6');ledger.deal('庄家',hidden=True)
        ledger.deal('玩家1','8');ledger.deal('玩家1','8')
        with self.assertRaises(InputUnavailable) as error:
            build_input(ledger,'玩家1')
        self.assertEqual(error.exception.code,'PRIOR_ROUND_OBSERVATION')

    def test_b13_restart_json_csv_preserve_input_and_origin_events(self):
        ledger,first=split_example(8)
        ledger.deal('玩家1','10',hand_id=first)
        original=build_input(ledger,'玩家1')
        events=canonical(ledger.to_list())
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            controller=SessionController(path/'restart.db')
            controller.store.save_ledger(ledger)
            controller.close()
            restored=SessionController.recover(path/'restart.db',ledger.session_id)
            try:
                self.assertEqual(restored.analysis_input('玩家1'),original)
            finally:
                restored.close()
            for export,load,extension in ((export_json,import_json,'json'),(export_csv,import_csv,'csv')):
                destination=path/('round.'+extension)
                export(ledger,destination)
                imported=load(destination)
                self.assertEqual(canonical(imported.to_list()),events)
                self.assertEqual(build_input(imported,'玩家1'),original)


class TestSplitTk(unittest.TestCase):
    setUp=ui_fixture.TestAnalysisUI.setUp
    close=ui_fixture.TestAnalysisUI.close
    wait_result=ui_fixture.TestAnalysisUI.wait_result

    def start_split(self,pair='8'):
        self.app.act_research_template(split=True)
        self.app.act_new_shoe();self.app.act_new_round()
        self.app.var_target.set('庄家');self.app.refresh_all()
        self.app.act_card('6');self.app.act_hidden_card()
        self.app.var_target.set('玩家1');self.app.refresh_all()
        self.app.act_card(pair);self.app.act_card(pair)
        self.assertEqual(self.app.var_hand.get(),'（按顺序行动手）')
        return self.app.analysis_panel

    def compute(self,panel):
        panel.compute_button.invoke()
        result=self.wait_result()
        self.assertEqual(result['status'],'available',result['reason'])
        if os.environ.get('HAKIMI_OFFLINE_REQUIRED')=='1':
            self.assertTrue(result['worker_network_guard_active'])
        self.assertIsNotNone(panel.saved,panel.persistence.get())
        return result

    def test_b01_b02_b03_actual_split_record_update_and_save(self):
        panel=self.start_split()
        before=self.compute(panel)
        self.assertIn('合计净收益分布',panel.text.get('1.0','end'))
        self.app.btn_split.invoke()
        self.assertIsNone(panel.last_result)
        split=self.compute(panel)
        self.assertAlmostEqual(before['actions']['split']['ev'],split['actions']['deal']['ev'],delta=1e-10)
        self.app.act_card('10')
        first=self.compute(panel)
        self.assertEqual(first['input']['hands'][1]['ranks'],('8',))
        self.app.act_card('10')
        bust=self.compute(panel)
        self.assertEqual(bust['input']['active_hand_id'],bust['input']['hands'][1]['hand_id'])
        self.assertAlmostEqual(bust['actions']['deal']['hand_evs'][0],-1,delta=1e-10)
        self.app.act_card('9')
        second=self.compute(panel)
        self.assertEqual(set(second['actions']),{'stand','hit'})
        self.app.btn_stand.invoke()
        done=self.compute(panel)
        self.assertEqual(set(done['actions']),{'complete'})
        self.assertEqual(self.errors,[])

    def test_b09_failed_refresh_drops_old_split_values_and_future_publication(self):
        panel=self.start_split();self.app.btn_split.invoke()
        self.compute(panel)
        count=self.app.ctrl.store.event_count()
        with patch.object(self.app,'refresh_all',side_effect=RuntimeError('render failed')):
            self.app.act_card('10')
        self.assertEqual(self.app.ctrl.store.event_count(),count+1)
        self.assertIsNone(panel.last_result)
        self.assertNotIn('合计 EV',panel.text.get('1.0','end'))
        self.app.act_refresh()
        panel.calculate_current()
        old=calculate(self.app.ctrl.analysis_input('玩家1'),request_id=panel.request_id)
        with patch.object(self.app.ctrl,'_context_listeners',[]):
            self.app.ctrl.deal_shown('玩家1','10',hand_id=old['input']['active_hand_id'])
        with patch.object(panel.service,'poll',return_value=old):
            panel._poll()
        self.assertIsNone(panel.last_result)
        self.assertIsNone(panel.service.active)

    def test_b10_b14_history_recomputes_original_prefix_separately(self):
        panel=self.start_split();self.app.btn_split.invoke()
        self.compute(panel)
        saved=panel.saved
        original_path=self.app.ctrl.analysis_store.directory/(saved['snapshot_id']+'.json')
        original=original_path.read_bytes()
        historical=self.app.ctrl.recompute_input(saved)
        panel.start(historical,saved['snapshot_id'])
        self.app.act_card('10')
        result=self.wait_result()
        self.assertEqual(result['status'],'available',result['reason'])
        self.assertIn('原时点结果，不代表当前输入',panel.text.get('1.0','end'))
        self.assertEqual(result['input_digest'],historical.input_digest)
        self.assertEqual(panel.saved['recomputed_from'],saved['snapshot_id'])
        self.assertNotEqual(panel.saved['snapshot_id'],saved['snapshot_id'])
        self.assertEqual(original_path.read_bytes(),original)
        self.assertEqual(self.errors,[])

    def test_b12_save_failure_cancel_and_timeout_keep_events(self):
        panel=self.start_split();self.app.btn_split.invoke()
        count=self.app.ctrl.store.event_count()
        with patch.object(self.app.ctrl.analysis_store,'save',side_effect=OSError('synthetic disk full')):
            panel.calculate_current();result=self.wait_result()
        self.assertEqual(result['status'],'available')
        self.assertIn('快照未保存',panel.persistence.get())
        self.assertEqual(self.app.ctrl.store.event_count(),count)
        panel.retry_save();self.assertIsNotNone(panel.saved)
        panel.start(self.app.ctrl.analysis_input('玩家1'),budget_seconds=.001)
        result=self.wait_result()
        self.assertEqual(result['status'],'timeout')
        self.assertEqual(result['actions'],{})
        panel.auto_button.invoke();self.app.act_card('10')
        panel.cancel_button.invoke()
        with patch.object(panel.service,'start',wraps=panel.service.start) as start:
            deadline=time.perf_counter()+.4
            while time.perf_counter()<deadline:
                self.app.update();time.sleep(.01)
            start.assert_not_called()
        self.assertEqual(self.app.ctrl.store.event_count(),count+1)


if __name__ == '__main__':
    unittest.main()
