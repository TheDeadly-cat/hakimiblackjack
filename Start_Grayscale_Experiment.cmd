@echo off
setlocal
set "LAB_ROOT=%~dp0"
set "LAB_PY=%LAB_ROOT%.local-evidence\rgb-corner-prototype\venv\Scripts\pythonw.exe"
set "LAB_MODEL=%LAB_ROOT%.local-evidence\reviewed-rank-update-20260914\run-1\model"
set "LAB_DETECTOR=%LAB_ROOT%.local-evidence\rgb-orientation-supervision-20260914\training-1"
set "LAB_STYLE=%LAB_ROOT%.local-evidence\continuous-review-20260913-21-12\runtime-inputs\style.json"
set "LAB_VIDEO=%USERPROFILE%\Videos\NVIDIA\Desktop\Desktop 2026.09.13 - 16.33.19.08.mp4"
if not exist "%LAB_PY%" goto missing
if not exist "%LAB_MODEL%\model.npz" goto missing
if not exist "%LAB_DETECTOR%\detector.pt" goto missing
if not exist "%LAB_STYLE%" goto missing
if not exist "%LAB_VIDEO%" goto missing
start "" /D "%LAB_ROOT%" "%LAB_PY%" "%LAB_ROOT%scripts\realtime_preview.py" --video "%LAB_VIDEO%" --style "%LAB_STYLE%" --model "%LAB_MODEL%" --detector "%LAB_DETECTOR%" --rgb-tiles --rgb-otsu --initial-method rgb-tiled-otsu --fps 8 --last-frame 11040 --evidence-limit 4096 --geometry 2000x1120+20+20
exit /b 0
:missing
echo This experiment needs the explicit local CNN, detector, style and video.
echo See docs\HOG_AND_GRAYSCALE_UPDATE_20260914.md. This remains an unaccepted experiment.
pause
exit /b 1
