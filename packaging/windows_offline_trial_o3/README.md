# Hakimi Blackjack 离线试用版 T1O3

固定业务源码 `e24cc7d795b9b5951c714781bf034583a854ba1f`，源码树 `e91856ac3dfa2e6ba94ed65000b7c43d421d52f1`，1563 个文件。同一 T1O3 入口与资料目录更新，兼容实际已安装的 13716e8 及原有兼容版本，保留旧程序回滚；不自动合并 Draft PR 或正式发布。

正常启动直接续录，不询问是否启用旧记录，也不弹常规试用提醒。没有牌靴先新建，尚未开轮点“开始本轮”。A–9/T/0 录点值，主注自动分析开启。一次输入连带自动暗牌或 A 非 BJ 通知时，Backspace / Ctrl+Z 撤回整次输入并恢复原位置。Perfect Pairs／21+3 实时研究关闭，旧配置、记录及精确牌面保留，底部不重复提示。

BCLC v2 日常新建靴按用户声明的完整八副、零烧牌记录。初牌录齐自动登记未知原底牌，只有 A 等待实际 BJ 通知。等待期间自动计算庄家 BJ 概率、含 BJ 结算分支的 EV1/EV2 和净盈利概率；补牌指标注明“非 BJ 后”。计算不会写入非 BJ 事实。实际收到通知后，0/J/Q/K/T 记录庄家 BJ；其他点值或合法 - / * 将非 BJ 通知与第一有效玩家的实际操作一次保存、一次撤回。拼音可能截获字母，0 表示十点、1 表示 A。多人计算保留其他玩家之后不再补牌的条件，天然 BJ 只显示终局评估。

已记录非 BJ 后误按十点底牌属于明确的输入拒绝，不保存测试牌、不锁住录牌或 Back。真实写入失败、回执未知及未执行队列仍保留原输入，需严格复核，不自动重试。导入未知靴、未核实观察和草案桌规仍保留 UNKNOWN。此前构建与失败证据保留于本地档案，当前验收以固定提交和安装产物为准。

## 安装及维护

安装 EXE 内置 Python 3.14.6 x64、Tcl/Tk 8.6.15、固定源码与预编译数值程序，不在目标机下载源码或 Python。目标 Windows 仍需要 .NET Framework 4 运行时；安装器不提权、不安装系统组件，程序未签名。

正常关闭应用后运行 `Hakimi_Blackjack_T1O3_Offline_Setup.exe`。同版本更新复用原资料目录和快捷方式，旧程序保留到 `%LOCALAPPDATA%\HakimiBJTrialRollbacks`；失败恢复旧程序并保留失败副本。未识别目录不会被覆盖，默认不导入其他版本的数据库；没有对应程序的非空资料目录须显式指定复用。

- 程序：`%LOCALAPPDATA%\HakimiBJTrial\T1O3-1848283`
- 资料：`%LOCALAPPDATA%\HakimiBJTrialData\T1O3-1848283`
- 入口：桌面现有“离线试用版 T1O3”或程序目录 `START_TRIAL.cmd`
- 安装维护日志：`%LOCALAPPDATA%\HakimiBJTrialInstallLogs`

不能直接移动安装目录。启动逐项核对完整产物、源码 SHA256、Git blob 与固定源码树，一次源码读取完成两类校验，不依赖时间戳或持久缓存。

正常关闭后运行 `BACKUP_DATA.cmd`，复制数据库、配置、日志和全部附属资料并核对摘要。复制成功与另目录应用恢复验收分开。`VERIFY_SOURCE.cmd` 校验运行库和固定源码。`UNINSTALL_TRIAL.cmd` 通过程序目录外的运行器卸载本版本，保留资料、历史和基础系统组件；快捷方式和外部卸载运行器另行处理。维护工具可显式传入 `--yes`，安装器显式 `--reuse-data` 才允许采用无对应程序的既有资料。

## 验收与构建

本轮定向验证覆盖 A 检查前真实后台计算、混合分布与保存身份校验、误按拒绝、完整撤回和 Tk 显示。最终 EXE 小历史操作与实际同入口更新另存证据，不代表完整 Windows 验收。源码 CI、普通权限、无预装 Python 干净机、断网、输入法、完整故障恢复及实时性能分别记录。

五秒计算预算、精度和主注分析保持不变。旧 10k 历史测量仍未满足实时验收；不因打包或定向检查通过改称正式实时版本。

使用隔离 Python 3.14.6 x64 与 PyInstaller 6.22.3，固定轮子及摘要保留，不修改全局 Python。构建需要 .NET Framework 编译器。每次用新输出目录，固定 Git blobs 导出并验证完整树后编译；包装源码变化时拒绝签署构建结果。安装包附带运行依赖许可证。

```powershell
build-python.exe -I -B packaging/windows_offline_trial_o3/build_runtime.py --output NEW_RUNTIME_BUILD --wheelhouse FIXED_WHEELHOUSE
build-python.exe -I -B packaging/windows_offline_trial_o3/build_setup.py --runtime NEW_RUNTIME_BUILD/dist/HakimiBlackjackTrialT1O3 --output NEW_SETUP_BUILD
```
