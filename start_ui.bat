@echo off
title Autonomous Browser Agent GUI
cd /d "%~dp0"

echo ========================================================
echo    Launching Autonomous Browser Agent GUI...
echo ========================================================

if exist ".venv\Scripts\python.exe" (
    start "" http://localhost:8000
    ".venv\Scripts\python.exe" gui.py
) else (
    echo [ERROR] Virtual environment not found in .venv!
    echo Please run setup first.
    pause
)
