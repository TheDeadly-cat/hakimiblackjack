@echo off
setlocal
set "LAB_ROOT=%~dp0"
set "LAB_PY=%LAB_ROOT%.local-evidence\rgb-corner-prototype\venv\Scripts\pythonw.exe"
set "LAB_MODEL=%LAB_ROOT%.local-evidence\review-completion-20260913-003511\upright-training-run\model"
set "LAB_DETECTOR=%LAB_ROOT%.local-evidence\rgb-corner-prototype\training-2-background"
set "LAB_STYLE=%LAB_ROOT%.local-evidence\continuous-review-20260913-21-12\runtime-inputs\style.json"
set "LAB_VIDEO=%USERPROFILE%\Videos\NVIDIA\Desktop\Desktop 2026.09.13 - 16.33.19.08.mp4"
if not exist "%LAB_PY%" goto missing
if not exist "%LAB_MODEL%\model.npz" goto missing
if not exist "%LAB_DETECTOR%\detector.pt" goto missing
if not exist "%LAB_STYLE%" goto missing
if not exist "%LAB_VIDEO%" goto missing
start "" /D "%LAB_ROOT%" "%LAB_PY%" "%LAB_ROOT%scripts\realtime_preview.py" --video "%LAB_VIDEO%" --style "%LAB_STYLE%" --model "%LAB_MODEL%" --detector "%LAB_DETECTOR%" --rgb-tiles --initial-method baseline --fps 8 --last-frame 11040 --evidence-limit 4096 --geometry 2000x1120+20+20
exit /b 0
:missing
echo This local demo needs the reviewed models, optional environment, style and source video.
echo No private model or video is downloaded automatically.
echo See docs\WGC_AND_CURRENT_REGRESSIONS_20260914.md for the explicit CLI inputs.
pause
exit /b 1
