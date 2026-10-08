"""在桌面生成快捷方式。

生成三个：
  1. 本地双语翻译          —— 直接启动主程序（pythonw，无黑窗口）
  2. 本地双语翻译 - 重启    —— 跑「重启.bat」，会弹出小窗显示结果
  3. 本地双语翻译 - 使用说明 —— 用记事本打开 README.md

用法：
    python tools/make_shortcuts.py
"""
from __future__ import annotations

import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
ASSETS = APP_DIR / "assets"
PYTHONW = APP_DIR / ".venv" / "Scripts" / "pythonw.exe"
MAIN = APP_DIR / "main.py"
RESTART_BAT = APP_DIR / "重启.bat"
README = APP_DIR / "README.md"
NOTEPAD = Path(r"C:\Windows\System32\notepad.exe")

import pythoncom
import win32com.client


def desktop_dir() -> Path:
    """取真实桌面路径（兼容 OneDrive 重定向）。"""
    import win32api
    import win32con
    try:
        # CSIDL_DESKTOPDIRECTORY = 0x0010
        return Path(win32api.SHGetFolderPath(0, 0x0010, None, 0))
    except Exception:
        return Path.home() / "Desktop"


def make(shortcut_path: Path, target: str, args: str, workdir: str,
         icon: str, description: str) -> None:
    shell = win32com.client.Dispatch("WScript.Shell")
    lnk = shell.CreateShortCut(str(shortcut_path))
    lnk.TargetPath = target
    if args:
        lnk.Arguments = args
    if workdir:
        lnk.WorkingDirectory = workdir
    if icon:
        lnk.IconLocation = icon
    lnk.Description = description
    lnk.Save()


def main() -> int:
    pythoncom.CoInitialize()
    desk = desktop_dir()
    desk.mkdir(parents=True, exist_ok=True)
    made = []

    if not PYTHONW.is_file():
        print("虚拟环境不存在，请先运行「首次安装.bat」")
        return 1

    tasks = [
        (
            desk / "本地双语翻译.lnk",
            str(PYTHONW), f'"{MAIN}"', str(APP_DIR),
            f"{ASSETS / 'main.ico'},0",
            "打中文时自动显示英文翻译的悬浮窗（纯本地）",
        ),
        (
            desk / "本地双语翻译 - 重启.lnk",
            str(RESTART_BAT), "", str(APP_DIR),
            f"{ASSETS / 'restart.ico'},0",
            "结束旧实例并重启（改完配置后用）",
        ),
        (
            desk / "本地双语翻译 - 使用说明.lnk",
            str(NOTEPAD), f'"{README}"', str(APP_DIR),
            f"{ASSETS / 'guide.ico'},0",
            "查看使用说明 README.md",
        ),
    ]

    for path, target, args, workdir, icon, desc in tasks:
        if not Path(target).is_file():
            print(f"跳过（目标不存在）：{path.name} -> {target}")
            continue
        make(path, target, args, workdir, icon, desc)
        made.append(path)
        print(f"已创建：{path}")

    print(f"\n桌面目录：{desk}")
    return 0 if made else 1


if __name__ == "__main__":
    sys.exit(main())
