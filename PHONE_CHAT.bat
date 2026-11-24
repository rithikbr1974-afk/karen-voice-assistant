@echo off
title Karen Remote (Telegram)
rem Chat with Karen from your phone on Telegram. Needs TELEGRAM_BOT_TOKEN (CHANGE_SETTINGS.bat).
rem The first time it links your Telegram account; only linked accounts get answers.
cd /d "%~dp0"
set "PATH=%~dp0tools;%PATH%"
if not exist data\remote.json (
    .venv\Scripts\python.exe remote.py --pair telegram || goto :end
)
echo.
echo Karen Remote is running. Message your bot on Telegram. Close this window to stop it.
echo To start it with Windows instead: .venv\Scripts\python.exe scripts\autostart.py remote on
.venv\Scripts\python.exe remote.py
:end
pause
