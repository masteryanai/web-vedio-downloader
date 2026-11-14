@echo off
set "repair=%~dp0.maintenance\recovery.ps1"
if not exist "%repair%" set "repair=%~dp0_internal\recovery.ps1"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%repair%" -InstallDir "%~dp0."
pause
