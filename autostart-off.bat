@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo 还没安装，请先双击「首次安装.bat」。
  pause
  exit /b 1
)

echo.
echo   正在关闭【开机自动启动】...
echo.
".venv\Scripts\python.exe" "%~dp0main.py" --set-autostart off

echo.
echo   当前状态：
".venv\Scripts\python.exe" "%~dp0main.py" --autostart-status
echo   其中 ON = 已开启，OFF = 已关闭。
echo.
echo   按任意键关闭本窗口。
pause >nul
exit /b 0
