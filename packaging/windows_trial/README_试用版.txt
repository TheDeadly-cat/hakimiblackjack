Hakimi Blackjack 试用版 T1R2 · af69ddb
====================================

这是联网／源码型安装脚本包，需要已有 Python 3.14 x64（含 Tcl/Tk）
和 .NET Framework 编译器。不是自带运行库的离线 EXE。
安装器不提权、不安装系统组件、不改 PATH。

本包是 T1-ce9c8bd 的独立修订版。原 T1 ZIP 和失败日志保留。
应用固定到 af69ddb3111b93e43bfbf01f4aa8c249be166b7a，包含纠错下拉框
焦点修复；包装层修复备份连接关闭和 Windows 合成 Git 测试夹具。
主注自动开启，Perfect Pairs / 21+3 自动研究关闭。未增加桌规、
识别训练、抓取或自动下注。实时性能验收仍未通过。

安装：
1. 完整解压 ZIP 到本地目录。
2. 双击 INSTALL_TRIAL.cmd，核对提示后输入 INSTALL。
3. 安装预检通过后，用桌面“Hakimi Blackjack 试用版 T1R2”或
   安装目录中的 START_TRIAL.cmd 启动。

程序：%LOCALAPPDATA%\HakimiBJTrial\T1R2-af69ddb\
资料：%LOCALAPPDATA%\HakimiBJTrialData\T1R2-af69ddb\
备份：%LOCALAPPDATA%\HakimiBJTrialBackups\
日志：%LOCALAPPDATA%\HakimiBJTrialInstallLogs\

本版新建独立程序和资料目录，拒绝覆盖已安装的同一版。
不读取或自动导入 T1、日常版或其他版本的数据。基础 Python 必须
保留；不要移动安装目录和虚拟环境。源码仅从固定 GitHub 提交下载，
全树校验通过后才执行，不跟随 main。

若 py/PATH 未找到现有解释器，可用 Python 3.14 的完整路径运行
install_trial.py。安装器支持 --source-zip 指定同一固定提交的 ZIP，
或将其命名为 source-af69ddb.zip 放在安装脚本旁；同样须通过全树校验。

BACKUP_DATA.cmd：正常关闭后输入 BACKUP，复制全部本版资料并核对摘要。
摘要一致仅说明复制通过，应用恢复须在独立目录另行验证。
VERIFY_SOURCE.cmd：检查固定源码；发现变化停止，不覆盖修复。
UNINSTALL_TRIAL.cmd：正常关闭后输入 UNINSTALL，只删除本版程序目录，
保留全部数据，不卸载基础 Python。桌面快捷方式另行移除。
同版数据保留后重新安装需要明确提供 --reuse-trial-data。

输入使用 A–9、T 点值；数字键 1=A、0=T，小键盘相同。
“待保存／需核对”时不复用旧建议，不自动重复录同一张牌。
大历史连续输入可能明显延迟；未测得安全历史上限，不能保证收益。

随包 VALIDATION.json 是打包时的检查范围。安装成功标记仅证明本机
核心自检、.NET 准备、环境检查和 Tk 空窗口通过，不证明完整应用、
普通权限、原生键盘、恢复或实时性能通过。后续 Windows 操作结论
单独保存在与 ZIP 摘要绑定的验收记录中。未制作离线 EXE/MSI；
未签名，不声明 SmartScreen 信誉。未合并仓库或发布 Release。

T1R1 在切换研究工作台时会丢失窗口试用标识；其包和验收记录保留。
T1R2 从恢复提示阶段开始，并在切换各个视图后，持续保留试用标识。
