@echo off
cd /d "%~dp0"
if exist "dist\WebVideoDownloaderPrototype\WebVideoDownloaderPrototype.exe" (
    start "" "dist\WebVideoDownloaderPrototype\WebVideoDownloaderPrototype.exe"
    exit /b
)
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "main.py"
    exit /b
)
echo Please run build.ps1 first.
pause
