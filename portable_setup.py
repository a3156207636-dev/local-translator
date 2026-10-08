"""便携版辅助命令（也可以被向导直接 import）。

    python portable_setup.py shortcuts       在桌面创建快捷方式
    python portable_setup.py shortcuts-off   删掉桌面快捷方式
    python portable_setup.py autostart-on    开启开机自启
    python portable_setup.py autostart-off   关闭开机自启
    python portable_setup.py status          看看当前状态
"""
from __future__ import annotations

import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
ROOT = APP_DIR.parent

LNK_MAIN = "本地双语翻译.lnk"
LNK_GUIDE = "本地双语翻译 - 使用说明.lnk"


def _fix_console_encoding() -> None:
    """cmd 默认代码页是 936，把输出固定成 GBK，中文才不会乱码。

    只在作为脚本运行时调用 —— 被 import 时不该有副作用。
    """
    import sys as _sys

    if not _sys.platform.startswith("win"):
        return
    for s in (_sys.stdout, _sys.stderr):
        try:
            s.reconfigure(encoding="gbk", errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass


# --------------------------------------------------------------------------- #
def runtime_dir() -> Path:
    """便携布局是 <root>/runtime，开发布局是 <root>/.venv/Scripts。"""
    cand = ROOT / "runtime"
    if (cand / "python.exe").is_file():
        return cand
    exe = Path(sys.executable)
    return exe.parent


def assets_dir() -> Path:
    cand = ROOT / "assets"
    return cand if cand.is_dir() else APP_DIR / "assets"


def _icon(name: str) -> str:
    ico = assets_dir() / f"{name}.ico"
    return f"{ico},0" if ico.is_file() else ""


def desktop_dir() -> Path:
    try:
        import win32api

        return Path(win32api.SHGetFolderPath(0, 0x0010, None, 0))
    except Exception:
        return Path.home() / "Desktop"


# --------------------------------------------------------------------------- #
def make_shortcuts() -> list[Path]:
    import pythoncom
    import win32com.client

    runtime = runtime_dir()
    pythonw = runtime / "pythonw.exe"
    if not pythonw.is_file():
        pythonw = runtime / "python.exe"
    main_py = APP_DIR / "main.py"
    readme = ROOT / "README.md"

    pythoncom.CoInitialize()
    shell = win32com.client.Dispatch("WScript.Shell")
    desk = desktop_dir()
    desk.mkdir(parents=True, exist_ok=True)
    made: list[Path] = []

    lnk = shell.CreateShortCut(str(desk / LNK_MAIN))
    lnk.TargetPath = str(pythonw)
    lnk.Arguments = f'"{main_py}"'
    lnk.WorkingDirectory = str(APP_DIR)
    lnk.Description = "打中文时自动显示英文翻译的悬浮窗（纯本地）"
    if _icon("main"):
        lnk.IconLocation = _icon("main")
    lnk.Save()
    made.append(desk / LNK_MAIN)

    if readme.is_file():
        lnk = shell.CreateShortCut(str(desk / LNK_GUIDE))
        lnk.TargetPath = r"C:\Windows\System32\notepad.exe"
        lnk.Arguments = f'"{readme}"'
        lnk.WorkingDirectory = str(ROOT)
        lnk.Description = "查看使用说明"
        if _icon("guide"):
            lnk.IconLocation = _icon("guide")
        lnk.Save()
        made.append(desk / LNK_GUIDE)

    return made


def remove_shortcuts() -> list[Path]:
    desk = desktop_dir()
    gone = []
    for name in (LNK_MAIN, LNK_GUIDE):
        p = desk / name
        if p.is_file():
            try:
                p.unlink()
                gone.append(p)
            except Exception:
                pass
    return gone


# --------------------------------------------------------------------------- #
def autostart(on: bool) -> tuple[bool, str]:
    import autostart as auto

    return auto.set_autostart(on)


def status() -> None:
    import autostart as auto

    print("程序目录 :", APP_DIR)
    print("运行时   :", runtime_dir())
    print("桌面     :", desktop_dir())
    print("开机自启 :", auto.describe())
    try:
        import config as cfgmod

        cfg = cfgmod.load()
        e = cfg.get("engine", {})
        print("引擎     :", e.get("type"), e.get("base_url"), e.get("model"))
    except Exception as exc:
        print("配置读取失败:", exc)


# --------------------------------------------------------------------------- #
def main() -> int:
    _fix_console_encoding()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

    if cmd == "shortcuts":
        try:
            for p in make_shortcuts():
                print("已创建:", p)
            return 0
        except Exception as e:
            print("失败:", type(e).__name__, e)
            return 1

    if cmd == "shortcuts-off":
        for p in remove_shortcuts():
            print("已删除:", p)
        return 0

    if cmd == "autostart-on":
        ok, msg = autostart(True)
        print("开机自启已开启:" if ok else "开启失败:", msg)
        return 0 if ok else 1

    if cmd == "autostart-off":
        ok, msg = autostart(False)
        print("开机自启已关闭:" if ok else "关闭失败:", msg)
        return 0 if ok else 1

    status()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
