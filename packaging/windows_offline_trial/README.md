# Hakimi Blackjack 离线试用版 T1O1

业务源码固定为 `3ec2006e82c4bdbc8da01bb0e1212b64c9111d2c`，完整 Git 源码树为 `7fc1e8bb64c5bc4a783941fee536f394acf1fff7`。这是独立的试用候选，不替换日常版，不合并或发布仓库。

日常 A–9/T 点值录牌和主注自动分析开启，Perfect Pairs／21+3 实时研究关闭。保留旧配置、边注历史和精确牌面记录。BCLC 未核实桌规仍为待确认。

## 安装及维护

安装 EXE 内置 Python 3.14.6 x64、Tcl/Tk 8.6.15、固定源码和预编译数值程序，不在目标机下载源码或调用 Python 安装器。Windows 仍需要 .NET Framework 4 运行时；安装器不提权、不安装系统组件。程序未签名，无 Python 的干净电脑和断网电脑仍须实际验收。

双击 `Hakimi_Blackjack_T1O1_Offline_Setup.exe`，阅读试用提示后确认安装。安装只接受新程序目录，不覆盖现有版本；非空数据目录默认拒绝复用。安装后的程序不能直接移动。

程序目录为 `%LOCALAPPDATA%\HakimiBJTrial\T1O1-3ec2006`，资料目录为 `%LOCALAPPDATA%\HakimiBJTrialData\T1O1-3ec2006`。桌面入口和窗口标题都包含“离线试用版 T1O1”。也可使用程序目录中的 `START_TRIAL.cmd`。

先正常关闭试用程序，再运行 `BACKUP_DATA.cmd`。它复制数据库、配置、日志和全部附属资料，并核对复制前后的文件摘要。成功提示给出备份位置；文件摘要通过不等于应用恢复验收通过。

`VERIFY_SOURCE.cmd` 核验完整运行库和固定源码。`UNINSTALL_TRIAL.cmd` 使用程序目录之外的运行器卸载本版本，保留全部试用资料和历史结果。桌面快捷方式及保留的外部卸载运行器需要另外处理。安装、维护和故障记录放在 `%LOCALAPPDATA%\HakimiBJTrialInstallLogs`，失败现场不会自动删除。

命令行批量验收可显式使用 `--yes`：安装 EXE、`BACKUP_DATA.cmd --yes`、`VERIFY_SOURCE.cmd --yes`、`UNINSTALL_TRIAL.cmd --yes`。卸载仍只允许处理绑定的本版本程序目录。明确要继续使用原有试用资料时，安装 EXE 同时传入 `--reuse-data`；默认不会导入日常数据库。

## 验收边界

打包、安装预检、源码 CI、完整应用链路、原生键盘／输入法、普通权限、断网、无预装 Python、备份恢复和实时性能是独立验收。安装成功标记只确认冻结环境、源码资源、按序录牌子进程和快捷方式预检，不代表后面这些验收完成。

大历史连续录牌下的最新判断仍存在明显滞后。原预算、精度和主注分析保持不变；本候选不包含 PR #43／#44 的实验优化。保存结果未知时必须核对原资料，不自动重试同一张牌。尚未全部收尾，不能称为正式实时版本。

早期构建 r3 的首次新牌靴操作出现算法源码资源缺失：牌靴已保存，界面未同步。原构建、失败资料和备份保留，不能交付；后续构建补齐冻结模块路径下的固定源码，并增加目标机资源预检。

## 构建

使用隔离的 Python 3.14.6 x64 构建环境和 PyInstaller 6.22.3。固定构建轮子和摘要另行保留，不修改全局 Python 或已安装试用环境。构建时需要 .NET Framework 编译器；目标机运行使用源绑定的预编译程序。

```powershell
build-python.exe -I -B packaging/windows_offline_trial/build_runtime.py --output NEW_RUNTIME_BUILD --wheelhouse FIXED_WHEELHOUSE
build-python.exe -I -B packaging/windows_offline_trial/build_setup.py --runtime NEW_RUNTIME_BUILD/dist/HakimiBlackjackTrialT1O1 --output NEW_SETUP_BUILD
```

每次必须使用新输出目录。源码由固定 Git blobs 导出，完整树校验后编译；打包模块来源、运行时算法源码资源、包装输入和最终文件摘要分别保留。包装源码在构建期间改变时拒绝签署该轮构建结果。构建时附带 Python、Tk/Tcl 和 PyInstaller 的许可证材料；安装器不会访问这些下载地址。
