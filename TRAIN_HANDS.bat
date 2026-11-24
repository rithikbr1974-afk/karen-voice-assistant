@echo off
rem Hand gesture trainer for the 3D viewer and the phone mirror (MediaPipe, runs on this PC).
cd /d "%~dp0"
start "" .venv\Scripts\pythonw.exe hands.py --train
