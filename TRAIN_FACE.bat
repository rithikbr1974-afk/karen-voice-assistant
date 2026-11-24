@echo off
rem Teach Karen your face (about 30 webcam samples). Then turn on Settings > Face unlock in Karen's window.
rem Run with --test to see your live match score.
cd /d "%~dp0"
.venv\Scripts\python.exe scripts\enroll_face.py %*
pause
