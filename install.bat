@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ==========================================================
echo   Local Bilingual Translator - first time setup
echo ==========================================================
echo.

set "PYCMD="
for %%V in (3.14 3.13 3.12 3.11 3.10) do (
  if not defined PYCMD (
    py -%%V -c "import tkinter" >nul 2>&1
    if !errorlevel! equ 0 set "PYCMD=py -%%V"
  )
)

if not defined PYCMD (
  python -c "import tkinter" >nul 2>&1
  if !errorlevel! equ 0 set "PYCMD=python"
)

if not defined PYCMD (
  echo [ERROR] No Python with tkinter found.
  echo.
  echo Please install Python 3.11 or newer from python.org
  echo and keep the "tcl/tk" component checked during install.
  echo.
  pause
  exit /b 1
)

echo Found Python: !PYCMD!
echo.

if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment in .venv ...
  !PYCMD! -m venv .venv
  if !errorlevel! neq 0 (
    echo [ERROR] Failed to create the virtual environment.
    pause
    exit /b 1
  )
)

echo Installing dependencies ...
".venv\Scripts\python.exe" -m pip install --upgrade pip -q
".venv\Scripts\python.exe" -m pip install -r "%~dp0requirements.txt"
if !errorlevel! neq 0 (
  echo [ERROR] pip install failed. Check your network and retry.
  pause
  exit /b 1
)

echo.
echo Registering auto start at logon ...
".venv\Scripts\python.exe" "%~dp0main.py" --set-autostart on
if !errorlevel! neq 0 (
  echo [WARN] Could not register auto start. You can retry later with
  echo        autostart-on.bat
)

echo.
echo Setup finished.
echo   - It will now start automatically after you log in.
echo   - To start it right now, run start.bat
echo   - To turn auto start off, run autostart-off.bat
echo.
pause
exit /b 0
