@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0recovery.ps1" -InstallDir "%~dp0.."
pause
