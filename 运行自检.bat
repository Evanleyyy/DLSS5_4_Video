@echo off
chcp 65001 >nul
cd /d "%~dp0"
".venv\Scripts\python.exe" -m unittest discover -s tests -p test_regressions.py -v
if errorlevel 1 goto done
".venv\Scripts\python.exe" -u tests\smoke_local.py
:done
pause
