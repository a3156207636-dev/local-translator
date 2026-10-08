"""开机自启开关：读写 HKCU\\...\\Run 下的启动项。

用 pythonw.exe 启动，所以登录时不会闪出黑色控制台窗口。
命令里带 --autostart，程序会等桌面（explorer）就绪后再挂键盘钩子。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import config as cfgmod

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "LocalBilingualTranslator"


# --------------------------------------------------------------------------- #
def pythonw_path() -> str:
    """优先用 pythonw.exe（无控制台）。"""
    exe = Path(sys.executable)
    cand = exe.with_name("pythonw.exe")
    if cand.is_file():
        return str(cand)
    return str(exe)


def launch_command(delay_s: int = 0) -> str:
    """登录时要执行的那条命令。"""
    main_py = cfgmod.APP_DIR / "main.py"
    parts = [f'"{pythonw_path()}"', f'"{main_py}"', "--autostart"]
    if delay_s > 0:
        parts += ["--delay", str(delay_s)]
    return " ".join(parts)


# --------------------------------------------------------------------------- #
def is_enabled() -> bool:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as k:
            winreg.QueryValueEx(k, VALUE_NAME)
            return True
    except FileNotFoundError:
        return False
    except Exception:
        return False


def current_command() -> str:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as k:
            return str(winreg.QueryValueEx(k, VALUE_NAME)[0])
    except Exception:
        return ""


def set_autostart(on: bool, delay_s: int = 0) -> tuple[bool, str]:
    """打开/关闭开机自启。返回 (是否成功, 说明)。"""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE | winreg.KEY_READ
        ) as k:
            if on:
                cmd = launch_command(delay_s)
                winreg.SetValueEx(k, VALUE_NAME, 0, winreg.REG_SZ, cmd)
                return True, cmd
            try:
                winreg.DeleteValue(k, VALUE_NAME)
                return True, "已关闭"
            except FileNotFoundError:
                return True, "本来就是关闭的"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


# --------------------------------------------------------------------------- #
def wait_for_shell(timeout_s: float = 25.0, extra_s: float = 2.0) -> bool:
    """等桌面（explorer）起来，避免登录瞬间抢资源。"""
    import ctypes
    import time

    k32 = ctypes.windll.kernel32
    TH32CS_SNAPPROCESS = 0x00000002
    INVALID = ctypes.c_void_p(-1).value

    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.c_ulong),
            ("cntUsage", ctypes.c_ulong),
            ("th32ProcessID", ctypes.c_ulong),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", ctypes.c_ulong),
            ("cntThreads", ctypes.c_ulong),
            ("th32ParentProcessID", ctypes.c_ulong),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", ctypes.c_ulong),
            ("szExeFile", ctypes.c_char * 260),
        ]

    def explorer_running() -> bool:
        snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snap == INVALID:
            return True
        try:
            entry = PROCESSENTRY32()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
            ok = k32.Process32First(snap, ctypes.byref(entry))
            while ok:
                if entry.szExeFile.decode("mbcs", "ignore").lower() == "explorer.exe":
                    return True
                ok = k32.Process32Next(snap, ctypes.byref(entry))
            return False
        finally:
            k32.CloseHandle(snap)

    end = time.time() + timeout_s
    ready = False
    while time.time() < end:
        if explorer_running():
            ready = True
            break
        time.sleep(1.0)
    if ready and extra_s > 0:
        time.sleep(extra_s)
    return ready


def describe() -> str:
    if is_enabled():
        return f"已开启 → {current_command()}"
    return "未开启"
