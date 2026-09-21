# 庄家简便录入：自动未知底牌与点数分流

## 使用约定

在“录牌／纠错”里勾选“下轮简便暗牌”，确认固定美式流程的约定后，从下一轮生效：录完全部玩家初始明牌和庄家明牌，代表确认初始发牌已完成，包括已发但背面朝上的庄家底牌。默认不启用；有效恢复的已启用计划保留本轮设置。开启不会改变桌规，不代表已经检查非BJ，也不表示看见了底牌点数。

正常流程无需“暗牌 .”及“揭示”切换，这些控件在简便流程中隐藏。最后一张初始明牌输入完成，登记一张未知底牌，回到玩家；轮到庄家时提示“庄家开牌：请输入底牌点数”。直接输入8，揭示原底牌；再输入3，才新增补牌事件。原手动流程保留在关闭模式或人工核对状态。

明牌A／十点时保持等待实际检查：可按实际结果点“已检查，确认非BJ”，或者输入实际翻出的底牌。不会由玩家补牌、停牌、刷新或模式开启推导`PEEK_NEGATIVE`。实际揭示BJ可结算；撤销揭示恢复同一未知底牌及检查门禁。

## 状态与保存

- 启用要求已确认美式底牌、A/十点决策前检查、从完整新牌靴开始。中途加入、未知/其他规则不会借录入设置套用本模型。
- 本轮`ROUND_STARTED.evidence`记录`confirmed-us-initial-hole-v1`约定。自动未知底牌的`evidence`记录同一约定及触发的最后明牌事件ID，说明依据是已确认流程，没有具体底牌值。
- 最后明牌和未知底牌复用现有`save_ledger`的SQLite事务一起追加，两次插入任一失败均回滚；成功后只发布一次上下文。计划文件失败时两条已提交记录保留，转人工核对，恢复不会要求重录。
- 自动登记只在一次明确录牌命令中执行；不会在刷新、恢复、计算或检查缺失状态时补事件。只接受可信冻结队列中最后明牌，缺口、待核对牌、跳槽、手动纠偏、缺失/过期计划不自动补暗牌。
- 撤销沿用“最后一个有效事件”语义。紧接自动登记后一次撤销撤掉暗牌，显示原手动核对入口，不自动补回；再次撤销才撤掉最后明牌。撤销开牌输入只撤销`CARD_REVEALED`，原未知发牌身份及物理移除不变。
- 计划升级为v3，新增明确的`simple_hole`标志。完整合法v2计划按关闭简便模式读取；v1、字段不全、身份/摘要不符仍暂停。v3简便标志还必须能对应本轮账本中的启用依据，不能靠改计划文件启用。
- 庄家点数自动分流只在已验证的庄家阶段/等待检查阶段，当前手牌存在唯一正常待揭示底牌时进行。多未知牌、非正常未知牌、提前开牌或不可信位置回人工选择，不随意取第一张未知牌。

## 验收对应

| 目标要求 | 证据入口 |
| --- | --- |
| 初始明牌完成后恰好一张未知底牌，不按暗牌键 | `test_normal_initial_has_exactly_one_auditable_unknown_hole_without_dot`、`test_visible_card_buttons_follow_the_same_confirmed_initial_command`、三玩家用例 |
| 输入8揭示原事件，输入3才新增一张 | `test_dealer_direct_rank_reveals_then_new_rank_deals_and_settles`：八副物理待发数412→412→411 |
| 连点、刷新、重启不重复生成/揭示 | 重复点数、物理键长按、初始恢复及揭示后重启用例 |
| 撤销自动暗牌不会刷新重建 | `test_undo_auto_hole_does_not_readd_on_refresh_or_restart` |
| 撤销揭示恢复同一未知底牌 | `test_undo_reveal_restores_same_unknown_physical_card` |
| A/十点没有确认非BJ就不发布假定排除BJ的结果 | A/T门禁及实际BJ用例；没有自动`PEEK_NEGATIVE` |
| 底牌未展示保留未知移除，历史不知道后来牌面 | `test_unshown_hole_stays_removed_and_history_does_not_learn_later_rank` |
| 新旧录入的概率、EV、牌靴一致 | 明牌6、A、T同条件数值比较；原条件化例子0.5与2/3保持 |
| 事务、通知、计划失败与恢复 | 第二次插入失败回滚、通知失败不重录、计划失败保留双事件、缺失计划及v2/v3恢复用例 |
| 模式约定、人工歧义与旧流程保留 | 默认关闭/拒绝启用控件用例；未知桌规/中途加入、跳槽、多未知与提前开牌用例；原回归套件 |

完整本地和远程结果以本次最终提交日志为准；原生截图/逐步状态使用新的自建临时数据库，保存在`.local-evidence/simple-hole-20260922`。数学公式、核心桌规、SQLite表结构、单手/逐玩家条件模型均不变。PR #15继续供审查，main不合并，原数据库与验收材料保留。

```powershell
python -m unittest tests.test_simple_hole_entry -v
python -m unittest discover -s tests -q
python scripts/preview_compact_panel.py --output .local-evidence/唯一新目录 --scenario single --simple-hole --before-last-card --label 简便暗牌核对
python scripts/preview_compact_panel.py --output .local-evidence/另一新目录 --scenario single --simple-hole --up-rank A --label 检查门禁核对
```
