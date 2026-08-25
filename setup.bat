@echo off
title Amigo Voice Assistant - Setup
cd /d "%~dp0"
echo ======================================================
echo    Setting up Amigo Voice Assistant
echo ======================================================
echo.

where py >nul 2>&1
if %errorlevel% equ 0 (
    py setup.py
) else (
    python setup.py
)

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Setup encountered an issue. Please check the logs above.
    pause
    exit /b %errorlevel%
)

echo.
echo [OK] Setup completed successfully!
pause
