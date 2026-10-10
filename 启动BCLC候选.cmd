@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "blackjack_candidate_python=C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if not exist "%blackjack_candidate_python%" (
    echo 未找到本机已验证的 Python 运行时，请保留候选目录并核对运行环境。
    pause
    exit /b 1
)
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
"%blackjack_candidate_python%" -m blackjack_lab.main --db "%~dp0data\bclc-candidate.db" --table-mode bclc
set "blackjack_candidate_exit=%ERRORLEVEL%"
if not "%blackjack_candidate_exit%"=="0" pause
exit /b %blackjack_candidate_exit%
