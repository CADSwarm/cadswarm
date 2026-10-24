@echo off
rem Double-click me to install CadSwarm into FreeCAD.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install\install.ps1"
if errorlevel 1 (
    echo.
    echo CadSwarm setup did not finish. Please read the message above.
    pause
)
