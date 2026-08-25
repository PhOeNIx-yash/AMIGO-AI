@echo off
title Amigo Voice Assistant - Web Dashboard
cd /d "%~dp0"
echo ======================================================
echo    Starting Amigo Voice Assistant Dashboard
echo ======================================================
echo.

start "" "http://localhost:5000"

where py >nul 2>&1
if %errorlevel% equ 0 (
    py ui_server.py
) else (
    python ui_server.py
)

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Amigo UI Server exited with an error.
    pause
)
