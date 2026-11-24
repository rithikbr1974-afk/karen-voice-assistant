@echo off
rem Teach Karen your voice: record "Karen" 40 times and 20 everyday sentences, then train your own wake word.
rem Close Karen first (she uses the microphone). Quiet room, normal distance from the mic.
cd /d "%~dp0"
.venv\Scripts\python.exe scripts\record_samples.py wake || goto :end
echo.
echo Training your wake word now. This takes 10-20 minutes and needs no input.
.venv\Scripts\python.exe scripts\train_wake.py
echo.
echo Done. Start Karen again: she now wakes to YOUR "Karen".
:end
pause
