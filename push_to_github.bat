@echo off
title Push Amigo to GitHub
cd /d "%~dp0"
set "GIT_EXE=C:\Users\yggar\.gemini\antigravity-ide\tools\mingit\cmd\git.exe"
if not exist "%GIT_EXE%" set "GIT_EXE=git"

echo ===================================================
echo    PUSHING AMIGO CHANGES TO PhOenIx-yash/AMIGO-AI
echo ===================================================
echo.
"%GIT_EXE%" remote set-url origin https://github.com/PhOenIx-yash/AMIGO-AI.git
echo Pushing to branch 'main'...
echo (If prompted, sign in with your GitHub account in the browser or enter your token)
echo.
"%GIT_EXE%" push -u origin main --force
echo.
if %ERRORLEVEL% equ 0 (
    echo ===================================================
    echo   SUCCESS! All changes are live on your GitHub:
    echo   https://github.com/PhOenIx-yash/AMIGO-AI
    echo ===================================================
) else (
    echo [!] Push encountered an issue. Check your GitHub authentication.
)
echo.
pause
