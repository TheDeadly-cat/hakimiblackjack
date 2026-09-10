@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
python -m blackjack_lab.main
set task_exit=%ERRORLEVEL%
pause
exit /b %task_exit%
