@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
"%~dp0runtime\python\python.exe" "%~dp0tools\relocate_environment.py"
pause
