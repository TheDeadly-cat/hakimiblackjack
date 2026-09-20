@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
if exist ".local-evidence\rgb-corner-prototype\venv\Scripts\pythonw.exe" (
  start "" ".local-evidence\rgb-corner-prototype\venv\Scripts\pythonw.exe" -m blackjack_lab.main --quick
) else (
  python -m blackjack_lab.main --quick
)
