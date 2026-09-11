# 项目接手状态

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
