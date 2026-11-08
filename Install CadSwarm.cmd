@echo off
rem Double-click me to install the CADSwarm app (Start menu + desktop shortcut, Open with CADSwarm).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install\install.ps1"
if errorlevel 1 (
    echo.
    echo CadSwarm setup did not finish. Please read the message above.
    pause
)
