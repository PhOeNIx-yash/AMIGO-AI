@echo off
title Amigo Voice Assistant Dashboard
cd /d "%~dp0"
echo ===================================================
echo    AMIGO VOICE ASSISTANT - STARTING SERVER
echo ===================================================
echo.
echo Opening Amigo Web Dashboard at http://localhost:5000...
start "" "http://localhost:5000"
python ui_server.py
pause
