@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Virtual environment not found, running first time setup...
  call "%~dp0install.bat"
)

echo Starting in console mode. Close this window to stop the app.
echo.
".venv\Scripts\python.exe" "%~dp0main.py" %*

echo.
echo The app has exited.
pause >nul
