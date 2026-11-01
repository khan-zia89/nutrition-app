@echo off
REM One-time setup: creates a private Python environment and installs the packages.
cd /d "%~dp0"
echo Creating the Python environment...
py -3 -m venv .venv 2>nul || python -m venv .venv
if not exist ".venv\Scripts\activate.bat" (
  echo.
  echo Could not create the environment. Is Python installed and "Add python.exe to PATH" ticked?
  pause
  exit /b 1
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
echo.
echo Setup finished. Double-click start_app.bat to open the app.
pause
