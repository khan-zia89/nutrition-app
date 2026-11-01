@echo off
REM Starts the app and opens it in your web browser. Close this window to stop the app.
cd /d "%~dp0"
if not exist ".venv\Scripts\activate.bat" (
  echo Please run install.bat first.
  pause
  exit /b 1
)
call .venv\Scripts\activate.bat
start "" cmd /c "timeout /t 4 >nul & start http://localhost:8501"
streamlit run app.py --server.address localhost
pause
