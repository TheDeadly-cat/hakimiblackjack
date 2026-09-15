# M0a–M3 软件 + M4 未勾选验收包 + M5 冻结清单（已按要求推送，不是发布）

日期：2026-09-15。基准提交仍是 `3bcd627814aaa3792d4edc24e2ad737cfbbe727d`。本段不是发布声明，也不是研究版或桌面观察版完成。用户已要求推送到 GitHub；不合入 `main`。

## M0a：规则透传与统计语义

人工剩余 `[10,10,9,9,8,7]`：独立 oracle 720 排列，无投降 EV=`0`，晚投降 EV=`5/36`。完整 `build_predeal_input → calculate` 与之一致，无投降分布不含 `-0.5`。合成剩余必须显式给出规则，缺规则不再套用晚投降研究模板；`solve_predeal_counts` / `evaluate_predeal` / `evaluate_interval` 缺投降字段拒绝，不能默认混入 `-0.5`。保存/加载快照必须带投降规则与对应动作集；去掉投降字段不能再当合法结果（缺字段不能用 `.get` 当成无投降）。区间诊断包络与 3/6 轮发牌前评估必须显式传入投降规则；缺字段摘要不能按研究模板补成晚投降。

分析面板合成剩余：未锁定牌靴时跟会话投降选项（默认「不支持」→ EV=`0`），不能因 `current_rules()` 为空而静默套用晚投降研究模板。已锁定牌靴跟牌靴快照，不跟事后改的下拉框。当前手摘要也写入实际投降规则；无投降结果不得出现「晚投降」。`parse_surrender_token(None)` 拒绝，不再把缺字段写成 late。

`zero_window` 只在完整已声明评估且无正 EV 时为真。全六副重洗全部 unsupported 现为「评估不完整」，不再记成已证明无窗口。混淆矩阵：真值不可评不定误报/共同阴性；真值正且观察缺失记 `missed_unavailable`。`evaluation_scope` 同时给出大纲字段：`evaluated_count`、`unassessable_count`、`no_positive_detected`；不完整时 `verified_no_positive_over_declared_domain` 为 null，不能写成已证实无窗口。混淆矩阵另计 `missed_due_to_abstention`（同 `missed_unavailable`）与 `classified_false_negative`。

整靴窗口 / 独立牌靴 / 策略耗牌对照 / 3/6 轮 / 合成录牌误差 / 未使用留出：Python 入口缺投降字段拒绝，不能默认晚投降。`None` 仍是无投降。六张牌 STAT-02 真值正例必须显式 `surrender="late"`，否则 EV=`0` 不能当正窗口。结果摘要写入 `surrender`。研究扫描 CLI `--surrender none|late` 为必填；省略旗帜不能静默补成晚投降。对照实验 CLI 合成缺省 `none`，不是研究扫描那条路径。冻结π共同区间 / 所列 maxmin / 可行组成区间 / `exact_common_policy_ev` 同样缺字段拒绝，不能把省略写成无投降。合成对照实验必须在映射里给出投降规则；缺字段 `SURRENDER_REQUIRED`。CLI 合成缺省 `none`（对齐主窗口「不支持」），配置文件已有 `late` 不被覆盖。对照实验窗口合成启动跟会话规则，无投降时合法动作不含投降；历史回放仍只用账本当时牌靴规则。

## M0b：观察当前性与发牌前异步身份

来源新鲜度与知识身份分开：帧时钟不改 `knowledge_identity`。`live_table_applicable()` 仅在 live 来源且知识干净时为真；手动为「截至人工确认记录」，回放为「录像回放 · 不适用于当前真实牌桌」，合成组成为「合成研究」。`display_policy()` 写明各状态能否计算/展示 as-of、能否贴 live 标签、能否 timely 声称；撤回/跳过不能自动对账；未定区域单独挡实时标签。发牌前组成 A→B / 非法 / 清空会撤销旧请求的当前资格，数字可保留但不再标当前。计算中出现新疑似牌后，即使队列被清空也不能把旧结果写回「当前」。发布记录写入请求当时的 `knowledge_revision`；历史复算不套当前观察身份，也不把 `live_applicable` 设成真。发牌前历史复算期间改当前合成组成，仍只显示「历史分析」，`timely_live_claim` 为假。当前手结果带 `ledger-prefix` 来源、账本前缀与时点，求解器 `timely` 仍为假。

## M1：小牌靴参考与输入严格性

`counts_from_values` / `build_predeal_input` / 留出剩余组成先拒绝 bool/float/NaN/inf，不再 `int()` 截断。`[10,10,10,10]` 终局 20 对 20 为和局 EV=`0`，不再报「后续抽牌不足」。`[1,10,10,10]` 独立排列 EV=`1/4`。结算后或「已结束未结算」桌上留牌都不能把发牌前入口误判成「本轮已发」；当前手仍是 `ROUND_INACTIVE`。观察完整时六副剩余仍是 `PREDEAL_SHOE_TOO_LARGE`；观察不完整记漏录/缺口，不是已发。未揭示牌仍使组成未知；**十点未细分 T** 对明示不分牌的点值入口只计入十点桶，不再无条件 `COMPOSITION_UNKNOWN`，也不能冒充 10/J/Q/K 身份或同牌级分牌组成。区间为 `diagnostic_candidate_envelope`，另有同一冻结π的 `common_frozen_policy_envelope`；调用方列出候选即使全部算成也不能把 `coverage_complete` 写成真。可行组成由起源知识枚举；枚举截断或有组成算不成则覆盖仍不完整。二者 `robust_signal_allowed=False`，分别优化的组成最优不是共同可执行π。同一可见信息集上可以选出相反最优动作（例如 16 vs 9，十点剩余停牌、五点剩余加倍），该诊断不能当稳健下界。可见信息第一动作 maxmin 只选一个动作：16 vs 9 停牌最坏 `-1`，不能执行加倍在五点剩余上的 `+2`；这是当前手条件优势，不是发牌前开局优势。默认补牌后续仍按各组成最优；也可显式冻结为停牌或玩具硬规则（12 vs 9 五点剩余上组成最优补牌 EV=`-0.2`，补后停牌 EV=`-1`，玩具硬 EV=`-0.6`）。冻结续玩在同一可见信息下动作相同，但仍不是全部信息可行π的搜索，`verified_robust_positive_lower_bound` 仍为假。`PreDealInput.from_dict` 缺投降字段拒绝，dataclass 缺省不再是晚投降。所列冻结π的 `maxmin` 不是组成最优包络：六张牌晚投降诊断上界 `5/36`，停牌/玩具硬 maxmin 为 `0`。交互精确入口仍 ≤16 张。独立 oracle 无生产导入；当前手 `5,5` vs 9 剩余三张十点加倍 EV=`+2`；软 17 vs 2 剩余三张十点补十是硬 17 不是 27。对照实验缺会话规则时拒绝，不再默默补晚投降。

## M2：完整牌靴固定策略离线 MC

`fixed_policy_mc.py` 现有冻结合法未分牌 S17（硬/软/加倍/允许时晚投降；不分牌、不买保险），以及原来的冻结停牌与玩具硬规则。都不是组成最优，也不是已核验赌场基本策略表。精确未分牌最优策略 ID 仍被拒绝。Python 入口缺投降字段拒绝。固定组成（同一副牌洗牌，含整靴 `remaining=None` 或 `remaining=整靴张数`）与剩余张数先验（每次重抽不同组成）分开标记；后者不能作正式窗口声称。回执分开 `rules_digest` / `policy_digest` / `algorithm_digest`，并写出组成向量、规则快照、净收益分布与 p(win/push/lose)。失败或未跑样本使总体 `ev` 为空，成功子样本均值只作诊断。`z` 与预算在抽样前校验。取消延迟是察觉 `cancelled()` 之后的墙钟。固定组成且预注册样本完成时，Wald 下界>0 可标统计正、上界≤0 可标统计非正；这不是精确最优、不是 timely、不是桌面已证。合成 MC 仍 `timely=False`、`not_a_reliable_window_claim=True`。交互 5 秒 / 16 张门槛未改。CLI 增加 `legal-unsplit`，不再因为统计符号声称而退出。

## M3：整靴口径

缺省扫描仍是精确发牌前：六副剩余>16 为 unsupported，不能把 312 张硬穷举。可选用冻结策略 MC 评估切牌前检查点；`evaluation_method`、`evaluation_policy_id`、`path_policy_id`、`input_scope`、`cut_policy`、`sample_plan` 分开写入。不把切牌改到尾靴或精确 16 张上限；整靴缺省切牌剩余仍是 52。MC 与精确最优不合并成一条曲线：一次研究只用一种评估方法。重洗负对照对同一组成/规则/策略重新评估，不只保留实现净收益。独立牌靴汇总的 `zero_window_rate` 只在有完整评估的牌靴上计算；全部评不完时为 null。3/6 轮快照是实际耗牌剩余，不是固定减 65/130；也可选用同一套 MC 检查点，缺省仍精确。`path_policy_id` 与 `evaluation_policy_id` 分开；玩具硬规则不得叫已核验基本策略。合成 `evaluate_predeal` 一律 `source_mode=synthetic-composition`、`timely=False`、`not_a_reliable_window_claim=True`。漏牌、重复、错认按公开发牌事件重建下一轮剩余，并与完美公开事件对照计分。延迟是已确认前缀。`run_shoe_windows.py` 增加 `--evaluation-method exact-small|fixed-policy-mc`。研究脚本 `--surrender none|late` 仍为必填。

漏牌修复：分牌后缀可重放，同靴跨轮后缀可重放；历史前缀仍看不到后来插入的牌。重放事件带 `repair_original_event_id` 指向原事件。发生时间只记在负载，`observed_at` 是确认时间，不能把早期发生时间当成当时已确认。内存应用失败不留半截撤销；进程从 SQLite 恢复后仍能分开当时前缀与修复后当前。已保存分析快照不因修复改 EV 或当成当时抓住窗口。跨新靴与纠错后缀继续拒绝，未删保护检查。

## M4：证据层与未勾选验收包已接线，真实材料仍缺

`analysis/evidence.py`：missing / declared / evidence-linked / reviewed；软件不能写 `accepted=true`。全屏 `accepted()` 恒为 False。操作者对照匹配的 operator/video/pair 字符串只记 `declared_pair_ids`，`paired` 仍为 False。真实桌档案 `missing_archive` 显式 `accepted=False`。

勾选与本地文件存在不能把 F11 写成 `accepted=true`（`fullscreen_acceptance.accepted()` 恒为 False；文件齐备最多 `evidence-linked`）。软件保存的全屏清单会清掉单项 `passed=true`，并写入文件 SHA 身份链；摘要不符仍是 `declared`。像素探针即使声称浏览器采集也不能把该项写成通过。手工把某项 `passed=true` 写进 JSON 再绑定文件时，软件会清掉勾选并始终列出五项 `human_blockers`。`bind_item_artifact` / `python scripts/bind_acceptance_artifact.py` 可把本机文件挂到某一项并哈希，该项 `passed` 仍为 false。`python scripts/extract_review_frames.py --video <本地录像> --output-dir <目录>` 可抽待审帧并哈希源片与 PNG；`--bind-pack` / `--pack-output` 可把待审帧挂到未勾选验收包。`attach_review_frames` 可按 SHA 挂到未勾选的授权原片项。`accepted` / `independent_video` / `unused_*` / `human_run` 恒为 false，不能挂到未使用声明项，不改写原片，不能代替授权原片或未使用声明。

操作者对照：`trial` / `trial_from_usage` 保留 `operator_id` / `video_id` / `pair_id`；`conclude` / `write_export` 写出 `identity_chain`。现有导出对话框可选填这三项，空则仍只记录条件。匹配字符串只记 `declared_pair_ids`，`paired` / `accepted` / `auto_prompt_default` 仍为 False。真实桌档案导出始终 `accepted=false`、`evidence_level=declared`；手工把 JSON 改成 `accepted=true` 后再加载仍会盖回未通过。界面顶栏把已载入档案标成「声明未验收」。`scripts/report_unattested_materials.py` 扫描操作者/全屏/桌档案/验收包时也不认手改 `accepted=true`。

发牌前精确入口与合成 `evaluate_predeal`、固定策略 MC 均写入 4.4 身份：`window_kind`、`source_mode`、`strategy_id`、`ledger_prefix_digest`、`information_cutoff`、`result_ready_at`、`decision_deadline`、`timely`、`window_state`。计算状态与窗口状态分开：`positive_supported` / `nonpositive_supported` / `indeterminate` / `unavailable`。MC 在固定组成且预注册样本完成、Wald 区间给出符号时可以 `window_claim_allowed=True`，但这是统计正/统计非正，不是精确最优，也不是 timely 或桌面已证；合成组成仍 `timely=False` 且 `not_a_reliable_window_claim=True`。剩余张数先验不能作正式窗口声称。当前手结果标 `current_hand`，开局 `window_state=unavailable`，不能改称开局优势，也不能进入零窗口/混淆矩阵的正开局计数。快照拒绝把合成结果改成当时抓住，也拒绝把当前手 `window_state` 改成正开局。求解器本身不把结果标成 timely live；UI 信封 `timely_live_claim` 只有当时 live 且知识/输入未变才可为 true。历史复算、手动 as-of、迟到结果、软件省略该字段都是 false。发牌前按钮可替换进行中的当前手请求；发布结果必须是发牌前 schema，显示「合成研究」，不贴「当前 ·」。

## M5：尚未冻结

`freeze_status()` 在软件侧永远 `ready=False`，并拒绝 `m4_pack.accepted=true` 或脏树冒充已授权提交。产品仍为 `0.2.0b1`，SQLite schema 2，交互精确入口仍 ≤16 张 / 5 秒。冻结清单：

- 单一候选 SHA（推送后以该分支 GitHub HEAD 为准；仍不是已授权发布冻结）
- 干净运行目录与绑定到该 SHA 的完整 unittest 计数
- PR #13 正文按本工作树更新（草稿，目标仍是 `vision/v0.3c-live`，不是 `main`）
- M4 五项由人验收通过（软件不得代勾）
- 用户明确授权发布冻结；不合入 `main`

## 本机验证（工作树）

```
python tests/independent_six_card_oracle.py
python tests/independent_small_shoe.py
python -m compileall -q blackjack_lab tests scripts
python -m unittest discover -s tests -q
```

完整 discover **1080** 通过、**8** 跳过。不要沿用 808/809、910/941、946、952、956、958、961、972、975、978、983、986、988、990、994、995、997、1001、1002、1003、1006、1008、1009、1011、1014、1016、1017、1019、1020、1023、1028、1031、1038、1043、1044、1045、1047、1048、1053 或上一轮 1055。本机计数不能换签为精确提交 CI，需等推送后的 GitHub Actions。

---



日期：2026-09-15。基准提交仍是 `3bcd627814aaa3792d4edc24e2ad737cfbbe727d`。本段不是发布声明，也不是 M0b–M5 完成。

## 本单范围

只做审查包 M0a：F01 无投降必须传到发牌前求解器；F02 不可评估不得记成非正或零窗口。未扩 16 张上限、未做观察当前性/异步身份、未做完整牌靴离线评估、未改 SplitEngine / SQLite schema / 私有材料。

## 原来失败、现在通过的算例

人工剩余 `[10,10,9,9,8,7]`（六张牌，不是六副牌）。独立 `tests/independent_six_card_oracle.py` 枚举 720 个物理排列，无生产导入：无投降 EV=`0`；晚投降 EV=`5/36`。

完整 `build_predeal_input → calculate` 与该 oracle、以及 `tests/predeal_reference.py` 一致。无投降结果不含 `-0.5` 质量，摘要写「无投降」而不是「晚投降」。

原 `test_full_six_deck_reshuffle_never_publishes_opening_ev` 曾要求全 unsupported 时 `zero_window=True`。该期待已作废：全未支持现在是「评估不完整，不能判断是否无窗口」，`zero_window` 仅在完整已声明评估且无正 EV 时为真。

## 改了哪些文件，为什么

- `predeal_contracts.py` / `predeal.py` / `research_windows.py`：投降规则进入输入、动作集、`solve_predeal_counts`、结果与摘要。
- `shoe_windows.py`：耗牌与评估共用同一投降动作集；零窗口改用 `evaluation_scope`。
- `observation_error.py` / `unused_holdout.py`：共用 `confusion_matrix`。真值不可评不定误报/共同阴性；真值正且观察缺失记 `missed_unavailable`，不是 FN。
- `ui/analysis_panel.py` / `ui/controller.py`：合成组成与复算走会话/快照规则，不再默认晚投降覆盖无投降。
- `core/rules.py` 能力矩阵：写明未支持不能记成零窗口。
- 测试：独立 oracle、成对六张牌、窗口统计语义。

## 本机验证（工作树，不是新提交 CI）

```
python tests/independent_six_card_oracle.py
python -m blackjack_lab.main --check
python -m compileall -q blackjack_lab tests scripts
python -m unittest discover -s tests -q
```

结果：oracle 无投降 `0` / 晚投降 `5/36`；自检通过；**910** 项通过，**8** 跳过，约 221.5s。不要沿用旧 PR 文案里的 808/809。尚未 push，GitHub Actions 不会对新工作树出号。

跳过的 8 项是原有 optional/环境跳过，不是为了本单删除失败记录。未跑私有录像、F11、真人操作者或 `verify_review_handoff.py` 以外的发布打包。

## 仍未完成（按大纲下一单）

- **M0b**：来源新鲜度 vs 知识变化；草稿撤回不等于对账完成；录像回放不得标成当前真实牌桌；发牌前输入 A→B 后旧异步结果不得重新发布。
- **M1**：小牌靴参考边界；`int()` 提前截断；`[10,10,10,10]` 终局过度拒绝。
- **M2–M5**：完整牌靴固定策略离线评估、整靴/误差口径、真实材料验收、冻结发布。

产品显示仍为 `0.2.0b1`。PR #13 仍为草稿，目标 `vision/v0.3c-live`，不是 `main`。未经要求不提交。

---



目标仍是审计任务单第 6 节的限定场景研究版 + 真实桌面版门槛，不是把当前手牌 EV 改称开局优势。T10 数学核心与 SQLite schema 2 未改。本段不是发布声明。

软件已接线、仍不能写成已验收的部分：
- 当前手 / 发牌前窗口分开；观察新鲜度进入分析；剩余≤16 的发牌前精确入口。
- 整靴合成扫描、独立牌靴集合、合成录牌误差（含延迟错过/误报漏报）、前三/六轮消耗对照（其他座位不是独立样本，也不是多玩家 EV）。
- 未知组成区间（公布 min/max，禁止平均牌靴）；策略耗牌对照（不共享实现路径）。
- 漏牌回溯修复（实验性）；真实桌档案可载入/导出，研究模板拒绝。
- 全屏验收清单、环境探测与浮层像素探针可保存；空证据、合成色块、未配对真人试验不能改自动提示默认，也不能把 F11 写成通过。
- 未使用留出包缺声明或缺 `video_id` 即拒绝；`source_kind=unused_video` 加具名 `video_id` 只记 `declared_unused_video`，软件仍写 `independent_video=False` / `accepted=False`。哈希只证明字节身份。窗口清单拒绝本工具浮层标题。

仍缺证据、不得宣称完成：真实目标桌档案（R3）、配对真人操作者试验（R4）、用户 Windows 浏览器 F11/源帧验收（R5）、已声明未使用的独立录像逐轮剩余（R7）、PR #13 发布冻结（R8）。`.local-evidence` 开发录像角色未确认，不能当作独立留出。

---

# 连续回放验收入口与新录像准备（2026-09-12 后续）

本次继续从已推送、CI成功的 `d2f9bd8` 推进。前一目标轮有源码/数据/CI实际进展；本轮找到候选新录像并补齐顺序回放验收入口，也属于实际进展。独立会话身份及人工真值仍未确认，目标未完成。

新增只读 `VideoReader → pipeline → FrameTracker → 人审采样帧对照` 入口；不让GT进入模型/跟踪，不写账本，不从GT局号生成操作员轮界。可评身份碎片/合并/切换、首次错认和逐输出错误；完整轮事件/账本事件仍未测。原帧标注增加源帧号、RGB/ROI摘要和可核对局号，录屏在转换中变化时拒绝声称完整。

本机历史录屏目录发现三段晚间文件：只将20:03开发候选转换成540张ROI帧、804个未标注裁片和27张原帧复核清单；20:21和20:42仍未查看画面/抽样/训练，后者为拟定最终保留候选。三个来源均做完整SHA冻结，用途和是否从未被别的流程使用还待用户确认。第一段120帧真实只读回放接线成功，ROI像素匹配；未人审，所以正式metrics=null。

新增17项timeline与6项annotation/转换回归均通过，含真正小合成录像走共享入口；新精确提交CI与最终测试另记本机 `.local-evidence/new-recordings-20260912-preparation/continuation-delivery.json`。不把前次613结果直接换签到本轮。

新素材和所有源摘要/画面/标签留在 `.local-evidence/new-recordings-20260912*`，原片未修改、未上传。原目标继续等待材料角色确认、人工标签/物理牌/归属/完整事件真值及独立真实效果比较；不可仅因CI或回放能执行而标完成。

---

# V0.3e 核心识牌整改接手记录（2026-09-12）

分支 `codex/v0.3e-recognition-review`，基线 `fbff7b296532daa2eceea0c4d7893c0b0e37598f`。本轮软件整改已完成，独立真实录像与人工真值验收未完成。用户此前已授权更新 GitHub；只推修复分支，不合并 main，不发布运行包。源码、真实材料和验收证据分别保存。

本轮修复：跨会话和内容防泄漏；增强/复制来源独立投票及真实拒识 margin；Q/A 透视宽高过滤与 10 白牌边缘/宽度遗漏；原帧人工框选和不可读/漏检/重复候选评测；多会话训练、旧报告拒覆盖；显式模型接入图片/回放/捕获共同入口；模型、ROI、来源切换与失败帧撤回；显式新轮绑定解决同位置新牌被旧 committed 身份吞掉。保留数学、DAS、Tk、SQLite 和真实用户库。

标签重要发现：原唯一 A 与两条 10 并非真实相应点数。原标签未改，本地建议更正 3 条，新增 Q4/A2/10各2个原帧建议样本（共8，均 assistant_proposed）。新开发模型共204条训练标签，A2、10=2、Q4；其余标签仍无已人审证据，不得把类别数量当独立牌或可信真值数量。

固定旧196训练/63开发裁片：原模型本机37正确+9错认+1junk误收，全输出precision78.7%、正确覆盖37/57；v3内容并票模型24正确+1错认+0junk误收，precision96%、覆盖24/57。更正建议模型在同旧开发集也是24/1/0。覆盖下降，且旧留出无Q和已验证人工真值，所以不宣称全面变准。原帧建议标注评测 valid=false，正式指标为null，不能用诊断计数作为验收成绩。

工程证据：原分支完整532测试，1失败、2跳过；失败是旧模板归一化不一致将合成A排为Q，已修复。修复树首次完整611测试通过、2跳过；随后原帧真值完整性再增2回归，最后聚焦dataset17+frame13共30通过。提交6a3ece2完整613项通过、2跳过（95.800s）；对应首次CI 34696841762因YAML冒号解析失败，0 job启动，已改块状命令并通过解析，重试保留独立记录。可选依赖与无依赖环境分别验证；最终数字及精确提交CI另附本机delivery-status.json，不把前次结果换签到后续提交。临时SQLite验证连续40帧双8只扣2、显式新轮同位置再扣2。v3实际WGC只捕获自建Tk窗口，34帧到达/9帧分类/18角标候选/0接受/不入账，窗口关闭；这是接入证据，不是视频准确率。

本机证据（工作树 `.local-evidence/`）：
- `baseline-20260912/materials-freeze.json` 与 `original-holdout-replay.json`：冻结3374文件和原模型实跑；复核原文件0变化。
- `v3-fixed196/`、`v3-corrected-proposals/`：分开的模型、参数、训练分组和开发报告。
- `q-investigation/`、`ten-investigation/`：原帧位置、漏提取分析、建议补框和标签更正；全是本机材料。
- `controlled-model-capture-v3-final/`：真实WGC加载最终v3开发模型，不接触现有浏览器。
- `full-suite-remediation-first.txt`、`final-metrics-focus.txt`：首次完整与最后局部修复日志。

继续工作必须获得：新的独立会话本地路径；既有标签及原帧全部目标的人审复核；跨帧物理牌ID、归属和轮次真值。独立视频应在参数冻结前划为最终测试，不能从反复使用的63裁片中取代。软件修复、CI和源码推送均不能替代这些条件。

操作见 `docs/vision/V0.3e-审查整改验收.md` 与 `docs/vision/V0.3e-指定模型与CLI.md`。旧材料/标签/模型/用户库不入Git，旧交接正文原样保留在下方。

---

# 项目接手状态

更新时间：2026-09-12（V0.3e 标注 + 训练 + 按局留出评测）
执行工具/模型：Cursor Grok 4.6（执行质量以本文件命令与日志为准，不由模型名称证明）
当前任务ID：V0.3e 真实角标标注闭环（候选观察，不自动入账）
产品显示仍为 `0.2.0b1`。不重做 T5–T10，不另建仓库，不开发录屏器，不改 SplitEngine / 公式 / 容差 / 5s / SQLite schema / 用户库。

分支与基线：
- 工作分支仍为 `vision/v0.3c-live`，HEAD 在本轮开始时是 `d062e32`（模板匹配未收敛）。
- 上游基线仍为 main `cdd0e8f2e34e27f9652575556b3d4b5c9f25603d`。
- 本轮已授权 commit，未授权 push / 发运行包。素材与标签在 `.local-evidence/`，不入库。

## 本轮完成

- 按局切分：`blackjack_lab/vision/glyph_dataset.py`。同一帧 / 同一局不得进训练与留出两侧。
- 分类器：`blackjack_lab/vision/rank_classifier.py`。本机 OpenCV 5 无 `cv2.ml` / HOG，用 numpy kNN。6/9 不做 180° 增强。分数不是校准概率。
- 脚本：`prepare_glyph_queue.py`（含 `--append` 只加训练局）、`label_glyphs.py`、`train_rank_classifier.py`（门槛只在训练局内选）。
- 测试：`tests.test_vision_rank_classifier` 9 项。
- 文档：`docs/vision/V0.3e-标注训练留出集.md`。

## 命令（本机已跑）

```
python -m unittest tests.test_vision_rank_classifier -q
python scripts/prepare_glyph_queue.py .local-evidence/material-train-20260912 --append --per-round 3
python scripts/train_rank_classifier.py .local-evidence/material-train-20260912/glyph-queue
```

## 真实留出数字（留出 63 条冻结；不是验收通过）

| | 第一轮 | 第二轮 |
|---|---|---|
| 训练标注 | 93 | 196 |
| 接受项准确率 | 0.857（24/28） | 0.804（37/46） |
| 端到端召回 | 0.421（24/57） | 0.649（37/57） |
| 错认 | 4 | 9 |

Q 仍未进入训练（连通块经常抽不到桌上的 Q）。A / 10 仍然太少。**自动确认未打开。**

## 已知限制

- 留出是同一段采集的后若干局，不是第二个会话。
- 标注来自拼版图上能看清的裁片；用户应用 `label_glyphs.py ui` 复核后再当真值。
- 未接到核对窗，未改实时捕获路径。
- 未改 SplitEngine.cs。未改他人测试。

## 下一动作

补 Q（可能要改抽取，而不是再加同一批连通块）；另采一段做留出。召回和错认没有到能入账的程度之前，不要打开自动确认。

# 历史接手（V0.3c 实时捕获落地，保留）

更新时间：2026-09-12（V0.3c 实时捕获落地：授权窗口 → 统一帧 → 现有识别管线）
执行工具/模型：Cursor Claude Opus 4.6（执行质量以本文件命令与日志为准，不由模型名称证明）
当前任务ID：V0.3c 实时窗口捕获（浏览器窗口只读捕获，不操控网站、不下注）
产品显示仍为 `0.2.0b1`。不重做 T5–T10，不另建仓库，不开发录屏器，不改 SplitEngine / 公式 / 容差 / 5s / SQLite schema / 用户库。


分支与基线：
- 先把此前**未提交**的 V0.3a/V0.3b 成果落盘为回滚点：`vision/v0.3a-r0-r1` 上的 `7c7405a`（81 文件，6496 插入）。用户已授权本次提交。
- 本轮工作分支：`vision/v0.3c-live`，从 `7c7405a` 新建。
- 上游基线仍为 main `cdd0e8f2e34e27f9652575556b3d4b5c9f25603d`。
- 未推送、未开 PR、未发运行包。

## 本轮完成

- `blackjack_lab/capture/`：`contracts.py`（FramePacket / GenerationToken / 状态常量 / 内容签名）、`window_list.py`（ctypes 枚举，不新增依赖，排除本进程窗口防预览回环）、`frame_intake.py`（采样、裁区、独占像素、重复帧、有界队列、换代）、`wgc_source.py`（WGC 适配层）。
- `blackjack_lab/vision/live_input.py`：`LiveStyle` 归一化标定 + `layout_for_frame` + `recognize_frame`。修掉 `navy_live_felt_v1` 把裁区写死成 2560×1440 绝对像素的问题。
- 模块边界双向加测：vision 不导入 capture，capture 不导入 vision/ledger/ui；没装 `windows-capture` 时识别侧仍可导入。
- 脚本：`scripts/live_capture_probe.py`（列窗口 / 真捕获 / 报真实指标）、`scripts/verify_live_capture.py`（自建已知颜色测试窗口的实机自检）。
- 文档：`docs/vision/V0.3c-实时捕获.md`（选型、实测数字、三条必须照做的性质、已知不支持）。

## 命令（本机已跑）

```
python -m unittest tests.test_capture_intake tests.test_vision_live_input tests.test_vision_live_pipeline -q
python scripts/verify_live_capture.py --output .local-evidence/live-selftest-20260912
python scripts/live_capture_probe.py --list
python scripts/live_capture_probe.py --window 14223860 --seconds 5 --fps 10
python -m unittest discover -s tests -q
```

焦点 66 项 OK。实机自检 9 项全通过（像素与已知颜色一致、静止报「暂无新帧」而非 live、运行中窗口关闭报来源消失、停止后不留线程、对已消失窗口重开被拒绝）。
全套 `discover` 519 项：**2 项失败是本轮之前就存在的**，已用 `git stash -u` 在 `7c7405a` 上复现确认，不是本轮引入：
1. `test_vision_isolation.test_main_check_without_loading_vision_cv2`：子进程输出 UTF-8 中文，`subprocess.run(text=True)` 用本机 GBK 解码，读取线程抛 `UnicodeDecodeError`，`proc.stdout` 变成 `None`。断言消息被提前求值因而报 TypeError。修法是给 `subprocess.run` 加 `encoding="utf-8"`；本轮未改他人测试。
2. `test_vision_navy_table.test_corner_ink_ranks_an_ace`：Hershey 字体模板把 A 认成 Q。这正是真实识别未达标的同一个根因。

未改 SplitEngine.cs。未改他人测试。

## 实测数字（一次本机测量，不是承诺速率）

显示器捕获 2560×1440 BGRA，4.04 秒 184 帧，首帧 219ms；静止窗口 5 秒仅 1 帧；取帧时帧龄 p50=p95=15.6ms。
依赖 `windows-capture==2.0.1`（MIT，`cp39-abi3` 轮子，Python 3.14 可直接装）。

## 已知限制（不得写成已完成）

- **真实牌桌点数识别仍未通过**：本机录像第 49222 帧检出 16 框、接受 0 点数。两个原因：牌太小（窗口 1218×840 时牌阵仅 730×140，点数约 10–14 像素高）；`navy_cards.py` 用 Hershey 字体当模板，与真实扑克字形无关。
- 座位归属未核对：探针把 16 张牌全归到「玩家4/5/6」。
- 实时预览界面、区域/座位标定界面、自动确认均未开工。
- 未验证：多显示器、DPI 中途变化、受保护内容黑屏、长时间稳定性、真实牌桌窗口上的连续捕获。

## 下一动作

唯一下一任务：**M2 真实牌面识别**。前置条件已由用户确认——牌桌视频会全屏/最大化观看，牌面像素约增 4–5 倍。
具体两步：(1) 用 `minAreaRect` 做旋转校正后再取角标，替换现在「取轴对齐框左上 55%」的做法（牌是扇形排开的，现做法必然取偏）；
(2) 从真实画面裁角标、人工标注，建真实模板库，替换 Hershey 占位模板。
在真实留出素材上出检出率/rank 准确率之前，不得开放任何自动确认。

# 历史接手（V0.3b 旁观录像，保留）

更新时间：2026-09-12（开发目标改为旁观录像闭环：理解录像，不生产录像）
执行工具/模型：Cursor Grok 4.6（执行质量以本文件命令与日志为准，不由模型名称证明）
当前任务ID：V0.3b 旁观录像（本地录屏文件 → 选区/识牌 → 跨帧去重 → 人工确认 → 现有账本）
产品显示仍为 `0.2.0b1`。不重做 T5–T10，不另建仓库，不开发录屏器，不改 SplitEngine / 公式 / 容差 / 5s / SQLite schema / 用户库。
开发基线：已合并 main `17c664375da2809138f8290fe0f7a77fc1603d25` 的后代 `cdd0e8f2e34e27f9652575556b3d4b5c9f25603d`。
本轮未授权 commit / push / 发运行包。

## 本轮完成

- 任务书：`docs/vision/V0.3b-旁观录像.md`。观察者模式；识别多人 ≠ 计算多人 EV。
- 开靴三态：新靴完整 / 中途开始 / 不确定。三种都不自动补副数或烧牌。
- 跨帧跟踪：同一牌多帧一个观察；两张 8 两个观察；移位更新归属；已确认后重放不再入账。
- 后揭底牌：`visible_rank_at` 在揭牌前保持未知，供复盘按当时信息分析。
- 核对窗可打开本地录像（只读），暂停后识别本帧；录屏仍用 NVIDIA / Windows 现成工具。

## 命令（本机已跑）

```
python -m unittest tests.test_vision_video tests.test_vision_isolation tests.test_vision_bridge tests.test_vision_ui_confirm tests.test_vision_pipeline tests.test_vision_contracts -q
```

47 项 OK。未改 SplitEngine.cs。未提交。

## 旁观第一桌（2026-09-12 续）

已从本机 NVIDIA 录像冻结 `navy-live-felt-v1`：2560×1440 → 直播画面 → 深色绒面，庄家+7 座位。检测出框，无模板则不自动认点。原文件只读。座位号按画面从左到右，须人工核对。

```
python -m unittest tests.test_vision_navy_table tests.test_vision_pipeline tests.test_vision_isolation tests.test_vision_video -q
python scripts/probe_navy_live_video.py "C:\Users\Administrator\Videos\NVIDIA\Desktop\Desktop 2026.09.12 - 12.58.11.02.mp4" --frame 49222
```

## 下一动作

用核对窗打开这段录像，人工拒绝印刷字、确认真牌与座位。未支持的多人分析必须标出。不要把当前工作树运行包直接发给别人。

# 历史接手（V0.3a R3 + T10 门禁，保留）

更新时间：2026-09-12（V0.3a R3 封闭集与确认入账已在更新后的 main 上实跑；T10-S1/S2 + T9-H1 保留）
执行工具/模型：Cursor Grok 4.6（执行质量以本文件命令与日志为准，不由模型名称证明）
当前任务ID：V0.3a 离线识牌（候选 → 人工确认 → 现有账本）
产品显示仍为 `0.2.0b1`。不重做 T5–T10，不合并 PR #12，不重建 `experiments/`，不改 SplitEngine / 公式 / 容差 / 5s / SQLite schema / 用户库。
开发基线：已合并 main `17c664375da2809138f8290fe0f7a77fc1603d25` 的后代 `cdd0e8f2e34e27f9652575556b3d4b5c9f25603d`。
本轮未授权 commit / push / 发运行包。

## 本轮完成

- **T10-S1**：不可能原牌组成拒绝（25K/六副）；合法 25T 与 6/7/8 对照保留。`"false"` 解析为 False；`[6.9]` 拒绝。
- **T10-S2**：输出目录已存在则拒绝覆盖。取消停当前计算、后续不跑、已完成保留；按钮文案一致。CLI 仅显式参数覆盖 `--config`。
- **T9-H1**：336 回执绑定案例→输入→完整结果。空壳、`EV=999`/`hit_bust=12`、共用 stub 拒绝。本工作树无 opt3 原材料则两项 skip，不重跑矩阵。
- **V0.3a**：本地图出候选；确认/改正/拒绝后走现有控制器与账本；拒绝不写。R3 holdout：1430 可辨认 / 200 干扰；已接受准确率 1.0；端到端检出 0.8636（建议 0.95 未过，未降阈值）。Windows 回执 `.local-evidence/acceptance-v03a-20260912-124156-212169/`。

## 命令（本机已跑）

```
python -m unittest tests.test_vision_contracts tests.test_vision_pipeline tests.test_vision_bridge tests.test_vision_ui_confirm tests.test_experiments.TestExperiments.test_twenty_five_kings_on_six_decks_are_rejected tests.test_experiments.TestExperiments.test_cli_defaults_do_not_erase_config_file -q
python scripts/verify_vision_v03a.py --rebuild-holdout
```

焦点 35 项 OK。验收包 `passed=true`（实验门禁 35 OK / 2 skip = 无本树 opt3 原材料）。未改 SplitEngine.cs。识牌改动与 R3 文档尚未提交。

## 下一动作

真正的前三/六轮模拟仍未开工。不要把当前工作树运行包直接发给别人。需要你授权才会 commit / 推送。

# 历史接手（T10-S1/S2 + T9-H1 完整结果绑定，保留）

更新时间：2026-09-12（T10-S1/S2 + T9-H1 完整结果绑定；基线 `17c6643`，PR **#12 已合并**）
执行工具/模型：Cursor Grok 4.6（执行质量以本文件命令与日志为准，不由模型名称证明）
当前任务ID：实验入口边界与 336 回执内容绑定
产品显示仍为 `0.2.0b1`。不重做 T5–T8，也不从头重做 T9 的 336 矩阵 / DAS 优化 / Windows 打包骨架。
开发基线：GitHub `main` **`17c664375da2809138f8290fe0f7a77fc1603d25`**。不要再派“合并 PR #12”或“开始创建 experiments 目录”。
本轮未改 C# 公式、容差、5 秒预算、p95=2s、SQLite 事件结构或真实用户库。未授权不发运行包。

## 本轮完成

- **T10-S1**：合成实验先按 13 种原牌容量核对玩家牌、庄家明牌与额外移除。明确 25K（六副）拒绝；未细分 25T 仍合法。`peek_negative="false"` 显式解析为 False，不再被 `bool(text)` 变成 True；`n_decks=[6.9]` 拒绝，不再截成 6。6／7／8 边界分别测过。
- **T10-S2**：输出目录已存在则拒绝覆盖；JSON/CSV 走现有原子写入。取消复用 `AnalysisService` 停当前计算，后续场景不再跑，已完成项保留。按钮文案与行为一致。CLI `--config` 不会被 argparse 默认值抹掉，只有显式参数才覆盖。
- **T10-S2**：输出目录已存在则拒绝覆盖；JSON/CSV 走现有原子写入。取消复用 `AnalysisService` 停当前计算，后续场景不再跑，已完成项保留。按钮文案与行为一致。
- **T9-H1 收口**：336 附件必须有案例身份、独立结果文件、完整输入/动作/EV/分布；空壳、`EV=999`/`hit_bust=12`、同一文件冒充 336 案均拒绝。opt3 原材料若仍在本机，按完整结果复核，不重跑矩阵。

## 命令（本机已跑）

```
python -m unittest tests.test_experiments tests.test_experiment_ui tests.test_das_release_gate -v
python -m unittest discover -s tests -q
```

焦点 36 项 OK（含本机 opt3 336 完整结果绑定，不再 skip）。全套 **397 OK**。未改 SplitEngine.cs。未提交本轮 CLI/7 副边界补充。

## 下一动作

V0.3a 识牌已接到本 main：候选 → 人工确认 → 现有账本。R3 封闭合成集与 `scripts/verify_vision_v03a.py` 正在本机跑。真正的前三/六轮模拟仍未开工。不要把当前工作树运行包直接发给别人。

# 历史接手（T9-H1/H2/H3 + T10-A/B，保留）

更新时间：2026-09-12（T9-H1/H2/H3 复核 + T10-A/B 最小实验入口已落地；未授权不合入/不推送/不发运行包）
执行工具/模型：Cursor Grok 4.6（执行质量以本文件命令与日志为准，不由模型名称证明）
当前任务ID：T9 发布校验与优化复核，随后 T10 V0.2c 最小实验
产品显示仍为 `0.2.0b1`。不重做 T5–T8，也不从头重做 T9 的 336 矩阵 / DAS 优化 / Windows 打包骨架。
审查冻结提交：`6f3b2560be91b655d628045918d3afd54d0f52b1`（PR **#12 开放未合并**）。`origin/main` 仍为 T8 `137bcdc`。候选 CI 333 项 / 1 skip 不能换签 336。
本轮未改 C# 公式、容差、5 秒预算或 p95=2s 门槛。未授权不 merge / force-push / 分发运行包。

## 本轮完成

- **T9-H1**：`scripts/das_matrix_gate.py` 复核附件 336；`receipt.passed` 只是声明。空 `passed:true`、错源码、漏文件、篡改结果、重算后超 p95 均拒绝。本地 opt3 原 336 目录按数值范围哈希复核通过。
- **T9-H2**：`make_portable_copy.py` 从 git HEAD + 允许清单打包。临时仓库覆盖干净/脏/忽略文件；`docs/.env` 与 `fixtures/session.sqlite3` 不进包。`BUILD_INFO.json` 区分 `source_manifest` 与覆盖 fixtures 的 `package_manifest`。符号链接拒绝。
- **T9-H3（审查定义）**：`tests/test_das_optimization_diff.py` 对照 Fraction：63/64/65/66 剩余、peek A/T、软多 A、注额、首手爆牌、第二手 DAS、三类待牌；逐格联合/合计分布，容差仍 1e-10。未改 SplitEngine。`tests/test_portable_runtime.py` 在仓库外副本、清空 PYTHONPATH、核验 `__file__` 后录牌→分析→保存→新进程复算。
- **T10-A/B**：`blackjack_lab/experiments/` + `scripts/run_experiment.py` + 主界面「对照实验」。6/7/8 副合成对照与历史前缀回放；非法组成失败保留；历史不吸收后续揭牌。固定移除 ≠ 整轮模拟。

## 命令（本机已跑）

```
python -m unittest tests.test_das_release_gate tests.test_portable_copy -v
python -m unittest tests.test_das_optimization_diff tests.test_portable_runtime -v
python -m unittest tests.test_experiments tests.test_experiment_ui -v
```

336 的 p95 1.756s 仍是开发机 opt3 记录，由门禁按当前 SplitEngine 字节绑定复核，不是本轮重跑矩阵。未提交，故没有新 HEAD 的 GitHub CI。

## 下一动作

工作树有未提交的 H1/H2/H3/T10。需要你授权才会 commit / 推送 / 合入 PR #12。不要把当前未提交工作树生成的运行包直接发给别人。V0.3 识牌与真正的前三/六轮模拟未开工。

# 历史接手（T9-H3 外部对照，保留）

更新时间：2026-09-12（possibly-wrong v7.6 已实跑对照；名称曾与审查包 T9-H3 冲突，审查包的 H3 以优化差分与副本运行为准）
执行工具/模型：Cursor Grok 4.6（执行质量以本文件命令与日志为准，不由模型名称证明）
当前任务ID：外部数学对照（小而完整的首批）
产品显示仍为 `0.2.0b1`。DAS 336 与 unittest 项数不能换签为本对照。GPL 二进制不入库。
钉住：`possibly-wrong/blackjack` `v7.6` / `a1f7dbb74266fb39296292bdff568b076120a61c`，`strategy.exe` sha256 `48b0f45c3a2096c11487b4f1d7dd1e1dc0569ff18421971d648a2dfc6b464d6e`。
本机回执：`.local-evidence/external-pw-20260912-t9h3/`，66/66 通过，动作 EV max abs 4.97e-12，庄家 max abs 7.45e-6（对方五位小数）。分牌只记录不门禁。
命令：`python scripts/fetch_possibly_wrong.py`；`python scripts/compare_possibly_wrong.py --output .local-evidence/external-pw-<唯一目录>`；`python -m unittest tests.test_external_pw_compare -v`。

# 历史接手（T9）

更新时间：2026-09-12（T9 完成：336 门禁与 Windows 验收回执已落地；PR #11 未授权不合入、本分支未推送）
执行工具/模型：Cursor Grok 4.6（执行质量以本文件命令与日志为准，不由模型名称证明）
当前任务ID：T9 DAS 专属性能与研究版交付
审查基线：`42e30d37b4e127a17d2980821178915a377826fe`（PR #11，318 CI 通过，未合并）
固定代码提交：`1c9eb717a052f1bcff9fbe1d914b80d3b5154dc1`（`handoff/t9-das-benchmarks`）。不重做 T5—T8。本消息不授权 merge/push。
336 正式过门：`.local-evidence/das-cold-20260912-t9-opt3/`，336 available、0 timeout、0 failed，p50=0.60s，p95=1.756s，各副 p95 均 <2s，max=2.619s。最终验收：`.local-evidence/acceptance-v02b2-das-20260912-t9c/`（干净工作树、`passed=true`、含运行副本）。本机 `unittest discover` 为 333 项（含 T9 新增，1 skip），不能换签 336 或旧 b1 318 冷请求。产品显示仍为 `0.2.0b1`；引擎仍为 das-2。


# 历史接手（T8）

更新时间：2026-09-11（T8 进行中：R-DAS-01 补牌等待不得再开 DAS；未授权不合入）
执行工具/模型：Cursor Grok 4.6（执行质量以本文件命令与日志为准，不由模型名称证明）
当前任务ID：T8 DAS pending-hit 语义修复
产品 `main`：`bb8674b504d855cd2e810be89fbd3cbdcb40400d`（#9 T6 + #10 T7 已合并；与 T7 `7a68563` 同树）。审查包若仍写 #9/#10 待合、`main=a071ed55`，以本段为准。
工作分支：`handoff/t8-das-pending-hit`（本轮提交并开 PR）。**不要**重做 T5，也不要把 Downloads 里过期的「T5下一单」当新任务。未授权不合入。
本轮允许：C# 用牌张数区分分牌第二张与普通补牌；生产引擎/策略升为 `das-2`/`das-v2`；投入上界与参考对齐；旧 `das-1` 快照只读。禁止改旧 b1 模板、旧 `tests/split_reference.py`、T5 `tests/das_contract.py` 参考身份、1e-10/1e-12、5 秒预算。未授权不 merge / force-push。

## 当前活动摘要（T8）
- 审查包 R-DAS-01（P1）：`forced_draw = len==1 or awaiting_hit` 把普通补牌等待当成了分牌第二张，C# 发完后按 `canDas=!aces && ns<21 && stake==1` 重新打开 DAS。直接录入第三张则禁止 DAS，两条路径不一致。
- R-DAS-02（P2）：分 A 的 split 上界曾写成 2；两张牌的补牌等待仍把当前手算进此后可能 DAS。
- R-DAS-03：DAS 专用性能矩阵仍未跑，不得用旧 318 冷请求换签。
- 修复要点：`CanDasAfterDeal(nCards==1 && ns<21 && stake==1)`；生产身份 `v0.2b2-finite-two-hand-das-2` / `sequential-two-hand-total-net-das-v2`。初始第二张仍可 DAS（夹具 EV=4）；已选 DAS 仍 stake=2 `force_close`（夹具 EV=1）。
- 本机 `318` 项 unittest 已通过。下一动作：PR CI 与合入授权。共享 `main` 在合入前仍含 P1。

# 历史接手（T1b/T2b，保留原文）

更新时间：2026-09-11（T1b/T2b 已推送；PR #8 待 CI 与合入）
执行工具/模型：Cursor Grok 4.6（执行质量以本文件命令与日志为准，不由模型名称证明）
当前任务ID：T1b+T2b 交付 + T5 并行
本轮允许文件：交接记录同步推送/PR 事实；T5 仅新契约文档、独立参考与参考测试。未改 `SplitEngine.cs`、旧规则模板、旧参考、容差或 5 秒预算。

## 当前活动摘要
- 集成基线：GitHub `main` `4f84595bfc933896586dab9af9d17ad73eed6e73`（#6/#7 已合并）。修复候选不在 main，不能写成“最新版已正式交付”。
- 远程分支：`handoff/t1b-t2b-output-receipt` 已存在，HEAD `0ed0e9d95762d4588b6de411dc33eb8cc44b2f7c`
- 关联 PR：[#8](https://github.com/TheDeadly-cat/hakimiblackjack/pull/8) → `main`（利用已有 `pull_request` 触发；`handoff/**` 的 push 不会跑 CI）
- 该 HEAD 的 Actions：创建 PR 后开始 run（以 GitHub 当时记录为准）；创建 PR 前 count=0。main CI `34588429217` / 258 项只证明 `4f84595`，不能换签给 `0ed0e9d`。
- G1/G2/G3 对应路径已实现；审查者 19 项隔离检查通过，不是完整 Windows 281。开发者 281 在 `.local-evidence/`，未独立核实为 CI。
- 未执行：`0ed0e9d` 的 318 冷请求与 20 张截图（不得用旧回执换签）
- 下一单：T5 DAS 契约/独立参考；**不是** T3，不重做 #2–#7
- 回滚：共享 main 上禁止 `git reset --hard de1d095`。未合入前以 PR 分支为准；先保留未提交工作、用户库和 `.analysis`

## 源码身份
- 仓库：https://github.com/TheDeadly-cat/hakimiblackjack
- 工作目录：`C:\Users\Administrator\Documents\ChatGPT\blackjack\implementation-v0.2b1`
- 分支：`handoff/t1b-t2b-output-receipt`（远程已存在；本地因 git :443 常失败，跟踪信息可能仍显示 `origin/main`）
- 基线HEAD：`4f84595bfc933896586dab9af9d17ad73eed6e73`
- T1b：`159c3bf152e71b9f3d3f22b007c76d35086f31c0`（动作集合与收益支持）
- T2b：`c43c187bb672f5e31d872ed5cc31584a80bd4185`（构建回执根对象与唯一写探针）
- 本轮新HEAD：`0ed0e9d95762d4588b6de411dc33eb8cc44b2f7c`（文档提交；产品字节与 T2b 树一致）
- 工作树是否干净：交接摘要同步推送/PR 事实后以 `git status` 为准；`.local-evidence/` 不入库
- 相关PR：#2–#7 已在 main。本修复分支 PR [#8](https://github.com/TheDeadly-cat/hakimiblackjack/pull/8)。未授权不合入。

## 环境
- 操作系统/位数：Windows-11-10.0.26200-SP0，64-bit
- Python路径/版本：`C:\Users\Administrator\AppData\Local\Python\pythoncore-3.14-64\python.exe` 3.14.6
- Tk和SQLite版本：Tk 8.6 / Tcl 8.6.15；sqlite 3.50.4
- .NET编译器路径/版本：`C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe` 4.8.9221.0
- 是否真实执行native：是（门禁 native 焦点、分牌工作流随 discover、已校验产物复用）。审查包 semantics/回执探针为 mock 夹具，不是 C# 数值神谕。
- 是否真实执行GUI：discover 中的 Tk 回调有执行；`inspect_environment()` 真实 `Tk()`；20 张窗口截图未运行

## 本轮任务
- 原始问题：G1 普通停牌/补牌/加倍/投降的非法净收益仍标 `available`；G2 三张牌多返回的高 EV `double` 会成为推荐；G3 坏 `build.json` 根对象使预检崩溃；G4 交接记录仍写未推送、main `09bb60c`、下一单 T3。
- 实际读取的关键文件/函数：`split_service._validate_available_action`/`calculate_split`、`environment.inspect_environment`、`native_backend._build_native_locked`、审查包 `probes/test_service_semantics.py` 与 `probes/test_environment_receipt.py`
- 修改与原因：分牌前与分牌后统一要求后端动作集合等于 `legal_actions`；按动作/注额检查净收益支持（普通 stand/hit `{-1,0,+1}`，double `{-2,0,+2}`，晚投降仅 `-0.5`，天然 BJ 未分牌两张的 stand `{0,+1.5}`）；抽出 `parse_build_receipt` 供预检与 `build_native` 共用；写探针改为缓存父目录下本次唯一临时文件并只删本次创建的文件。`example()` 在十点明牌发第三张前补 peek，使审查包三张牌探针能在完整工程建快照（与真实录牌顺序一致）。
- 不在范围内的未修改项：`SplitEngine.cs`、DAS 生产求解、1e-10/1e-12、5 秒预算、进程层 Job 竞态重写、318/截图、T5
- 新增/调整的测试：`tests/test_split_output_guard.py` 保留原 26 项并增加动作语义 mock + native 正向保护（天然 BJ、T/J pending、三张仅 stand/hit）；`tests/test_split_environment.py` 增加 `[]`/`null`/字符串/`{}`/语法错误/正常对象与唯一写探针。Mock 与 native 分班标注。

## 验证（每条写真实执行情况）
cwd 均为 `C:\Users\Administrator\Documents\ChatGPT\blackjack\implementation-v0.2b1`。下表命令在工作树已含 T1b/T2b 源码时执行；当时 `git rev-parse HEAD` 仍显示基线 `4f84595`，因测试先于提交。提交后源码字节与该次运行一致。

| 命令 | 退出码 | 通过/失败/跳过/未运行 | 完整日志 |
|---|---|---|---|
| 审查包 `test_service_semantics.py`（修前） | 1 | 执行且失败（7 项：2 ok，4 FAIL，1 ERROR） | `.local-evidence/t1b-t2b-pre-fix-20260911-190625/semantics-pre.txt` |
| 审查包 `test_environment_receipt.py`（修前） | 1 | 执行且失败（6 项：3 ok，3 ERROR/`AttributeError`） | `.local-evidence/t1b-t2b-pre-fix-20260911-190625/environment-pre.txt` |
| 审查包 `test_service_semantics.py`（修后） | 0 | 执行且通过（7/7，mock） | `.local-evidence/t1b-t2b-20260911-191223/semantics-post.txt` |
| 审查包 `test_environment_receipt.py`（修后） | 0 | 执行且通过（6/6，mock 回执；不执行 C#） | `environment-receipt-post.txt` |
| `python scripts/check_environment.py --json` | 0 | 执行且通过（`ready=true`，合法 JSON） | `check-environment-json.txt` |
| `python scripts/check_environment.py` | 0 | 执行且通过（`ready=True`） | `check-environment.txt` |
| `python -m blackjack_lab.main --prepare-split` | 0 | 执行且通过（复用已校验产物） | `prepare-split.txt` |
| `python -m unittest tests.test_split_output_guard tests.test_split_environment tests.test_split_process -v` | 0 | 执行且通过（62/62；含 mock 语义与 native 正向保护） | `focus.txt` |
| `python -m unittest discover -s tests -v` | 0 | 执行且通过（281 tests，45.822s；main CI 258 + 本轮 23） | `unittest-discover.txt` |
| `python scripts/verify_review_handoff.py --output .../original-5-48-4` | 0 | 执行且通过（原 5/48/4，`passed=true`，max_abs_error≈2.22e-16） | `verify-review-handoff.txt` 与 `original-5-48-4/` |
| `python -m unittest discover -s docs/acceptance/n1-20260910-220300-9f6f68/original -p test_snapshot_shape.py -v` | 0 | 执行且通过（4/4） | `n1-original.txt` |
| `python -m compileall -q blackjack_lab tests scripts` | 0 | 执行且通过 | `compileall.txt` |
| `python scripts/verify_split_release.py` / 318 冷请求 / 20 张截图 | — | 未运行（本轮未改数学核心与请求预算；不得用旧回执换签） | — |

## 证据身份
- 修前证据：`.local-evidence/t1b-t2b-pre-fix-20260911-190625`
- 修后证据：`.local-evidence/t1b-t2b-20260911-191223`
- 已提交 hash：
  - `blackjack_lab/analysis/split_service.py` git blob `b2d96f71991e1bf5d8f03c6e4517c41afea82f3e`；sha256 `226bcc1a73f67b348ccf7c5a9ecc411190459191e67308d949ac2850e863dcf0`
  - `blackjack_lab/analysis/environment.py` git blob `4d20d5400190ec6bf867baa99e54a629545ab556`；sha256 `24f2001082bf25760c951bcfbcfe84447f2eeaba6ee7737af46016d3eb5899fd`
  - `blackjack_lab/analysis/native_backend.py` git blob `28009b390884a4ae1f7f78e5a9d57f14854f481c`；sha256 `e562ed1b42a22987a02acf6ce4f2d6fb795b7c4cf156fa73ffcd1fd55ac385f8`
  - `tests/test_split_output_guard.py` git blob `0e659e5b55bc2dfd5b00bb675021acf024e035e0`；sha256 `975dc2df5a0c3d22d23ffbeec07a6401a29b75935dd80b5f4a3f755e1797c52f`
  - `tests/test_split_environment.py` git blob `13ac50c00ffaa87d4602f85be28151952bd791a1`；sha256 `ba259ae9774d46fc1c13d1a15784237621169b02cba22a1f1b644c5a588a9f75`
  - `tests/test_analysis_integration.py` git blob `d201d7a70fd99e86224eacb08af18aaacff1a9f5`；sha256 `26a1680ff169579c6bddcf424e8fe9630e3576d763fb1e3630c26315a1adfe68`
  - 已校验产物未改：`SplitEngine.exe` binary sha256 `437e4016ceee36a531a2bc25c86e542658c2b31189c63ed3aabf4f91d299dc87`
- 历史证据是否保持不变：是（未改 `docs/acceptance`、未覆盖 T0–T2 `.local-evidence`）
- 外部CI与本地执行的区分：上表为本机 Windows 原生执行（探针为 mock）；CI 258 对应已合并 main `4f84595`，不能写成当前工作树结果。

## 兼容与风险
- 拒绝发布时清空 `actions` / `probabilities` / `highest_ev_action`；不静默改分布、不改容差、不重算 EV。坏回执与损坏 exe 保留，不删 `data/`，不安装、不提权。
- 未解决：T5 DAS；当前 HEAD 的 318/截图；Job 归属窄窗口仍只登记、未复现孤儿 `csc`。
- 回滚：不要在已共享的 main 上 `git reset --hard de1d095`。丢弃本轮未提交改动前先确认没有需要保留的本地文件；用户数据库与 `.analysis` 本轮未触碰。

## 下一单
- 唯一下一任务ID：T5（两手 DAS 契约与独立参考：每手注额、唯一加倍牌、合计 [-4,4]、分 A 禁 DAS、信息上界有「无 DAS」前提）
- 前置条件：T1b/T2b 本机相关测试已通过；生产 `SplitEngine` DAS 要等设计审查后的 T6
- 需用户或审查者决定的事项：PR #8 的 Windows CI 通过后是否授权合入 main
- 是否获准push/merge：已推送分支并开 PR；**未获准 merge**

---

# T2 时点历史（以下为当时原文，未改验收表）

更新时间：2026-09-11（本机 T2 完成后）
执行工具/模型：Cursor Grok 4.6（执行质量以本文件命令与日志为准，不由模型名称证明）
当前任务ID：T2
本轮允许文件：`blackjack_lab/analysis/native_backend.py`、`blackjack_lab/analysis/environment.py`、`scripts/check_environment.py`、`tests/test_split_environment.py`、`tests/test_split_process.py`、`blackjack_lab/main.py`、`README.md`、本交接记录。T2 未改 `split_service.py` / `SplitEngine.cs`。

## 源码身份
- 仓库：https://github.com/TheDeadly-cat/hakimiblackjack
- 工作目录：`C:\Users\Administrator\Documents\ChatGPT\blackjack\implementation-v0.2b1`
- 分支：`handoff/v02b1-result-validation`（从审查基线新建，未推送）
- 基线HEAD：`de1d095b3f739771578a35edcd8b61eb55869996`（0.2.0b1 / `codex/v0.2b1-split-analysis`）
- 本轮新HEAD：本提交（T1 输出门禁 + T2 环境预检；提交后以 `git rev-parse HEAD` 为准）
- 工作树是否干净：本提交纳入 T1/T2 源码、测试与交接记录；`.local-evidence/` 仍为本地证据、不入库
- 相关PR及实际base/head（交接包观察，本轮未改远程）：
  - main `09bb60c72c64363f02d3b27b818a49a254395af9`
  - #2 `948028db303533e2bd5c06d793c3f2d217305717` → main
  - #3 `e7cd8b8c6f638ce78dfcdf5d02a0690b17666b34` → N1
  - #4 `7c17b6e69e9ec1751c85d3ec9c9f7698d6ec83f8` → B1
  - #5 `82d627e031643c99091f7969e19f640b0c025b30` → B2
  - #6 `de1d095b3f739771578a35edcd8b61eb55869996` → B3
  - 查询时均为 open、merged=false。本轮未 merge、未 force-push。

## 环境
- 操作系统/位数：Windows-11-10.0.26200-SP0，64-bit
- Python路径/版本：`C:\Users\Administrator\AppData\Local\Python\pythoncore-3.14-64\python.exe` 3.14.6
- Tk和SQLite版本：Tk 8.6 / Tcl 8.6.15；sqlite 3.50.4
- .NET编译器路径/版本：`C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe` 4.8.9221.0
- 是否真实执行native：是（`check_environment.py`、冷编译/复用、进程 Job 清理、分牌数学/工作流/native 单测）
- 是否真实执行GUI：工作流单测中的 Tk 回调有执行；`inspect_environment()` 真实 `Tk()` 初始化；20 张窗口截图与 `verify_split_release.py` 未运行

## 本轮任务
- 原始问题：接手者可能把缺编译器/权限/产物损坏当成算法失败；冷编译取消是否残留 `csc` 此前未测，不能写成已复现事故。
- 实际读取的关键文件/函数：交接包 T2/H21/H22、`native_backend.build_native`/`_compile_source`/`_Job`、`tests/test_split_process.py`
- 修改与原因：新增本机预检（不安装、不改执行策略、不提权）；编译改走 kill-on-close Job；缺编译器/摘要不匹配给出中文失败且不发占位 EV、不删用户数据；取消/超时/工作进程死亡清理本请求编译器替身，不杀无关进程。首次编译与已 prepare 复用分开验证。
- 不在范围内的未修改项：`split_service.py`（T1 已改，本轮未再动）、`SplitEngine.cs`、概率公式、规则模板、1e-10/1e-12、5 秒预算、PR 链、用户数据库、318 基线材料
- 新增/调整的测试及契约依据：`tests/test_split_environment.py`（本机 ready、缺编译器仍可录牌、摘要不匹配保留文件、冷编译 vs 复用、CLI/`--check-environment`）；`tests/test_split_process.py` 增加编译超时清理与工作进程死亡不残留编译器替身，并把源码编辑拦截从 `subprocess.run` 改到 `_run_owned_process`

## 验证（每条写真实执行情况）
| 命令 | cwd | 退出码 | 通过/失败/跳过/未运行 | 完整日志 |
|---|---|---|---|---|
| `python -m compileall -q blackjack_lab tests scripts` | 仓库根 | 0 | 执行且通过 | `.local-evidence/t2-environment-20260911-144357/compileall.txt` |
| `python scripts/check_environment.py` | 仓库根 | 0 | 执行且通过（`ready=True`，未安装软件） | `check-environment.txt` |
| `python scripts/check_environment.py --json` | 仓库根 | 0 | 执行且通过 | `check-environment-json.txt` |
| `python scripts/check_environment.py --prepare` | 仓库根 | 0 | 执行且通过（已有产物，`prepare_seconds=0.001`，未再调用编译器） | `check-environment-prepare.txt` |
| `python -m blackjack_lab.main --check-environment` | 仓库根 | 0 | 执行且通过 | `main-check-environment.txt` |
| `python -m blackjack_lab.main --check` | 仓库根 | 0 | 执行且通过 | `main-check.txt` |
| `python -m unittest tests.test_split_environment tests.test_split_process -v` | 仓库根 | 0 | 执行且通过（13/13；首次因临时目录清理后断言失败，修复后再跑） | `t2-focus.txt`（保存的是修复后通过日志） |
| `python -m unittest tests.test_split_environment tests.test_split_process tests.test_split_output_guard tests.test_split_math tests.test_split_native_single tests.test_split_workflow tests.test_split_contracts -v` | 仓库根 | 0 | 执行且通过（70/70，22.998s） | `split-focus.txt` |
| `python -m unittest discover -s tests -v` | 仓库根 | 0 | 执行且通过（258 tests，44.389s；原 224 + T1 26 + T2 环境 6 + 进程新增 2） | `unittest-discover.txt` |
| `python scripts/verify_review_handoff.py --output .../original-5-48-4` | 仓库根 | 0 | 执行且通过（原 5/48/4，`passed=true`，max_abs_error≈2.22e-16） | `original-5-48-4-wrapper.txt` 与子目录 |
| `python -m unittest discover -s docs/acceptance/n1-20260910-220300-9f6f68/original -p test_snapshot_shape.py -v` | 仓库根 | 0 | 执行且通过（4/4） | `n1-original.txt` |
| `python scripts/verify_split_release.py` / 318 冷请求 / 20 张截图 | — | — | 未运行（T2 未改数学模型与请求预算；本轮未重跑完整交付包） | — |
| `probes/run_isolated_output_probe.py` | — | — | 未运行（交接说明：不得当作完整工程验收） | — |
| T3 合并 PR | — | — | 未运行（无合并授权） | — |

## 证据身份
- 新证据目录：`C:\Users\Administrator\Documents\ChatGPT\blackjack\implementation-v0.2b1\.local-evidence\t2-environment-20260911-144357`
- 源码/产物hash清单：
  - `blackjack_lab/analysis/native_backend.py` git blob `340b31730444efede7650dbcbebc6dadc696fcaa`；sha256 `7ccf88f6c180de72111ee4a5608916570a4cc15ff1ee50c74c179be45aef93b5`
  - `blackjack_lab/analysis/environment.py` git blob `5581dc0b19b4fcb83dc36a077a14b99701554478`；sha256 `1256f448539e1ceb5d991c05e11d7adea5e6510675396cb2bb684f8c65b8e51d`
  - `scripts/check_environment.py` git blob `49cdff8cecb5c953a2daed6c27c2a2f7ecd60d09`；sha256 `e99d44cfd228b8dabc5ec9431e49b19448c656069ebc3883ce0b00d9e1b00f3d`
  - `tests/test_split_environment.py` git blob `e70d61416c8241593ecc6b029048249eb86e70d4`；sha256 `d18cdc7ac7de2e6a6be03d49e079272f7dfe6d3a5daa66e7a2c08e12c3b555a6`
  - `tests/test_split_process.py` git blob `5fa86b8d375df7c1a3a842a0300224e47387d824`；sha256 `29d75ff69ce99aa6eefc7a112d203b9f4019770f36707dd07c0b72ba465e44cd`
  - T1 `split_service.py` 仍为 sha256 `2a3ba93343404c4ab7fc88a5c38f75bba5d9be6c5dfd4aea57dd00206d8cef12`（本轮未改）
  - 已校验产物：`blackjack_lab/.local-native/88e2cecf760d9075a0be59e082a4a3ddb71d499c3683a3466444ffc05c63c405/SplitEngine.exe` binary sha256 `437e4016ceee36a531a2bc25c86e542658c2b31189c63ed3aabf4f91d299dc87`
- 历史证据是否保持不变：是（未改 `docs/acceptance`、未覆盖 T0/T1 `.local-evidence`）
- 当前提交对应CI run/job/TESTED_HEAD：基线仍对应 GitHub run `34503837972` / job `102960977413` / `de1d095`。本轮本地测试不能冒充该 CI。
- 外部CI与本地执行的区分：上表均为本机 Windows 原生执行；CI 数字未抄作本轮结果。

## 兼容与风险
- 原事件/规则/快照兼容：环境失败发布 `failed` 并清空 `actions`/`probabilities`/`highest_ev_action`；不写占位 EV；不删除损坏产物或 `data/`。容差与预算未改。
- 未解决或未测问题：T3 PR 整合；318/截图完整交付包；T4 研究会话备份；T5 DAS 契约。冷编译残留此前未当作已复现缺陷；本轮新增测试通过，不回溯声称旧 HEAD 必有孤儿 `csc`。
- 权限/依赖问题：本机 Python/.NET 可用；预检 `installs_software=false`。`csc` 横幅在 UTF-8 模式下按 `errors=replace` 读取，避免 GBK 字节把预检打挂。
- 回滚步骤及新数据保护：`git reset --hard de1d095` 可回到审查基线（会丢掉本提交）。未触碰真实用户数据库。`.local-evidence/` 不入库。

## 下一单
- 唯一下一任务ID：T3（整合 N1—B4 依赖 PR 并验证最终 main）
- 前置条件：T0/T1/T2 本机相关测试已通过；**必须取得用户明确合并授权**
- 需用户或审查者决定的事项：是否推送 `handoff/v02b1-result-validation`；T3 合并授权仍为否
- 是否获准push/merge：否
