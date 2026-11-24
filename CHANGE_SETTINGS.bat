@echo off
rem Change your name, city, keys and extras (Enter keeps what's saved).
cd /d "%~dp0"
.venv\Scripts\python.exe scripts\setup_wizard.py
pause
