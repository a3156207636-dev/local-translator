"""时延探针：假设程序已经在运行，实测三个数字。

  1. 按下第一个键 → 悬浮窗出现在屏幕上，用了多久
  2. 上屏中文 → 译文的第一个字符，用了多久
  3. 再过 10 秒，窗口是否还在（auto_hide_ms = 0）

注意：程序要用「脱离会话」的方式先启动好（见 启动.bat），
在同一个命令行里连着跑本脚本。
"""
from __future__ import annotations

import ctypes
import re
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

import uiautomation as auto
from pynput import keyboard

APP_DIR = Path(__file__).resolve().parent
LOG = APP_DIR / "logs" / "app.log"
SAMPLE = "这个方案在离线环境下也能稳定运行，我已经测试过了。"
POLL = 0.01

user32 = ctypes.windll.user32

results: list[tuple[str, bool, str]] = []


def report(name: str, ok: bool, detail: str) -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}    {detail}")
    results.append((name, ok, detail))


# --------------------------------------------------------------------------- #
# 用 Win32 直接查窗口：比 UIA 轻得多，不会把被测量程序的界面线程拖住
# --------------------------------------------------------------------------- #
CB = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def overlay_windows(only_visible: bool = True) -> list[tuple[int, tuple[int, int, int, int], bool]]:
    """返回所有 Tk 顶层窗口 (hwnd, (left, top, w, h), visible)。"""
    found: list[tuple[int, tuple[int, int, int, int], bool]] = []

    def cb(hwnd, _lparam):
        try:
            buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(wintypes.HWND(hwnd), buf, 256)
            if not buf.value.startswith("Tk"):
                return True
            vis = bool(user32.IsWindowVisible(wintypes.HWND(hwnd)))
            if only_visible and not vis:
                return True
            r = wintypes.RECT()
            user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(r))
            found.append((int(hwnd), (r.left, r.top, r.right - r.left, r.bottom - r.top), vis))
        except Exception:
            pass
        return True

    user32.EnumWindows(CB(cb), 0)
    return found


user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL


def overlay_window():
    """悬浮窗本体（排除右下角那个 96x28 的小状态球）。"""
    for hwnd, rect, _vis in overlay_windows(True):
        w, h = rect[2], rect[3]
        if w > 130 or h > 40:
            return hwnd, rect
    return None, None


def is_visible() -> bool:
    hwnd, _ = overlay_window()
    return bool(hwnd)


def wait_visible(timeout: float, trace: bool = False) -> float | None:
    t0 = time.perf_counter()
    last_trace = 0.0
    while time.perf_counter() - t0 < timeout:
        if is_visible():
            return time.perf_counter() - t0
        if trace and time.perf_counter() - t0 - last_trace > 0.25:
            last_trace = time.perf_counter() - t0
            all_tk = overlay_windows(False)
            info = " | ".join(
                f"hwnd={h} {r[2]}x{r[3]}@{r[0]},{r[1]} vis={v}" for h, r, v in all_tk
            ) or "（没有任何 Tk 窗口）"
            print(f"        +{last_trace:.2f}s  {info}")
        time.sleep(POLL)
    return None


def root_children():
    """只用于找记事本，调用次数很少，不会造成压力。"""
    try:
        return auto.GetRootControl().GetChildren()
    except Exception:
        return []


def log_lines() -> list[str]:
    try:
        return LOG.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return []


def wait_log(rx: str, baseline: int, timeout: float) -> tuple[str, float] | None:
    t0 = time.perf_counter()
    pat = re.compile(rx)
    while time.perf_counter() - t0 < timeout:
        for line in log_lines()[baseline:]:
            if pat.search(line):
                return line.strip(), time.perf_counter() - t0
        time.sleep(POLL)
    return None


def find_edit(pid: int, timeout: float = 15.0):
    end = time.time() + timeout
    while time.time() < end:
        for w in root_children():
            try:
                if w.ProcessId != pid:
                    continue
                if "记事本" not in w.Name and "Notepad" not in w.Name:
                    continue
                for c in w.GetChildren():
                    if c.ControlType in (auto.ControlType.DocumentControl,
                                         auto.ControlType.EditControl):
                        return w, c
                    for gc in c.GetChildren():
                        if gc.ControlType in (auto.ControlType.DocumentControl,
                                              auto.ControlType.EditControl):
                            return w, gc
            except Exception:
                continue
        time.sleep(0.4)
    return None, None


# --------------------------------------------------------------------------- #
def main() -> int:
    if not is_visible():
        print("起始状态：悬浮窗隐藏 ✔")
    else:
        print("起始状态：悬浮窗已可见（先点一下窗口上的 × 再跑更准）")

    print("打开记事本…")
    p = subprocess.Popen(["notepad.exe"])
    time.sleep(2.0)
    win, edit = find_edit(p.pid)
    if edit is None:
        print("!! 没找到记事本的编辑框")
        p.terminate()
        return 1
    try:
        win.SetActive()
        edit.SetFocus()
    except Exception:
        pass
    time.sleep(0.6)

    kb = keyboard.Controller()

    # ---- 1. 第一个键 → 窗口出现 -------------------------------------- #
    print("\n[1] 按下第一个键，测窗口出现时延…")
    baseline = len(log_lines())
    kb.press(keyboard.Key.backspace)
    t_press = time.perf_counter()
    kb.release(keyboard.Key.backspace)
    dt = wait_visible(3.0, trace=True)
    if dt is None:
        report("按键 → 窗口出现", False, "3 秒内没出现")
    else:
        report("按键 → 窗口出现", dt < 0.35, f"{dt * 1000:.0f} ms")
    # 再补两下，确保打字信号稳定
    for _ in range(2):
        kb.press(keyboard.Key.backspace)
        kb.release(keyboard.Key.backspace)
        time.sleep(0.06)

    # ---- 2. 上屏中文 → 译文出现 --------------------------------------- #
    print("\n[2] 上屏中文，测译文出现时延…")
    log_base = len(log_lines())
    try:
        edit.GetValuePattern().SetValue(SAMPLE)
    except Exception as e:
        print("   写入失败：", e)
        p.terminate()
        return 1

    hit = wait_log(r"共 \d+ms，首字 \d+ms", log_base, timeout=12.0)
    if hit is None:
        report("上屏 → 出译文", False, "12 秒内没看到翻译完成的日志")
        for line in log_lines()[log_base:]:
            print("        ", line.strip())
    else:
        print(f"        日志：{hit[0]}")
        m = re.search(r"共 (\d+)ms，首字 (\d+)ms", hit[0])
        total_ms, first_ms = int(m.group(1)), int(m.group(2))
        report("上屏 → 首字浮现", first_ms < 800, f"首字 {first_ms} ms")
        report("上屏 → 整句完成", total_ms < 1500, f"共 {total_ms} ms")
        report("上屏 → 窗口可见", wait_visible(0.2) is not None, "窗口在屏幕上")

    # ---- 3. 不自动消失 ------------------------------------------------ #
    print("\n[3] 静置 10 秒，看窗口会不会自己消失…")
    time.sleep(10.0)
    still = is_visible()
    report("10 秒后仍在屏幕上", still, "auto_hide_ms=0" if still else "被自动隐藏了")

    hwnd, rect = overlay_window()
    if hwnd:
        print(f"        窗口 hwnd={hwnd} 位置=({rect[0]},{rect[1]})  尺寸 {rect[2]}x{rect[3]}")

    try:
        win.SetActive()
        edit.SetFocus()
    except Exception:
        pass
    p.terminate()

    print()
    bad = [n for n, ok, _ in results if not ok]
    if bad:
        print(f"不通过 {len(bad)} 项：{bad}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
