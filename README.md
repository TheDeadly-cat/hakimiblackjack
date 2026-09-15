# Hakimi Blackjack Lab — 单玩家两手顺序分析（含显式 DAS 模板）

**分牌前比较动作，分牌后逐张更新当前操作、原始投注的合计 EV 和净收益分布，并保存历史复盘。**

产品显示版本 `0.2.0b1`。规则模板、求解引擎是另一套身份：无 DAS 为 `v0.2b1-finite-two-hand-1`；两手 DAS 生产引擎为 `v0.2b2-finite-two-hand-das-2`。保留 Python、Tkinter 和 SQLite。没有多玩家 EV、再分或平台控制接口。识牌为可选功能：本地图片、录像和授权窗口捕获共用候选识别入口，训练模型需要显式选择，全部结果仍待人工确认；真实录像准确率尚未通过独立验收。两手 DAS **只在显式 V0.2b2 模板**下计算，不会把旧无 DAS 快照改写成新引擎。数学与工作流验证、目标机性能、CI 和交付证据分别报告；318 项 unittest、旧 b1 318 冷请求、DAS 336 矩阵不能互相换签。不把测试通过解释为获利保证。Windows DAS 336 门禁与干净运行副本的做法见 [V0.2b2 验收与交付](docs/V0.2b2-验收与交付.md)。

## 开始使用

辅助记牌开发版：双击 **Start_Assisted_Recording.cmd**，或在主窗口点击“悬浮记牌”。可用置顶小面板快速补牌、人工确认连续候选、回看原图及修正账本；默认不自动入账。操作方法、录像压力检查和尚未完成的全屏/人工效率验收见 [辅助记牌工作台](docs/ASSISTED_RECORDING_20260914.md)。

Windows 64 位 / Python 3.10+（含 Tkinter）。Python 主程序使用标准库；分牌数值程序另使用系统 .NET Framework 4 编译器和运行时，无新增 pip 运行依赖、API Key、网络服务或下载步骤。目标验收环境为 Windows 11 / Python 3.14.6；分牌加速器当前仅交付 Windows。

双击 **启动界面.bat**，或在项目根目录运行：

```powershell
python -m blackjack_lab.main
```

第一次可以导入 `fixtures/v02b1/split-eight-before.json`，然后点右侧“计算当前手牌”。还可导入 `split-eight-first-bust.json` 查看首手爆牌后继续第二手，或 `split-aces-ordinary-21.json` 查看分A的普通21结算期望。DAS 合成示例见 `fixtures/v02b2/`。这些都是标记为自建模拟器的数据，底牌仍未知；不是平台记录或投注建议。原单手示例仍在 `fixtures/v02a/`。

自己录入时：

1. 选 6／7／8 副，在“研究模板（新靴用）”选择 **V0.2b1 两手顺序分牌模板**，或 **V0.2b2 两手顺序分牌DAS模板**，再新建牌靴。旧模板仍保留原四手录牌规则，不会自动升级为两手或 DAS。研究模板不代表平台桌规。
2. 勾选本轮一个参与玩家，点“新开一轮”。仍可记录最多 7 位玩家，分析仅支持单参与玩家。
3. 录入两张玩家牌、庄家明牌和一张“暗牌”。庄家明牌为 A 或十点时，必须有实际的非 BJ 检查信息，再点“庄家检查底牌：确认非 BJ”。未知结果不要自行勾选。
4. 点右侧“计算当前手牌”。可选“自动”，在合格输入变化后自动发起新请求。窗口可以继续录入；新牌、撤销、纠错、换目标或换会话会让旧结果过期。辅助队列未确认、来源冻结或队列溢出不会改账本，但会把“当前”降为“截至已确认记录”。
5. 分牌前显示全部合法动作；分牌后选择“按顺序行动手”，先补齐并完成第一手，再给第二手第二张。21 或爆牌后自动切到下一手。DAS 模板下，非 A 分手两张可加倍，只补一张后该手闭合，再继续另一手。面板区分选中手和当前行动手；DAS 分开写当前已投入、立刻追加、此后可能再追加，不把分牌后总投入写成固定 2。不同实际顺序的记录保留，但会阻断该模型的精确分析。
6. 结果自动保存为独立快照。点“历史分析 / 复算”可读取原结果（只读，保留当时引擎身份），或按原事件前缀用**当前生产引擎**另建输入复算；重算生成新快照，不覆盖原判断。旧 b1 快照仍走无 DAS 引擎。T7 的 `das-1` 快照仍可展示，不能用旧引擎重算。

## 分析范围与含义

共同规则前提：有限不放回；6／7／8 副；完整新靴；明确初始零烧牌；S17；天然 BJ 3:2；美式底牌；A／十点明牌在玩家决策前明确非 BJ 检查；初始两张可加倍；无投降或晚投降。V0.2b1 模板增加同原始牌面、最多两手、无再分/无DAS/分A一张/分后无投降及首手完成后才发第二手第二张。V0.2b2 模板相同，但非 A 分手允许一次加倍。

两手共享剩余牌、未知底牌和庄家结算。当前手策略最大化合计净收益，不能提前知道第二手未来牌，也不能用独立卷积替代联合分布。无 DAS 时：分牌前已投入1，分牌另加1，分后总投入2；两手全赢+2、全输-2、一赢一输0。DAS 时每手还可再加1，合计净收益最大 ±4。首手爆牌已经计入已下注额，不能再次扣本金；分A的A+十点是普通21，且禁止 DAS。

初次分牌请求会在本地编译对应源码，编译时间也计入请求预算。可事先运行 `python -m blackjack_lab.main --prepare-split`。派生产物位于 `blackjack_lab/.local-native/<源码摘要>/`，使用前校验产物摘要；缺失编译器或编译失败会明确报告，仍可继续录牌。源码和编译器路径/参数随构建回执保留，不承诺不同机器产物逐字节相同。依赖预检（不安装软件、不改执行策略、不提权）：`python scripts/check_environment.py` 或 `python -m blackjack_lab.main --check-environment`。

- 正常未知底牌作为隐藏变量计算。非 BJ 检查既影响底牌候选，也影响下一张玩家牌的条件概率；不会把账面剩余数直接除以物理待发数。
- 补牌 EV 包含之后按新见牌在补／停之间继续决策，不是强制补一张后停牌，也没有偷看底牌再选策略。
- EV 是**相对原始一单位初始注的最终净收益**。加倍按 -2／0／+2 结算，返还本金不算盈利。每个支持动作还保存同口径收益分布。
- 旧四手模板仍仅作原单手分析，合法分牌缺少 EV 时显示部分比较，不静默截断为两手。全部适用动作完成且超过数值区分阈值时才标示模型下 EV 最高；强制发牌或等待庄家属于确定流程。
- 全部动作 EV 为负时会明确提示。较高可能只意味着少亏；当前手牌 EV **不等于下一轮开局优势**，本版不输出注额建议。发牌前开局优势是独立入口：精确穷举剩余不超过 16 张的组成，并计入庄家 blackjack；6／7／8 副整靴开局的精确穷举仍拒绝，不能靠改当前手牌标题冒充。离线固定策略蒙特卡洛见 `python scripts/run_fixed_policy_mc.py`（冻结停牌或玩具硬规则，不是精确最优 EV；须显式 `--surrender none|late`）。分析面板可填合成剩余点值（例如 `10,10,9,9,8,7`）做可穷举对照；未锁定牌靴时跟会话投降选项，默认「不支持」不能静默改成晚投降。整靴窗口扫描见 `python scripts/run_shoe_windows.py`；独立牌靴集合见 `python scripts/run_independent_shoes.py`（样本单位是牌靴，同靴各轮相关）；前三／六轮消耗对照见 `python scripts/run_round_windows.py`（其他座位不是独立样本，也不是多玩家 EV）；合成录牌误差见 `python scripts/run_observation_error.py`；组成区间见 `python scripts/run_composition_interval.py`（禁止平均牌靴）；策略耗牌对照见 `python scripts/run_policy_contrast.py`（同一洗牌起点、各自耗牌，不把一条实现路径当所有反事实策略的共同真值）；未使用留出见 `python scripts/run_unused_holdout.py`（缺声明即拒绝；具名 video_id 仍只是声明，不能把开发材料改成独立录像）。这些研究扫描脚本省略 `--surrender` 会退出，不能缺省晚投降。真实材料未勾选验收包见 `python scripts/prepare_acceptance_pack.py`（只哈希本机候选，软件不能写成通过）。大于 16 张记未支持，超时保持超时，零窗口是合法结果，不是可靠优势声明。动作结果同时给出净赢／打和／净亏概率与净 EV；赢的次数更多不是净优势的充分条件。
- 研究模板不能导出为真实桌规则档案。主窗口可载入带来源／版本／核对日期的档案，但当前没有已验收的真实赌场或平台桌。漏记且发生在后续动作之前时，用「插入漏牌（回溯修复）」预演后缀后一次追加；确认时间是现在，历史前缀看不到后来插入的牌。全屏验收清单与操作者对照可以导出，空证据或未配对真人试验不能把自动提示改成默认。
- 未知起靴、未知初始烧牌数、漏牌、待确认牌或未支持规则会阻止相应精确分析。旧规则中没有初始烧牌数字，不会自动当零。
- H17、6:5、非零未知烧牌、其他底牌规则、其他分牌顺序、再分和多玩家分析仍不支持；未声明的旧四手 DAS 组合也不是本契约。保留真实记录，不套用研究模板代算。

两手规则、不可变输入、独立参考和预定性能门槛见 [V0.2b1 数学与性能契约](docs/V0.2b1数学与性能契约.md) 与 [V0.2b2 DAS 契约](docs/V0.2b2-DAS契约.md)。DAS 验收命令见 [V0.2b2 验收与交付](docs/V0.2b2-验收与交付.md)。原单手定义仍见 [V0.2a 数学契约](docs/V0.2a数学契约.md)，完整路线见 [调整版r1](docs/planning/开发大纲调整版-r1-20260910.md)。

## 记录、保存与恢复

- 默认数据库 `data/blackjack_lab.db`；分析旁路目录为 `data/blackjack_lab.db.analysis/`。也可使用 `python -m blackjack_lab.main --db "D:\Records\lab.db"` 指定其他文件。
- 原事件继续使用 SQLite schema 2。分析写失败会单独提示，可重试；不会回滚已经提交的牌面事件。
- 旧单手快照仍可读，两手输入/结果使用独立格式，复算另存。坏 JSON 按文件隔离并显示文件名与原因，不删除坏文件；最小旧存储信封只显示元数据，不补造 EV。
- “已提交但界面刷新失败”与“事件未提交”分开提示。前者请刷新或重启，不要重复录牌。
- 新记录成功提交时立即撤下旧当前分析，不依赖界面重绘。历史复算明确标注原时点，可以独立完成。
- “结束本轮（未结算）”分别填写原因与观察完整性，默认未知。已知漏录／未知会持续阻止同靴后续精确分析；明确完整但仅暂不结算的轮次可以保留资格。正常“结束本轮并结算”代表确认本轮牌面记录完成。
- 旧结束事件缺少观察完整性字段时保留原文，按未知处理；可在核实后对结束事件追加观察状态纠错。结构性缺牌不能靠声明完整消除。详见 [增量契约](docs/V0.2a观察与生命周期契约.md)。
- 取消计算与关闭“自动”均会清除排队回调；取消后同一输入不会因刷新自行重启，新的输入或显式操作才会重新计算。
- 导出 JSON/CSV 使用原子替换，写到一半失败时保留原文件；拒绝导出到活动数据库、数据库别名或 SQLite 文件。
- CSV 展示列中可能触发公式的文本会转义，`event_json` 保留原始审计内容。旧摘要 CSV 只读，不能假装完整导入。
- 旧非法记录可在“旧会话诊断”中只读检查和原样导出，不自动改正或作为正式分析输入。
- 稳定备份请在关闭程序后同时复制数据库和同名 `.analysis` 目录。“备份数据库”按钮只备份 SQLite，不包含旁路分析目录。
- 键盘：0=10，A/J/Q/K 录牌，T=十点未细分，X=庄家暗牌，G/B/V=停牌/加倍/分牌，Ctrl+Z 撤销。输入框里不会触发录牌快捷键。

## 验证和开发

```powershell
python scripts/check_environment.py
python scripts/probe_capture_environment.py
python -m unittest discover -s tests -v
python -m blackjack_lab.main --check
python -m blackjack_lab.main --check-environment
python -m compileall -q blackjack_lab tests scripts
python scripts/verify_review_handoff.py
python scripts/split_benchmarks.py
python scripts/das_benchmarks.py
python scripts/make_portable_copy.py --require-clean
python scripts/verify_das_release.py
python scripts/fetch_possibly_wrong.py
python scripts/compare_possibly_wrong.py --output .local-evidence/external-pw-<唯一目录>
```

后两步下载并运行 GPL 的官方 `strategy.exe`，**不是产品的一部分**，也不进入 CI。缺少该二进制时 unittest 仍应通过。做法见 [V0.2b2 外部对照](docs/V0.2b2-外部对照.md)。

可选识牌（未安装时不影响手动录牌与分析）：

```powershell
python -m pip install -r requirements-vision.txt
python scripts/vision_demo.py fixtures/vision/synthetic-v1/smoke/all13.png
python scripts/vision_demo.py --gui
python scripts/vision_benchmarks.py
python scripts/verify_vision_v03a.py --rebuild-holdout
```

主窗口「识牌核对」可打开本地图/旁观录像、选择样式与训练模型，也可从「选择窗口实时预览」选定 WGC 来源。确认/改正/拒绝后经控制器入账，未确认时账本不变，匹配度不是正确概率。主程序内的录像与窗口预览均可「冻结识别帧并核对」，原图、选中位置和全部可滚动裁片一起进入人工页；可将候选明确关联到同一张已记录的牌，不再次扣牌，并从保存快照恢复关联；[使用步骤与实机核对](docs/REALTIME_MANUAL_HANDOFF_20260914.md)。另有 `scripts/realtime_preview.py` 持续 Tk 演示，共用现有最新帧入口，支持真实 1 倍速录像及授权 WGC 窗口，自动显示候选、短时稳定值和过期状态；它不自动记账。模型、来源、ROI 切换会撤回旧候选。本机已有私有素材与模型时可双击 `Start_Realtime_Preview.cmd`，启动方法及旧／RGB 实测见 [实时启动与当前回归](docs/WGC_AND_CURRENT_REGRESSIONS_20260914.md)。RGB 未通过替代门槛，旧方案也仍有误收；不能宣称可靠秒级识牌或只识别上角已经全面验收。

后续[独立小 CNN 分类对照](docs/RANK_CNN_EXPERIMENT_20260914.md)已完成一次固定训练、原图与 1 倍速回放。裁片结果有改善，但自动框错认增加，正确稳定事件由 9/31 降至 7/31，因此保持显式实验选项，不默认替代旧模型。

另有[原生分块输入实验](docs/RGB_NATIVE_CONTEXT_20260914.md)：沿用同一检测器权重，以训练时的 320×320 原生网格推理。三轮实际回放的正确稳定事件为整图＋HOG 9/31、分块＋HOG 14/31、分块＋CNN 15/31；错误、下角重复和身份碎片仍存在。现有启动器已加入「RGB 原生分块」选项，须手动选择。

[方向监督修正实验](docs/RGB_ORIENTATION_SUPERVISION_20260914.md)另存 11 个下角正例的用途修正及 53 个下角负例，原人审不改写。固定 HOG 的两轮真实回放中，正确稳定从 14/31 增至 16/31，额外稳定代表中的下角和背景误收减少，但仍有漏检、拒识及背景误收。可用 `Start_Orientation_Experiment.cmd` 显式查看该实验模型，尚未通过可靠秒级识牌验收。

[分类审核接入与 CNN 更新对照](docs/CLASSIFIER_REVIEW_UPDATE_20260914.md)将已完成补充审核和当前上角选择连接为 296 个分类训练裁片，原记录及旧权重保留。更新 CNN 的静态收益未转化为实时收益：固定对照正确稳定由 16/31 降至 13/31、错认增加，因此未替换启动器中的原 HOG。

既有人工流程和验收边界见 [V0.3e 审查整改](docs/vision/V0.3e-审查整改验收.md)，操作见 [指定模型与 CLI](docs/vision/V0.3e-指定模型与CLI.md)。原 [V0.3e 训练留出记录](docs/vision/V0.3e-标注训练留出集.md) 保留为历史开发记录，其中标签和分数不构成独立真实验收。

原 V0.2a 回归与截图验收脚本（不能代替新增分牌工作流/性能验收，截图使用可选开发依赖 Pillow）：

```powershell
python -m pip install -r requirements-qa.txt
python scripts/verify_release.py
```

每次在 `.local-evidence/acceptance-v02a-时间-随机标识/` 创建独立目录，不覆盖原报告。包含真实命令、退出码、源文件哈希、独立小牌靴有理数验证、禁止 socket 网络连接的独立进程工作流、固定种子模拟、目标机延迟/内存和真实窗口截图。`--output` 可指定新的目录，已存在目录会被拒绝。

新分牌冷请求脚本 `scripts/split_benchmarks.py` 固定300个无 DAS 分牌前案例及18个动态前缀。DAS 专属矩阵是 `scripts/das_benchmarks.py` 的 336 条（300 分牌前 + 36 动态），与 unittest 项数和旧 b1 318 冷请求都不是同一份证据。5秒硬请求预算和2秒p95目标不随结果修改。

原 V0.1/V0.2a 文档、首轮失败输出与旧回执保留为历史证据。当前能力以本 README 和本版验收回执为准。许可证边界见 `NOTICE.md`。没有平台控制、自动下注或云端识牌；真实媒体、标签、模型和用户账本仅保存在本机。

`review_tests/` 保留原增量审查包的4项交接用例、48场景脚本和来源摘要。`verify_review_handoff.py` 不改写原脚本，使用当前完整工程执行并在唯一目录保存新输出；原数学5项必须仍存在、实际执行并通过，后续新增数学测试允许存在并单独统计，48个场景不计作48项新增 unittest。回执写出 `failed_conditions`。完整验收与 Windows CI 均包含这一步。
