@echo off
cd /d "%~dp0"

echo.
echo   本地双语翻译 —— 首次安装
echo   ========================
echo   会自动找 Python、建虚拟环境、装依赖，并登记开机自启。
echo   只需要跑这一次，之后开机就会自动启动。
echo.

call "%~dp0install.bat"
