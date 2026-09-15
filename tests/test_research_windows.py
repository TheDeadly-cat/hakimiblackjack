"""Current-hand vs pre-deal windows, and win-rate vs net EV."""
import unittest

from blackjack_lab.analysis.contracts import PENDING
from blackjack_lab.analysis.research_windows import (
    WINDOW_CURRENT_HAND, WINDOW_PRE_DEAL, build_predeal_input,
    hypothetical_winrate_not_equal_to_ev, knowledge_revision_token, net_ev, net_outcome_summary,
    result_heading, source_mode_from_information, window_kind_for_current_analysis,
)


class ResearchWindowTest(unittest.TestCase):
    def test_current_hand_heading_is_not_predeal(self):
        self.assertEqual(WINDOW_CURRENT_HAND, window_kind_for_current_analysis())
        self.assertNotEqual(WINDOW_CURRENT_HAND, WINDOW_PRE_DEAL)
        self.assertTrue(result_heading().startswith("当前"))
        self.assertIn("截至已确认记录", result_heading(live_applicable=False))
        self.assertTrue(result_heading(historical=True).startswith("历史分析"))
        self.assertIn("录像回放", result_heading(applicability_kind="replay"))
        self.assertIn("截至人工确认记录", result_heading(applicability_kind="manual_asof"))
        self.assertTrue(result_heading(applicability_kind="synthetic").startswith("合成研究"))

    def test_information_source_is_not_a_live_catch(self):
        self.assertEqual("synthetic-composition",
                         source_mode_from_information({"source": "explicit-composition"}))
        self.assertEqual("ledger-prefix",
                         source_mode_from_information('{"source":"ledger-prefix"}'))
        self.assertEqual("unknown", source_mode_from_information(None))
        self.assertEqual("unknown", source_mode_from_information("{"))
        self.assertIsNone(knowledge_revision_token(None))
        self.assertEqual(64, len(knowledge_revision_token(("gen", "live", 0, False, 0, 0, 1))))

    def test_predeal_empty_call_is_missing_not_a_retitled_current_hand(self):
        with self.assertRaises(Exception) as caught:
            build_predeal_input()
        error = caught.exception
        self.assertEqual(PENDING, error.status)
        self.assertEqual("PREDEAL_INPUT_MISSING", error.code)
        self.assertNotEqual("PREDEAL_UNSUPPORTED", error.code)

    def test_win_rate_can_be_worse_while_net_ev_is_positive(self):
        dist, parts, ev = hypothetical_winrate_not_equal_to_ev()
        self.assertAlmostEqual(parts["win"], 0.45)
        self.assertAlmostEqual(parts["lose"], 0.47)
        self.assertAlmostEqual(parts["push"], 0.08)
        self.assertLess(parts["win"], parts["lose"])
        self.assertAlmostEqual(ev, 0.005)
        self.assertAlmostEqual(net_ev(dist), 0.005)
        self.assertAlmostEqual(sum(net_outcome_summary(dist).values()), 1.0)


    def test_capability_matrix_keeps_full_shoe_opening_experimental(self):
        from blackjack_lab.core.rules import CAPABILITY_MATRIX, EXPERIMENTAL, UNSUPPORTED
        opening = CAPABILITY_MATRIX["下一轮开局天然BJ概率/开局优势"]
        self.assertEqual(EXPERIMENTAL, opening[0])
        self.assertIn("整靴开局仍拒绝", opening[1])
        scan = CAPABILITY_MATRIX["整靴发牌前窗口扫描"]
        self.assertEqual(EXPERIMENTAL, scan[0])
        self.assertIn("零窗口", scan[1])
        self.assertIn("实现路径", scan[1])
        self.assertEqual(UNSUPPORTED, CAPABILITY_MATRIX["用户全屏浏览器捕获与热键验收"][0])
        self.assertEqual(UNSUPPORTED, CAPABILITY_MATRIX["操作者对照效率实验"][0])
        self.assertEqual(UNSUPPORTED, CAPABILITY_MATRIX["真实目标桌规则档案"][0])
        self.assertEqual(UNSUPPORTED, CAPABILITY_MATRIX["真实材料未勾选验收包"][0])
        self.assertEqual(UNSUPPORTED, CAPABILITY_MATRIX["发布冻结"][0])
        self.assertEqual(EXPERIMENTAL, CAPABILITY_MATRIX["回溯插入漏牌的原子修复"][0])
        self.assertIn("历史前缀", CAPABILITY_MATRIX["回溯插入漏牌的原子修复"][1])
        self.assertEqual(EXPERIMENTAL, CAPABILITY_MATRIX["本地牌面识别/屏幕捕获"][0])
        rounds = CAPABILITY_MATRIX["前三/六轮消耗对照"]
        self.assertEqual(EXPERIMENTAL, rounds[0])
        self.assertIn("独立样本", rounds[1])
        interval = CAPABILITY_MATRIX["未知组成区间研究"]
        self.assertEqual(EXPERIMENTAL, interval[0])
        self.assertIn("平均牌靴", interval[1])
        self.assertIn("调用方", interval[1])

    def test_surrender_token_is_explicit(self):
        from blackjack_lab.analysis.research_windows import parse_surrender_token
        self.assertIsNone(parse_surrender_token("none"))
        self.assertIsNone(parse_surrender_token("不支持"))
        self.assertEqual("late", parse_surrender_token("late"))
        with self.assertRaises(ValueError):
            parse_surrender_token("early")
        with self.assertRaises(ValueError):
            parse_surrender_token(None)
        with self.assertRaises(ValueError):
            parse_surrender_token("")

    def test_research_template_is_not_a_platform_table(self):
        from blackjack_lab.analysis.contracts import research_rules
        rules = research_rules(6)
        self.assertIn("不代表平台", rules.rule_source)
        self.assertIn("研究", rules.rule_source)
