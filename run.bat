@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set UV_OFFLINE=1
if not exist ".venv\Scripts\python.exe" (
  echo Environment missing. Run setup.bat once on this Windows computer.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m streamlit run app/main.py %*
if errorlevel 1 (
  echo Start failed. See the message above. Installation: setup.bat
  pause
  exit /b 1
)
