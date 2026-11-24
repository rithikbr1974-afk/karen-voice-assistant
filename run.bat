@echo off
rem Start Karen (opens the interface). Options: --no-ui (console only), --text (type), --mic (mic check), --devices
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    echo Run setup.bat first.
    pause
    exit /b 1
)
.venv\Scripts\python.exe main.py %*
pause
