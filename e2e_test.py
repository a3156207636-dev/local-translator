"""端到端集成测试：造一个原生 Win32 输入框，模拟打字，验证完整触发链路。

链路：键盘事件 → watcher 防抖 → UIA 读焦点输入框文本 → 本地模型翻译 → 悬浮窗
跑完自动清理，不留窗口和进程。
"""
from __future__ import annotations

import ctypes
import json
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

import uiautomation as auto

APP_DIR = Path(__file__).resolve().parent
PY = APP_DIR / ".venv" / "Scripts" / "python.exe"
LOG = APP_DIR / "logs" / "app.log"
CONFIG = APP_DIR / "config.json"

u32 = ctypes.windll.user32
k32 = ctypes.windll.kernel32

# 必须显式声明，否则 64 位下 lParam/WPARAM 会溢出
u32.DefWindowProcW.argtypes = [wintypes.HWND, ctypes.c_uint,
                               wintypes.WPARAM, wintypes.LPARAM]
u32.DefWindowProcW.restype = ctypes.c_ssize_t

SAMPLE = "今天天气不错，我们出去走走吧。"
EXPECT_IN_LOG = "今天天气不错"

WS_OVERLAPPEDWINDOW = 0x00CF0000
WS_CHILD, WS_VISIBLE, WS_BORDER = 0x40000000, 0x10000000, 0x00800000
WS_VSCROLL = 0x00200000
ES_MULTILINE, ES_AUTOVSCROLL = 0x0004, 0x0040
SW_SHOW = 5
PM_REMOVE = 1
VK_SPACE = 0x20
KEYEVENTF_KEYUP = 0x0002

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, ctypes.c_uint,
                             wintypes.WPARAM, wintypes.LPARAM)


def _wnd_proc(hwnd, msg, wp, lp):
    if msg == 0x0010:  # WM_CLOSE
        u32.DestroyWindow(hwnd)
        u32.PostQuitMessage(0)
        return 0
    return u32.DefWindowProcW(hwnd, msg, wp, lp)


class WNDCLASS(ctypes.Structure):
    _fields_ = [
        ("style", ctypes.c_uint),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


def pump(seconds: float) -> None:
    """跑消息循环，让窗口保持响应。"""
    end = time.time() + seconds
    msg = wintypes.MSG()
    while time.time() < end:
        while u32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
            u32.TranslateMessage(ctypes.byref(msg))
            u32.DispatchMessageW(ctypes.byref(msg))
        time.sleep(0.02)


def tap(vk: int) -> None:
    u32.keybd_event(vk, 0, 0, 0)
    time.sleep(0.05)
    u32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


def set_log_level(level: str) -> None:
    if not CONFIG.exists():
        return
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    cfg.setdefault("app", {})["log_level"] = level
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    print("=" * 66)
    print("端到端集成测试：打字 → 悬浮窗双语翻译")
    print("=" * 66)

    LOG.parent.mkdir(exist_ok=True)
    LOG.write_text("", encoding="utf-8")
    set_log_level("DEBUG")

    print("\n[1] 启动应用 ...")
    app = subprocess.Popen(
        [str(PY), str(APP_DIR / "main.py")],
        cwd=str(APP_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    ready = False
    deadline = time.time() + 120
    while time.time() < deadline:
        time.sleep(1)
        text = LOG.read_text(encoding="utf-8", errors="replace") if LOG.exists() else ""
        if "模型已预热" in text:
            ready = True
            break
        if app.poll() is not None:
            print("   应用提前退出：")
            print(app.stdout.read().decode("utf-8", "replace")[:2000])
            return 1
    print("   就绪" if ready else "   预热超时，继续测试")
    for l in LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        if any(k in l for k in ("引擎", "预热", "配置")):
            print("   " + l)

    print("\n[2] 创建原生输入框 ...")
    hinst = k32.GetModuleHandleW(None)
    proc = WNDPROC(_wnd_proc)
    wc = WNDCLASS()
    wc.lpfnWndProc = proc
    wc.hInstance = hinst
    wc.lpszClassName = "LbtE2ETestWnd"
    u32.RegisterClassW(ctypes.byref(wc))

    hwnd = u32.CreateWindowExW(
        0, "LbtE2ETestWnd", "LBT 集成测试输入框", WS_OVERLAPPEDWINDOW,
        120, 120, 640, 220, None, None, hinst, None,
    )
    edit = u32.CreateWindowExW(
        0, "EDIT", "",
        WS_CHILD | WS_VISIBLE | WS_BORDER | WS_VSCROLL | ES_MULTILINE | ES_AUTOVSCROLL,
        10, 10, 600, 160, hwnd, ctypes.c_void_p(1), hinst, None,
    )
    u32.ShowWindow(hwnd, SW_SHOW)
    u32.UpdateWindow(hwnd)
    pump(0.5)

    u32.SetWindowTextW(edit, SAMPLE)
    u32.SetForegroundWindow(hwnd)
    u32.SetFocus(edit)
    pump(0.8)
    print(f"   输入框内容 : {SAMPLE!r}")

    auto.InitializeUIAutomationInCurrentThread()
    fg = auto.GetFocusedControl()
    vp = fg.GetPattern(auto.PatternId.ValuePattern)
    print(f"   焦点控件   : {fg.ControlTypeName}")
    print(f"   UIA 读到   : {(vp.Value if vp else '(无 ValuePattern)')!r}")

    print("\n[3] 模拟按键触发自动翻译 ...")
    tap(VK_SPACE)
    pump(8)

    print("\n[4] 检查应用日志 ...")
    log_text = LOG.read_text(encoding="utf-8", errors="replace")
    lines = log_text.splitlines()
    hit = [l for l in lines if EXPECT_IN_LOG in l and "->" in l]
    trig = [l for l in lines if "触发翻译" in l or "停顿" in l]
    err = [l for l in lines if "ERROR" in l or "出错" in l]

    ok = bool(hit)
    if trig:
        print("   触发记录：")
        for l in trig[:3]:
            print("     " + l)
    if hit:
        print("   翻译结果：")
        for l in hit:
            print("     " + l)
    else:
        print("   未看到翻译结果，日志尾部：")
        print("\n".join(lines[-25:]))
    if err:
        print("   错误：")
        for l in err[:6]:
            print("     " + l)

    print("\n[5] 清理 ...")
    u32.PostMessageW(hwnd, 0x0010, 0, 0)
    pump(0.3)
    app.terminate()
    try:
        app.wait(timeout=5)
    except Exception:
        app.kill()
    set_log_level("INFO")
    print("   已关闭测试窗口与应用")

    print("\n" + "=" * 66)
    print("结果:", "PASS" if ok else "FAIL")
    print("=" * 66)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
