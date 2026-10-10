@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
echo Hakimi Blackjack 试用版 T1R4 - 联网安装脚本包
echo 需要现有 Windows x64 Python 3.14 / Tcl-Tk / .NET Framework。
echo 不包含应用源码压缩包或 Python 运行库；首次安装从固定 GitHub 提交下载源码。
echo.
set PYTHON_MANAGER_AUTOMATIC_INSTALL=false
set PYLAUNCHER_ALLOW_INSTALL=
set PYLAUNCHER_ALWAYS_INSTALL=
set PYLAUNCHER_DRYRUN=
set PYTHONHOME=
set PYTHONPATH=
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set PYTHONDONTWRITEBYTECODE=1
where py >nul 2>nul
if errorlevel 1 goto check_python
py -3.14 -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3,14) else 1)" >nul 2>nul
if errorlevel 1 goto check_python
py -3.14 -B "%~dp0install_trial.py" %*
set "CODE=%ERRORLEVEL%"
goto finish
:check_python
where python >nul 2>nul
if errorlevel 1 goto missing
python -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3,14) else 1)" >nul 2>nul
if errorlevel 1 goto missing
python -B "%~dp0install_trial.py" %*
set "CODE=%ERRORLEVEL%"
goto finish
:missing
echo 未找到现有 Python 3.14。请查阅 README_试用版.txt 的依赖说明。
echo 安装器不会自动下载或修改系统 Python；也可以用现有解释器的完整路径运行 install_trial.py。
set "CODE=2"
:finish
echo.
if not "%CODE%"=="0" echo 本次安装未完成，请保留上面的报错，不要删除旧数据。
pause
exit /b %CODE%
