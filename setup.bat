@echo off
title Amigo Voice Assistant - Setup
cd /d "%~dp0"
echo ===================================================
echo    AMIGO VOICE ASSISTANT - ONE-CLICK INSTALLER
echo ===================================================
echo.
python setup.py
echo.
echo Setup finished. Press any key to start Amigo Dashboard...
pause >nul
start "" "start_amigo.bat"
