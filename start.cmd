@echo off
cd /d "%~dp0"
if exist "dist\WebVideoDownloader\WebVideoDownloader.exe" (
    start "" "dist\WebVideoDownloader\WebVideoDownloader.exe"
    exit /b
)
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "main.py"
    exit /b
)
echo Please run build.ps1 first.
pause
