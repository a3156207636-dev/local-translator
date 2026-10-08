@echo off
cd /d "%~dp0"

echo.
echo   本地双语翻译 —— 调试模式
echo   ========================
echo   这个窗口会保留下来，打印全部运行日志和报错。
echo   去别的窗口打一句中文，这里会显示触发过程。
echo   关闭本窗口 = 退出程序。
echo.

call "%~dp0debug.bat"
