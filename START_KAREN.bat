@echo off
rem Start Karen (opens her window). Options: --no-ui (console only), --text (type), --mic (mic check), --devices
cd /d "%~dp0"
set "PATH=%~dp0tools;%PATH%"
if not exist .venv\Scripts\python.exe (
    echo Run SETUP.bat first.
    pause
    exit /b 1
)
.venv\Scripts\python.exe main.py %*
if errorlevel 1 pause
