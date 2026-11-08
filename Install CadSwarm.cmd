@echo off
rem Double-click me to install the standalone CADSwarm desktop application.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install\install.ps1"
if errorlevel 1 (
    echo.
    echo CadSwarm setup did not finish. Please read the message above.
    pause
)
