"""P4 收尾补证 T2：直接覆盖 EventLedger.replay() 新增校验入口的永久回归测试。

背景（cbc8011，blackjack_lab/ledger/ledger.py）：
原实现在 replay() 内对每个事件执行 `Event.from_dict(ev.to_dict())` 往返校验；
优化后改为 `observed_at` 显式检查 + 同一实例的 `ev.__post_init__()`，
并把 UNDO 候选从"每条 UNDO 重建全量列表"改为增量维护 `undoable`。

为什么需要本文件：
既有 tests/test_ledger.py 与 tests/test_analysis_prerequisites.py 覆盖的拒绝路径
都发生在**导入入口**（Event(...) 构造即抛、EventLedger.from_list 删字典字段即抛），
事件根本无法进入账本，因此永远不会执行到 replay() 里的新增 guard。
若将来有人删除 `if ev.observed_at is None: raise ...` 或改坏 `undoable` 增量逻辑，
既有测试不会失败。本文件通过"先经正常入口建立合法账本，再修改已存在事件对象、
或绕过 append 预演直接追加控制事件，然后调用 replay()"来直接命中新入口。

约束：本文件只做行为断言，不逐行镜像实现；不放宽任何既有校验，
也不以"禁用 replay"的方式换取坏输入被拒绝。
"""

import copy
import unittest

from blackjack_lab.core.rules import RuleProfile
from blackjack_lab.ledger.events import (
    CARD_DEALT,
    CORRECTION,
    Event,
    SESSION_STARTED,
    SHOE_CREATED,
    UNDO,
    new_event_id,
)
from blackjack_lab.ledger.ledger import EventLedger, LedgerError


def _rules(n=6, **kw):
    return RuleProfile(n_decks=n, split_match="same_value",
                       double_after_split=True, surrender="early", **kw)


def _fresh(n=6):
    """经正常入口建立合法账本：会话 → 牌靴 → 轮次。"""
    led = EventLedger("perf4-guard-session")
    led.start_session()
    led.create_shoe(_rules(n))
    led.start_round()
    return led


def _attach_raw(led, event):
    """把事件挂到账本但**绕过 append 的预演校验**，使随后的 replay() 成为首个校验点。

    append() 会先 deepcopy 一份 candidate 并调用 _validate_append()（内部即 replay），
    失败则不发布状态；那样测到的是 append 入口而不是 replay 入口。
    这里直接写入 events/_ids/_seq，并复刻 append() 的上下文同步规则
    （含 SHOE_CREATED 的特判），模拟"持久化状态已损坏后被重新加载"的情形。
    """
    event.seq = led._seq + 1
    event.session_id = led.session_id
    if event.etype == SHOE_CREATED:
        event.shoe_id = event.payload.get("shoe_id")
        event.round_id = None
    else:
        event.shoe_id = event.shoe_id or led._current_shoe_id
        event.round_id = event.round_id or led._current_round_id
    led.events.append(event)
    led._ids.add(event.event_id)
    led._seq = event.seq
    return event


def _live(led, etype, index=0):
    """取得账本内**当前有效**的事件对象。

    关键陷阱：append() 内部执行 copy.deepcopy(self)，因此除最后一次追加返回的
    对象外，此前所有由 deal()/correct()/undo_last() 返回的引用都已指向旧副本，
    修改它们不会影响账本、也不会被 replay() 观察到。要命中 replay() 里的校验，
    必须从 led.events 取活对象。
    """
    matched = [e for e in led.events if e.etype == etype]
    return matched[index]


class TestReplayObservedAtGuard(unittest.TestCase):
    """直接命中 replay() 中新增的 observed_at 显式拒绝。"""

    def test_observed_at_none_rejected_without_silent_repair(self):
        led = _fresh(6)
        led.deal("玩家1", "A")
        bad = _live(led, CARD_DEALT)              # 活对象，而非 deal() 返回的陈旧引用
        self.assertIsNotNone(bad.observed_at)     # 前置：正常入口下该字段有值
        event_time_before = bad.event_time
        observed_before = bad.observed_at

        bad.observed_at = None                    # 构造后修改，不经导入校验
        with self.assertRaises(ValueError) as ctx:
            led.replay()
        self.assertEqual(str(ctx.exception), "导入事件缺少观察时间")

        # 关键：不得静默用 event_time 补值，也不得改写坏事件的其他字段。
        # 若 guard 被删除，__post_init__ 会把 observed_at 替换成 event_time，
        # 这条断言即失败——这正是本测试的长期保护价值。
        self.assertIsNone(bad.observed_at)
        self.assertEqual(bad.event_time, event_time_before)
        self.assertNotEqual(bad.observed_at, observed_before)

    def test_mutating_stale_reference_does_not_pass_as_replay_coverage(self):
        """反向自检：修改 deal() 返回的陈旧引用不会触发 guard。

        若没有这条，"guard 被删除"与"我改错了对象"两种情况都会表现为未抛异常，
        无法区分。它锁定了 append() 的 deepcopy 语义，也说明本文件其余用例
        必须使用 _live()，否则测试是假的。
        """
        led = _fresh(6)
        first = led.deal("玩家1", "A")
        led.deal("玩家1", "7")                    # 第二次 append 使 first 变为陈旧引用

        self.assertFalse(any(e is first for e in led.events))
        first.observed_at = None
        led.replay()                              # 不抛异常：改的是账本外的副本
        self.assertIsNone(first.observed_at)
        self.assertTrue(all(e.observed_at is not None for e in led.events))

    def test_guard_does_not_disable_replay_for_healthy_ledger(self):
        """正常未损坏账本仍必须完整通过；guard 不得以拒绝一切输入的方式生效。"""
        led = _fresh(6)
        led.deal("玩家1", "A")
        led.deal("庄家", None, hidden=True)      # 规则暗牌不携带牌面
        led.deal("玩家1", "7")
        result = led.replay()

        self.assertEqual(len(result.segments), 1)
        seg = result.current
        self.assertIsNotNone(seg)
        self.assertEqual(seg.shoe.exact_out["A"], 1)
        self.assertEqual(seg.shoe.exact_out["7"], 1)
        self.assertEqual(sorted(seg.table.players["玩家1"].hands[0].ranks), ["7", "A"])
        # 全部事件都带有观察时间，未被 guard 拦下
        self.assertTrue(all(e.observed_at is not None for e in led.events))


class TestReplayPostConstructionMutations(unittest.TestCase):
    """参数化：对象构造后修改字段，再直接 replay；断言走的是 replay 入口。"""

    CASES = (
        # (用例名, 变异函数, 期望异常类型, 期望消息)
        ("seq_is_bool", lambda e: setattr(e, "seq", True),
         ValueError, "事件序号必须为整数"),
        ("empty_source", lambda e: setattr(e, "source", ""),
         ValueError, "事件source不能为空"),
        ("blank_source", lambda e: setattr(e, "source", "   "),
         ValueError, "事件source不能为空"),
        ("unknown_etype", lambda e: setattr(e, "etype", "BOGUS_ETYPE"),
         ValueError, "未知事件类型: BOGUS_ETYPE"),
        ("payload_unknown_field",
         lambda e: e.payload.__setitem__("unexpected_field", 1),
         ValueError, "CARD_DEALT事件负载缺少字段或含未支持字段"),
        ("payload_missing_required_field", lambda e: e.payload.pop("rank"),
         ValueError, "CARD_DEALT事件负载缺少字段或含未支持字段"),
        ("payload_not_dict", lambda e: setattr(e, "payload", []),
         ValueError, "事件payload必须为对象"),
        ("empty_event_id", lambda e: setattr(e, "event_id", ""),
         ValueError, "事件event_id不能为空"),
        ("nan_event_time", lambda e: setattr(e, "event_time", float("nan")),
         ValueError, "事件event_time必须为有限时间戳"),
        ("non_text_shoe_id", lambda e: setattr(e, "shoe_id", 123),
         ValueError, "事件shoe_id必须为文本或空"),
        ("bad_confirm_status", lambda e: setattr(e, "confirm_status", "guess"),
         ValueError, "未知事件确认状态"),
    )

    def test_mutations_rejected_by_replay(self):
        for name, mutate, exc, message in self.CASES:
            with self.subTest(case=name):
                led = _fresh(6)
                target = led.deal("玩家1", "A")
                self.assertIsNotNone(led.replay().current)   # 前置：变异前账本合法

                mutate(target)
                with self.assertRaises(exc) as ctx:
                    led.replay()
                self.assertEqual(str(ctx.exception), message)

    def test_mutations_reach_replay_not_import_entry(self):
        """证明上述用例确实经过 replay：变异对象已在账本内、seq 已由账本分配。"""
        led = _fresh(6)
        target = led.deal("玩家1", "A")
        assigned_seq = target.seq
        self.assertGreater(assigned_seq, 0)
        self.assertIn(target.event_id, led._ids)

        target.source = ""
        # 变异发生在对象已入账之后；from_list/Event 构造入口无从参与
        with self.assertRaises(ValueError):
            led.replay()
        self.assertEqual(target.seq, assigned_seq)
        self.assertEqual(target.source, "")


class TestReplayUndoableIncremental(unittest.TestCase):
    """直接覆盖 UNDO 候选增量维护（undoable）替代全量重建后的语义。"""

    def test_correction_then_undo_correction_then_reverse_undo_restores_state(self):
        """纠错 → 撤回纠错 → 继续逆序撤回，状态必须真正回到起点。

        undo_last 选取目标时跳过 UNDO 与 SESSION_STARTED，但 CORRECTION 属可撤销候选，
        因此"撤回一条纠错"是合法路径：撤回后 corrections 不再生效、原牌面恢复入账。
        这条路径完全依赖 undoable 中 CORRECTION 的追加与弹出顺序。
        """
        baseline_remaining = _fresh(6).replay().current.shoe.remaining

        led = _fresh(6)
        first = led.deal("玩家1", "K")          # 误记为 K
        second = led.deal("玩家1", "7")
        correction = led.correct(first.event_id, {"rank": "Q"}, reason="看错牌面")
        self.assertEqual(correction.etype, CORRECTION)

        seg = led.replay().current
        self.assertEqual(seg.shoe.exact_out["Q"], 1)   # 纠错已生效
        self.assertEqual(seg.shoe.exact_out["K"], 0)

        # 1) 撤回这条纠错：修正失效，K 恢复入账
        led.undo_last()
        seg2 = led.replay().current
        self.assertEqual(seg2.shoe.exact_out["K"], 1)
        self.assertEqual(seg2.shoe.exact_out["Q"], 0)
        self.assertEqual(seg2.shoe.exact_out["7"], 1)
        self.assertTrue(led.is_voided(correction.event_id))

        # 2) 继续逆序撤回 7，再撤回 K
        led.undo_last()
        seg3 = led.replay().current
        self.assertEqual(seg3.shoe.exact_out["7"], 0)
        self.assertEqual(seg3.shoe.exact_out["K"], 1)

        led.undo_last()
        seg4 = led.replay().current
        self.assertEqual(seg4.shoe.exact_out["K"], 0)
        self.assertEqual(seg4.shoe.remaining, baseline_remaining)
        # 实测结构：所有发牌都被撤销后，座位不再持有任何手牌（hands 为空列表），
        # 而不是保留一个空手牌对象。
        self.assertEqual(seg4.table.players["玩家1"].hands, [])
        self.assertEqual(
            sum(seg4.shoe.exact_out.values()), 0,
            "全部撤回后不应有任何牌被记为已发出")

    def test_undo_candidates_exclude_control_events(self):
        """UNDO 与 SESSION_STARTED 不得成为可撤销候选：撤销它们必须被拒。"""
        led = _fresh(6)
        led.deal("玩家1", "A")
        undo_event = led.undo_last()

        with self.subTest(target="undo_of_undo"):
            probe = copy.deepcopy(led)
            _attach_raw(probe, Event(UNDO, {
                "target_event_id": undo_event.event_id,
                "reason": "", "target_etype": UNDO}))
            with self.assertRaises(LedgerError) as ctx:
                probe.replay()
            self.assertEqual(str(ctx.exception), "控制事件必须引用此前可撤销/纠错的事件")

        with self.subTest(target="undo_of_session_started"):
            session_event = next(e for e in led.events if e.etype == SESSION_STARTED)
            probe = copy.deepcopy(led)
            _attach_raw(probe, Event(UNDO, {
                "target_event_id": session_event.event_id,
                "reason": "", "target_etype": SESSION_STARTED}))
            with self.assertRaises(LedgerError) as ctx:
                probe.replay()
            self.assertEqual(str(ctx.exception), "控制事件必须引用此前可撤销/纠错的事件")

    def test_replay_rejects_skipping_last_effective_event(self):
        """跳过最后有效事件、撤销更早记录必须被拒（逆序撤回约束）。"""
        led = _fresh(6)
        first = led.deal("玩家1", "A")
        led.deal("玩家1", "K")               # K 才是最后有效事件

        _attach_raw(led, Event(UNDO, {
            "target_event_id": first.event_id,
            "reason": "", "target_etype": CARD_DEALT}))
        with self.assertRaises(LedgerError) as ctx:
            led.replay()
        self.assertEqual(str(ctx.exception), "只能逆序撤销最后有效事件，不能跳过后续记录")

    def test_replay_rejects_reference_to_already_voided_event(self):
        led = _fresh(6)
        led.deal("玩家1", "A")
        voided = led.deal("玩家1", "K")
        led.undo_last()
        self.assertTrue(led.is_voided(voided.event_id))

        _attach_raw(led, Event(UNDO, {
            "target_event_id": voided.event_id,
            "reason": "", "target_etype": CARD_DEALT}))
        with self.assertRaises(LedgerError) as ctx:
            led.replay()
        self.assertEqual(str(ctx.exception), "控制事件不可引用已撤销记录")

    def test_replay_rejects_unknown_target(self):
        led = _fresh(6)
        led.deal("玩家1", "A")
        _attach_raw(led, Event(UNDO, {
            "target_event_id": new_event_id(),
            "reason": "", "target_etype": CARD_DEALT}))
        with self.assertRaises(LedgerError) as ctx:
            led.replay()
        self.assertEqual(str(ctx.exception), "控制事件必须引用此前可撤销/纠错的事件")

    def test_replay_rejects_correction_of_voided_event(self):
        """已撤销事件不可再被纠错：corrections 映射不得复活已作废记录。"""
        led = _fresh(6)
        dealt = led.deal("玩家1", "K")
        led.undo_last()
        self.assertTrue(led.is_voided(dealt.event_id))

        _attach_raw(led, Event(CORRECTION, {
            "target_event_id": dealt.event_id, "payload_fix": {"rank": "Q"},
            "reason": "", "target_etype": CARD_DEALT}))
        # replay 中 CORRECTION 分支要求目标 etype 在 CORRECTABLE_FIELDS 内且未被撤销；
        # 已撤销目标仍会先通过 seen 检查，因此断言以实际抛出的 LedgerError 为准。
        with self.assertRaises(LedgerError):
            led.replay()

    def test_replay_through_seq_prefix_still_validates(self):
        """through_seq 前缀重放同样经过 guard（内部 deepcopy 后递归 replay）。"""
        led = _fresh(6)
        first = led.deal("玩家1", "A")
        led.deal("玩家1", "K")
        prefix_result = led.replay(through_seq=first.seq)
        self.assertEqual(prefix_result.current.shoe.exact_out["A"], 1)
        self.assertEqual(prefix_result.current.shoe.exact_out["K"], 0)

        live_first = _live(led, CARD_DEALT, 0)    # 第二次追加后 first 已陈旧
        self.assertIsNot(live_first, first)
        live_first.observed_at = None
        with self.assertRaises(ValueError) as ctx:
            led.replay(through_seq=first.seq)
        self.assertEqual(str(ctx.exception), "导入事件缺少观察时间")
        self.assertIsNone(live_first.observed_at)

    def test_replay_rejects_misplaced_session_started(self):
        led = _fresh(6)
        led.deal("玩家1", "A")
        _attach_raw(led, Event(SESSION_STARTED, {"note": "重复开会话"}))
        with self.assertRaises(LedgerError) as ctx:
            led.replay()
        self.assertEqual(str(ctx.exception), "会话开始事件只能位于首条")

    def test_replay_rejects_duplicate_shoe_identity(self):
        led = _fresh(6)
        led.end_round()
        existing_shoe_id = led.events[1].shoe_id
        _attach_raw(led, Event(SHOE_CREATED, {
            "shoe_id": existing_shoe_id, "n_decks": 6,
            "rules_snapshot": _rules(6).to_json()}))
        with self.assertRaises(LedgerError) as ctx:
            led.replay()
        self.assertEqual(str(ctx.exception), "新牌靴必须使用唯一身份")


class TestHealthyPathsUnaffected(unittest.TestCase):
    """guard 与增量 undoable 不得影响合法路径的既有结果。"""

    def test_repeated_replay_is_stable(self):
        led = _fresh(6)
        led.deal("玩家1", "5")
        led.deal("玩家1", "6")
        led.correct(led.events[-1].event_id, {"rank": "7"}, reason="看错")
        led.undo_last()
        led.deal("玩家1", "9")

        first = led.replay()
        second = led.replay()
        self.assertEqual(first.current.shoe.exact_out, second.current.shoe.exact_out)
        self.assertEqual(len(first.segments), len(second.segments))
        self.assertEqual([e.event_id for e in first.current.events],
                         [e.event_id for e in second.current.events])
        self.assertEqual(first.current.shoe.exact_out["9"], 1)
        self.assertEqual(first.current.shoe.exact_out["7"], 0)

    def test_idempotent_append_then_replay(self):
        led = _fresh(6)
        eid = new_event_id()
        e1 = led.deal("玩家1", "A", event_id=eid)
        e2 = led.deal("玩家1", "A", event_id=eid)
        self.assertIs(e1, e2)
        seg = led.replay().current
        self.assertEqual(len(seg.table.players["玩家1"].hands[0].cards), 1)

    def test_conflicting_event_id_rejected_at_append(self):
        led = _fresh(6)
        eid = new_event_id()
        led.deal("玩家1", "A", event_id=eid)
        with self.assertRaises(LedgerError):
            led.deal("玩家1", "K", event_id=eid)

    def test_undo_allows_same_track_reuse(self):
        led = _fresh(6)
        led.deal("玩家1", "A", track_id="phys-perf4-1")
        led.undo_last()
        led.deal("玩家1", "A", track_id="phys-perf4-1")
        seg = led.replay().current
        self.assertEqual(seg.shoe.exact_out["A"], 1)

    def test_no_undo_target_raises(self):
        led = EventLedger("perf4-empty")
        led.start_session()
        with self.assertRaises(LedgerError):
            led.undo_last()


if __name__ == "__main__":
    unittest.main()
