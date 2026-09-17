@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
"%~dp0.venv\Scripts\python.exe" "%~dp0tools\check_dlss_runtime.py" --version auto --render
pause
