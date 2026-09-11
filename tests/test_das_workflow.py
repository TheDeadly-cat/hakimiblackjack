"""T7: DAS template recording, display, save and historical recompute."""
import os
import unittest

from blackjack_lab.analysis.contracts import InputUnavailable, research_rules
from blackjack_lab.analysis.information import build_input
from blackjack_lab.analysis.service import calculate
from blackjack_lab.analysis.split_contracts import (
    DAS_ENGINE, DAS_ENGINE_LEGACY, DAS_PROFILE, DAS_STRATEGY, DAS_STRATEGY_LEGACY,
    SPLIT_ENGINE, SPLIT_PROFILE, SplitAnalysisInput, das_research_rules, split_research_rules)
from blackjack_lab.core.table import ACTION_DOUBLE, ACTION_HIT, TableError
from blackjack_lab.ui.split_display import format_split_result
from tests import test_analysis_ui as ui_fixture
from tests.test_analysis_integration import example
from tests.test_split_workflow import split_example


def das_split_example(n=6, pair='8', up='6'):
    ledger=example(n,cards=(pair,pair),up=up,rules=das_research_rules(n))
    hand_id=build_input(ledger,'玩家1').hand_id
    ledger.player_action('玩家1',hand_id,'分牌')
    return ledger,hand_id


class TestDasWorkflow(unittest.TestCase):
    def test_das_template_uses_das_engine_and_keeps_b1_identity(self):
        das=build_input(example(cards=('8','8'),up='6',rules=das_research_rules()),'玩家1')
        self.assertEqual(das.engine_version,DAS_ENGINE)
        self.assertEqual(das.strategy_version,DAS_STRATEGY)
        self.assertIn('double',das.legal_actions)
        self.assertIn('split',das.legal_actions)
        b1=build_input(example(cards=('8','8'),up='6',rules=split_research_rules()),'玩家1')
        self.assertEqual(b1.engine_version,SPLIT_ENGINE)
        self.assertNotEqual(DAS_PROFILE,SPLIT_PROFILE)
        four=example(cards=('8','8'),up='6',rules=research_rules())
        hid=build_input(four,'玩家1').hand_id
        four.player_action('玩家1',hid,'分牌')
        with self.assertRaises(InputUnavailable) as error:
            build_input(four,'玩家1')
        self.assertEqual(error.exception.code,'SPLIT_HAND_UNSUPPORTED')

    def test_split_double_unique_card_then_second_hand(self):
        ledger,first=das_split_example()
        ledger.deal('玩家1','3',hand_id=first)
        snapshot=build_input(ledger,'玩家1',first)
        self.assertEqual(snapshot.legal_actions,('stand','hit','double'))
        self.assertEqual(snapshot.hands[0].bet_units,1)
        result=calculate(snapshot)
        self.assertEqual(result['status'],'available',result['reason'])
        self.assertEqual(result['engine_version'],DAS_ENGINE)
        self.assertEqual(result['current_investment'],2)
        double=result['actions']['double']
        self.assertEqual(double['additional_investment'],1)
        self.assertEqual(double['possible_future_additional'],1)
        self.assertEqual(double['max_final_investment'],4)
        text=format_split_result(result)
        self.assertIn('立刻追加',text)
        self.assertIn('此后可能再追加至多',text)
        self.assertIn('非A允许DAS',text)
        self.assertNotIn('无DAS',text)
        self.assertNotIn('总投入',text)
        self.assertIn('注额1',text)

        ledger.player_action('玩家1',first,'加倍')
        waiting=build_input(ledger,'玩家1',first)
        self.assertEqual(waiting.legal_actions,('deal',))
        self.assertEqual(waiting.hands[0].bet_units,2)
        self.assertTrue(waiting.hands[0].forced_draw)
        self.assertFalse(waiting.hands[0].closed)
        pending=calculate(waiting)
        self.assertEqual(pending['status'],'available',pending['reason'])
        self.assertEqual(pending['current_investment'],3)
        self.assertEqual(set(pending['actions']),{'deal'})
        self.assertEqual(pending['actions']['deal']['additional_investment'],0)

        ledger.deal('玩家1','6',hand_id=first)
        after=build_input(ledger,'玩家1')
        self.assertTrue(after.hands[0].closed)
        self.assertEqual(after.hands[0].bet_units,2)
        self.assertEqual(after.active_index,1)
        self.assertEqual(after.legal_actions,('deal',))
        second=after.hands[1].hand_id
        ledger.deal('玩家1','9',hand_id=second)
        ready=build_input(ledger,'玩家1')
        self.assertEqual(ready.legal_actions,('stand','hit','double'))
        done=calculate(ready)
        self.assertEqual(done['current_investment'],3)
        self.assertEqual(done['actions']['double']['possible_future_additional'],0)
        ledger.player_action('玩家1',second,'停牌')
        complete=calculate(build_input(ledger,'玩家1'))
        self.assertEqual(set(complete['actions']),{'complete'})
        self.assertEqual(complete['current_investment'],3)

    def test_b1_split_still_refuses_das_and_keeps_old_copy(self):
        ledger,first=split_example()
        ledger.deal('玩家1','3',hand_id=first)
        snapshot=build_input(ledger,'玩家1',first)
        self.assertEqual(snapshot.engine_version,SPLIT_ENGINE)
        self.assertNotIn('double',snapshot.legal_actions)
        with self.assertRaises(TableError):
            ledger.player_action('玩家1',first,'加倍')
        text=format_split_result(calculate(snapshot))
        self.assertIn('无DAS',text)
        self.assertIn('总投入',text)
        self.assertNotIn('立刻追加',text)

    def test_split_aces_cannot_das(self):
        ledger,first=das_split_example(pair='A')
        ledger.deal('玩家1','9',hand_id=first)
        snapshot=build_input(ledger,'玩家1')
        self.assertTrue(snapshot.hands[0].closed)
        self.assertEqual(snapshot.legal_actions,('deal',))
        self.assertNotIn('double',snapshot.legal_actions)
        before=build_input(example(cards=('A','A'),up='6',rules=das_research_rules()),'玩家1')
        split=calculate(before)
        self.assertEqual(split['status'],'available',split['reason'])
        self.assertEqual(split['actions']['split']['possible_future_additional'],0)
        table=ledger.replay().current.table
        with self.assertRaises(TableError):
            table.apply_action('玩家1',first,ACTION_DOUBLE)

    def test_legacy_das_identity_validates_but_cannot_recompute(self):
        snapshot=build_input(example(cards=('8','8'),up='6',rules=das_research_rules()),'玩家1')
        data=snapshot.to_dict()
        data['engine_version']=DAS_ENGINE_LEGACY
        data['strategy_version']=DAS_STRATEGY_LEGACY
        old=SplitAnalysisInput.from_dict(data)
        old.validate()
        failed=calculate(old)
        self.assertEqual(failed['status'],'failed')
        self.assertIn('已升级',failed['reason'])

    def test_hit_pending_deal_does_not_keep_current_das(self):
        ledger,first=das_split_example()
        ledger.deal('玩家1','3',hand_id=first)
        ledger.player_action('玩家1',first,ACTION_HIT)
        snapshot=build_input(ledger,'玩家1')
        self.assertEqual(snapshot.legal_actions,('deal',))
        self.assertTrue(snapshot.hands[0].forced_draw)
        self.assertEqual(snapshot.hands[0].bet_units,1)
        result=calculate(snapshot)
        self.assertEqual(result['status'],'available',result['reason'])
        self.assertEqual(set(result['actions']),{'deal'})
        self.assertEqual(result['actions']['deal']['possible_future_additional'],1)


class TestDasTk(unittest.TestCase):
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
        return self.app.analysis_panel

    def compute(self,panel):
        panel.compute_button.invoke()
        result=self.wait_result()
        self.assertEqual(result['status'],'available',result['reason'])
        if os.environ.get('HAKIMI_OFFLINE_REQUIRED')=='1':
            self.assertTrue(result['worker_network_guard_active'])
        self.assertIsNotNone(panel.saved,panel.persistence.get())
        return result
    def start_das(self,pair='8'):
        self.app.act_research_template(das=True)
        self.app.act_new_shoe();self.app.act_new_round()
        self.app.var_target.set('庄家');self.app.refresh_all()
        self.app.act_card('6');self.app.act_hidden_card()
        self.app.var_target.set('玩家1');self.app.refresh_all()
        self.app.act_card(pair);self.app.act_card(pair)
        self.assertEqual(self.app.var_hand.get(),'（按顺序行动手）')
        self.assertIn('两手DAS模型',self.app.var_topinfo.get())
        return self.app.analysis_panel

    def test_template_selection_does_not_rewrite_current_b1_shoe(self):
        self.start_split()
        self.assertEqual(self.app.ctrl.current_rules().profile_id,SPLIT_PROFILE)
        self.app.act_research_template(das=True)
        self.assertEqual(self.app.ctrl.current_rules().profile_id,SPLIT_PROFILE)
        self.assertIn('当前规则快照不变',self.app.var_status.get())

    def test_das_split_double_save_and_history_recompute(self):
        panel=self.start_das()
        before=self.compute(panel)
        self.assertEqual(before['engine_version'],DAS_ENGINE)
        self.assertEqual(before['actions']['split']['possible_future_additional'],2)
        self.assertIn('立刻追加',panel.text.get('1.0','end'))
        self.app.btn_split.invoke()
        self.compute(panel)
        self.app.act_card('3')
        two=self.compute(panel)
        self.assertEqual(set(two['actions']),{'stand','hit','double'})
        self.assertEqual(two['current_investment'],2)
        self.app.btn_double.invoke()
        waiting=self.compute(panel)
        self.assertEqual(set(waiting['actions']),{'deal'})
        self.assertEqual(waiting['current_investment'],3)
        self.assertEqual(waiting['input']['hands'][0]['bet_units'],2)
        self.app.act_card('6')
        second=self.compute(panel)
        self.assertEqual(second['input']['active_hand_id'],second['input']['hands'][1]['hand_id'])
        self.app.act_card('9')
        ready=self.compute(panel)
        self.assertIn('double',ready['actions'])
        self.app.btn_stand.invoke()
        done=self.compute(panel)
        self.assertEqual(set(done['actions']),{'complete'})
        saved=panel.saved
        original_path=self.app.ctrl.analysis_store.directory/(saved['snapshot_id']+'.json')
        original=original_path.read_bytes()
        historical=self.app.ctrl.recompute_input(saved)
        self.assertEqual(historical.engine_version,DAS_ENGINE)
        panel.start(historical,saved['snapshot_id'])
        result=self.wait_result()
        self.assertEqual(result['status'],'available',result['reason'])
        self.assertEqual(result['engine_version'],DAS_ENGINE)
        self.assertIn('原时点结果，不代表当前输入',panel.text.get('1.0','end'))
        self.assertEqual(panel.saved['recomputed_from'],saved['snapshot_id'])
        self.assertEqual(original_path.read_bytes(),original)
        self.assertEqual(self.errors,[])

    def test_b1_history_still_uses_b1_engine_after_das_template_exists(self):
        panel=self.start_split();self.app.btn_split.invoke()
        result=self.compute(panel)
        self.assertEqual(result['engine_version'],SPLIT_ENGINE)
        saved=panel.saved
        self.app.act_research_template(das=True)
        historical=self.app.ctrl.recompute_input(saved)
        self.assertEqual(historical.engine_version,SPLIT_ENGINE)
        panel.start(historical,saved['snapshot_id'])
        recomputed=self.wait_result()
        self.assertEqual(recomputed['engine_version'],SPLIT_ENGINE)
        if os.environ.get('HAKIMI_OFFLINE_REQUIRED')=='1':
            self.assertTrue(recomputed['worker_network_guard_active'])
        self.assertEqual(self.errors,[])

    def test_hit_then_deal_does_not_reenable_das(self):
        panel=self.start_das()
        self.compute(panel)
        self.app.btn_split.invoke()
        self.compute(panel)
        self.app.act_card('3')
        ready=self.compute(panel)
        self.assertIn('hit',ready['actions'])
        hit_ev=ready['actions']['hit']['ev']
        self.app.act_action(ACTION_HIT)
        pending=self.compute(panel)
        self.assertEqual(set(pending['actions']),{'deal'})
        self.assertAlmostEqual(pending['actions']['deal']['ev'],hit_ev,delta=1e-10)
        self.assertEqual(pending['actions']['deal']['possible_future_additional'],1)
        self.app.act_card('6')
        after=self.compute(panel)
        self.assertNotIn('double',after['actions'])
        self.assertEqual(self.errors,[])


if __name__ == '__main__':
    unittest.main()
