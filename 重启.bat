@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo 还没安装，请先双击「首次安装.bat」。
  pause
  exit /b 1
)

echo.
echo   本地双语翻译 —— 重启
echo   ========================
echo   会先关掉正在运行的实例，再重新启动一个。
echo   改完 config.json 或代码之后，用这个让改动生效。
echo.

".venv\Scripts\python.exe" "%~dp0restart.py"

echo.
echo   按任意键关闭本窗口。
pause >nul
exit /b 0
