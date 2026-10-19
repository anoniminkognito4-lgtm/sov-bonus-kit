@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set UV_OFFLINE=1
if not exist ".venv\Scripts\python.exe" (
  echo Environment missing. Run setup.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" tools/acceptance.py --require-windows
if errorlevel 1 (
  echo Acceptance failed. See build/acceptance for logs.
  pause
  exit /b 1
)
echo Acceptance passed. See build/acceptance. Complete the browser checklist in docs/WINDOWS.md.
pause
