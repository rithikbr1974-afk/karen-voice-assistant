@echo off
rem Karen setup: Python 3.12 environment, packages, local models (~1.5 GB), then the setup wizard.
rem Safe to run again. "setup.bat --menu" jumps straight to one step of the wizard.
cd /d "%~dp0"
chcp 65001 >nul
if "%~1"=="--menu" goto :wizard

echo.
echo  [1/3] Python environment and packages (a few minutes the first time)
where uv >nul 2>nul
if %errorlevel%==0 (
    if not exist .venv\Scripts\python.exe uv venv --python 3.12 .venv || goto :fail
    uv pip install --python .venv\Scripts\python.exe -r requirements.txt || goto :fail
) else (
    py -3.12 --version >nul 2>nul || goto :nopython
    if not exist .venv\Scripts\python.exe py -3.12 -m venv .venv || goto :fail
    .venv\Scripts\python.exe -m pip install --upgrade pip >nul
    .venv\Scripts\python.exe -m pip install -r requirements.txt || goto :fail
)

echo.
echo  [2/3] Voice, speech and wake word models
.venv\Scripts\python.exe scripts\download_models.py || goto :fail
if not exist .env copy .env.example .env >nul

:wizard
echo.
echo  [3/3] Setup wizard
.venv\Scripts\python.exe setup.py %*
pause
exit /b 0

:nopython
echo.
echo Python 3.12 was not found. Install it from https://www.python.org/downloads/ (version 3.12)
echo (tick "Add python.exe to PATH" in the installer), then run setup.bat again.
pause
exit /b 1

:fail
echo.
echo Setup failed - see the error above. docs\SETUP.md has fixes for common problems.
pause
exit /b 1
