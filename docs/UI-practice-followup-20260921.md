# 简洁面板练习反馈（2026-09-21）

在16对庄家6的临时练习中，结算后录牌区仍提示“下一张给庄家”，顶部也没有展示揭示的9和补入的2。当前界面直接读取重放后的本轮状态：庄家由“6 暗牌 · 点数待确认”更新为“6 9 · 15点”，再更新为“6 9 2 · 17点”；结算、未结算结束、牌靴关闭后均显示对应结束提示。结算弹窗出现前完成刷新。撤销及恢复会话使用同一状态，不修改原事件。

顶部另显示实际牌靴副数及“当前发牌给：座位／第几手”。新建默认8副牌，已有牌靴及显式6／7副设置不改。Tab/Enter和反向键按参与顺序循环，空座跳过、分牌按具体手牌经过；Ctrl+1～7直达玩家，Ctrl+0直达庄家。导航不入账、不替玩家完成动作，也不切换分析对象。初始未录槽位被跳过时仍暂停自动轮转，原对齐要求保留。多人录牌不扩展多人EV。

历史分析仍只展示原输入中的庄家明牌，不混入后来揭示、补牌或纠错得到的牌面。数值计算、规则、账本格式和存储不改。

本地专项检查：59项摘要/面板/按键/发牌/K-F5检查通过，39项PR14可靠性、原分析UI及工作流检查通过；修正默认副数后的历史312张案例显式指定6副，保留其原断言。最后的结算弹窗刷新顺序另复跑面板套件。完整Windows远程CI须以本次提交为准。

Computer Use使用独立自建临时库核对原生窗口，覆盖隐藏底牌、揭示、补牌、结算与座位切换；截图和逐步状态保存在`.local-evidence/compact-followup-20260921`。原用户练习窗口和数据库保留。本次没有将这些检查当作长期真人体验验收。

复现：

```powershell
python -m unittest tests.test_compact_panel tests.test_decision_summary tests.test_manual_keymap tests.test_deal_entry tests.test_kf5_followthrough tests.test_pr14_reliability tests.test_analysis_ui tests.test_ui_workflow -q
python scripts/preview_compact_panel.py --output .local-evidence/唯一新目录 --scenario single --label 修复核对
python scripts/preview_compact_panel.py --output .local-evidence/另一个新目录 --scenario single --players 3 --label 多人快捷键核对
```
