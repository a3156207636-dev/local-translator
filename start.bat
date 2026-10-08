@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
  echo Virtual environment not found, running first time setup...
  call "%~dp0install.bat"
  if not exist ".venv\Scripts\pythonw.exe" exit /b 1
)

".venv\Scripts\python.exe" "%~dp0main.py" --running >nul 2>&1
if %errorlevel%==0 (
  echo [OK] Already running - see the status ball on screen.
  exit /b 0
)

start "" ".venv\Scripts\pythonw.exe" "%~dp0main.py"
echo [OK] Started - a status ball will appear shortly.
exit /b 0
