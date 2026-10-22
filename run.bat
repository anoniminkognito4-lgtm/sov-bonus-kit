@echo off
rem SOV panel for Windows: run.bat (work database) or run.bat demo (demo database).
rem First start installs dependencies with uv (internet needed once).
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1

if exist ".venv\Scripts\python.exe" goto start
set "SOV_UV="
where uv >nul 2>nul && set "SOV_UV=uv"
if not defined SOV_UV if exist "%USERPROFILE%\.local\bin\uv.exe" set "SOV_UV=%USERPROFILE%\.local\bin\uv.exe"
if not defined SOV_UV if exist "%USERPROFILE%\.cargo\bin\uv.exe" set "SOV_UV=%USERPROFILE%\.cargo\bin\uv.exe"
if not defined SOV_UV (
  echo uv is not installed. Install it in PowerShell:
  echo   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  echo Then open a new window and run run.bat again. See README.md, "Kak zapustit".
  pause
  exit /b 1
)
echo First start: installing dependencies, this takes a few minutes...
"%SOV_UV%" sync --locked --python 3.12
if errorlevel 1 (
  echo Installation failed. Check the internet connection and run run.bat again.
  pause
  exit /b 1
)

:start
set UV_OFFLINE=1
if /i "%~1"=="demo" (
  set SOV_DEMO=1
  set "SOV_DB_PATH=%~dp0build\demo\sov.sqlite"
  echo DEMO mode: separate database build\demo\sov.sqlite with test data.
) else (
  if not defined SOV_DB_PATH set "SOV_DB_PATH=%~dp0build\sov.sqlite"
)
echo The panel opens in the browser: http://localhost:8501  - stop with Ctrl+C
rem The script opens the browser itself: Streamlit then asks no questions on first start.
start "" /min cmd /c "timeout /t 5 /nobreak >nul & start http://localhost:8501"
".venv\Scripts\python.exe" -m streamlit run app/main.py --server.headless true
if errorlevel 1 (
  echo Start failed. See the message above.
  pause
  exit /b 1
)
