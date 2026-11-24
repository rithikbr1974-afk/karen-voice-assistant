@echo off
title Karen setup
rem One click: installs Python 3.12 inside this folder, Karen's packages and models (~2.5 GB, 10-20 min the first
rem time), then asks a few questions. Safe to run again: finished steps are skipped.
cd /d "%~dp0"
set "UV=%~dp0tools\uv.exe"
set "UV_PYTHON_INSTALL_DIR=%~dp0.python"
set "PATH=%~dp0tools;%PATH%"
echo.
echo   Setting up Karen in %~dp0
echo   Keep this window open. The first run downloads about 2.5 GB.
echo.
if not exist "%UV%" (
    echo [0/4] uv, the Python installer, from astral.sh
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$env:UV_INSTALL_DIR='%~dp0tools'; $env:UV_NO_MODIFY_PATH='1'; irm https://astral.sh/uv/install.ps1 | iex"
    if not exist "%UV%" goto :fail
)
if not exist .venv\Scripts\python.exe (
    echo [1/4] Python 3.12
    "%UV%" venv --python 3.12 --python-preference only-managed .venv || goto :fail
)
echo [2/4] Karen's packages
"%UV%" pip install --python .venv\Scripts\python.exe -r requirements.txt || goto :fail
echo [3/4] Voice, speech, face and hand models
.venv\Scripts\python.exe scripts\download_models.py || goto :fail
echo [3/4] Google tools (Gmail, Calendar) - cached for offline start
"%UV%" tool run --quiet --python cpython-3.12-windows-x86_64-none workspace-mcp@1.30.1 --help >nul 2>nul
echo [4/4] Your details and keys
.venv\Scripts\python.exe scripts\setup_wizard.py
echo.
echo Setup done. Start Karen with START_KAREN.bat
pause
exit /b 0

:fail
echo.
echo Setup stopped - see the message above. Check your internet connection and run SETUP.bat again.
pause
exit /b 1
