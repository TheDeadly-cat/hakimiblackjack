# Hakimi Blackjack 离线试用版 T1O3

双击安装 EXE，会显示可见安装窗口。可直接填写完整安装文件夹，或点“选择文件夹”选择桌面、其他磁盘或文件夹；浏览选择所在位置时自动新建 `Hakimi Blackjack T1O3` 应用文件夹。安装中显示核验、解压、运行环境检查及快捷方式阶段，完成后提供“启动 Blackjack”和“打开安装文件夹”。不能在安装写入过程中关闭窗口。

## 自选位置与多目录安装

每个自定义安装文件夹都是独立实例，可在多个不同位置重复安装，互不共用录牌记录。例如选择 `D:\Games\Hakimi Blackjack T1O3` 后：

- 程序在该文件夹的 `app` 中。
- 录牌资料在 `data` 中。
- 安装日志、程序回滚和备份分别在 `install-logs`、`rollbacks`、`backups` 中。
- 桌面快捷方式包含文件夹名与稳定实例短号，更新同一实例复用原入口。

首次安装使用新文件夹或空文件夹，已有其他文件的未识别目录不覆盖。同目录已认证安装可以更新，保留原资料和旧程序回滚。卸载只删除本实例程序，保留资料、容器身份和其他实例；卸载后重装如需采用保留记录，勾选“重新安装时使用此目录保留的记录”。安装位置绑定绝对路径，安装后不能直接移动目录。

原默认安装继续兼容：程序 `%LOCALAPPDATA%\HakimiBJTrial\T1O3-1848283`，资料 `%LOCALAPPDATA%\HakimiBJTrialData\T1O3-1848283`。安装窗口默认显示该位置；改选其他目录时生成独立实例。原版本和记录不自动搬移。

命令行可显式使用 `--yes --install-dir "D:\Games\Hakimi Blackjack T1O3"`，`--yes`不显示安装窗口；省略 `--install-dir` 使用原默认安装。`--reuse-data`显式采用卸载后保留的同实例资料。

## 日常使用与维护

安装 EXE 内含 Python 3.14.6 x64、Tcl/Tk 8.6.15、固定源码和预编译数值程序，无需下载 Python 或源码。Windows 仍需要 .NET Framework 4 运行时；安装器不提权、不安装系统组件，程序未签名。正常关闭应用后才能更新或备份。

正常启动直接续录。A–9/T/0 录点值，主注自动分析开启。BCLC 日常完整八副新靴、零烧牌，起手录齐自动登记原未知底牌；A等待阶段显示含BJ分支的EV1/EV2和净盈利概率，补牌指标注明非BJ后。0/J/Q/K/T记录实际庄家BJ，其他点值或合法 - / * 一次保存实际非BJ通知与第一有效玩家操作，一次Back撤回。已记录非BJ后误按十点只拒绝该牌，不锁住录牌。真正写入失败和提交未知保留原输入，不自动重试。

Perfect Pairs／21+3实时研究关闭，旧配置与历史保留。多人评估采用其他玩家之后不再补牌的条件，天然BJ只显示终局评估。未知靴或观察保持UNKNOWN。拼音可能截获字母，可用0表示十点、1表示A。

程序目录提供 `START_TRIAL.cmd`、`BACKUP_DATA.cmd`、`VERIFY_SOURCE.cmd`、`UNINSTALL_TRIAL.cmd`。备份包含全部附属资料并核对摘要，应用恢复验收另行记录。卸载通过实例日志目录内的外部运行器完成，保留资料；快捷方式与外部运行器另外处理。

启动逐项校验完整运行库、源码SHA256、Git blobs和固定源码树。固定业务源码 `8b89f44826f68683f5e5a17e3f6e7f6e9e9f0a4a`，树 `9f634b97d8c1a7baafaf631d22e83d89454fee2f`，1563文件。相对e24cc7d只修复spawn故障注入的测试夹具，生产代码无差异；原两次CI失败保留，最新CI按相同提交单独记录。

## 验收与构建

自选布局、实例绑定、拒绝未识别非空目录、同实例回滚、可见Tk进度和维护隔离均做定向验证。冻结EXE在多个目录安装、启动、备份及卸载属于独立产物证据，不等于普通权限、干净无Python电脑或断网验收。原五秒计算预算、精度和主注分析不变，大历史实时性能仍未通过；保持试用版和Draft PR，不自动合并。

使用隔离Python3.14.6和PyInstaller6.22.3及固定轮子，构建时需要.NET Framework编译器。源码按固定Git blobs导出并核对完整树；包装源码变化时拒绝签署构建。运行依赖许可证随包提供。

```powershell
build-python.exe -I -B packaging/windows_offline_trial_o3/build_runtime.py --output NEW_RUNTIME_BUILD --wheelhouse FIXED_WHEELHOUSE
build-python.exe -I -B packaging/windows_offline_trial_o3/build_setup.py --runtime NEW_RUNTIME_BUILD/dist/HakimiBlackjackTrialT1O3 --output NEW_SETUP_BUILD
```
