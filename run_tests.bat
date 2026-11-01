@echo off
REM Runs the automated tests. All should say "passed".
cd /d "%~dp0"
call .venv\Scripts\activate.bat
python -m pytest -v
pause
