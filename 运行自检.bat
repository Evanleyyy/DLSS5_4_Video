@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
".venv\Scripts\python.exe" -m unittest discover -s tests -p "test_*.py"
if errorlevel 1 goto done
".venv\Scripts\python.exe" tools\check_dlss_runtime.py --version auto --render
:done
pause
