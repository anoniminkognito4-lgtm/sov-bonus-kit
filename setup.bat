@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
where uv >nul 2>nul
if errorlevel 1 (
  echo Install uv first: winget install --id=astral-sh.uv -e
  echo Then reopen this window and run setup.bat again.
  pause
  exit /b 1
)
uv sync --locked --python 3.12
if errorlevel 1 (
  echo Setup failed. Check network access or the prepared Windows uv cache.
  pause
  exit /b 1
)
echo Setup completed. Run run.bat. Acceptance checks: check.bat
pause
