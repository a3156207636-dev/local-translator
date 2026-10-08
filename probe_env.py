"""环境自检：验证 UI Automation 与全局键盘钩子在本机可用。"""
import sys
import time
import threading


def probe_uia():
    print("[1] UI Automation ...", end=" ", flush=True)
    try:
        import uiautomation as auto
    except Exception as e:
        print("FAIL 导入失败:", e)
        return False
    try:
        root = auto.GetRootControl()
        print("OK  根元素 =", root.Name or "(桌面)")
    except Exception as e:
        print("FAIL 获取根元素:", e)
        return False

    try:
        fg = auto.GetForegroundControl()
        print("    前台元素 =", repr(fg.Name), "| 类型 =", fg.ControlTypeName)
    except Exception as e:
        print("    前台元素读取失败:", e)

    try:
        focused = auto.GetFocusedControl()
        print("    焦点元素 =", repr(focused.Name), "| 类型 =", focused.ControlTypeName)
        vp = focused.GetValuePattern()
        if vp:
            print("    ValuePattern 可用, 当前值 =", repr(vp.Value)[:80])
        else:
            print("    该控件无 ValuePattern")
        tp = focused.GetTextPattern()
        print("    TextPattern:", "可用" if tp else "不可用")
    except Exception as e:
        print("    焦点元素读取失败:", e)
    return True


def probe_hook():
    print("[2] 全局键盘钩子 ...", end=" ", flush=True)
    try:
        from pynput import keyboard
    except Exception as e:
        print("FAIL 导入失败:", e)
        return False

    got = []

    def on_press(key):
        got.append(key)
        return False

    try:
        listener = keyboard.Listener(on_press=on_press)
        listener.daemon = True
        listener.start()
        time.sleep(0.4)
        alive = listener.is_alive()
        listener.stop()
        print("OK  监听线程已启动" if alive else "WARN 监听线程未存活")
        return True
    except Exception as e:
        print("FAIL 启动失败:", e)
        return False


def probe_win32():
    print("[3] Win32 前台窗口 ...", end=" ", flush=True)
    try:
        import ctypes
        from ctypes import wintypes
        u32 = ctypes.windll.user32
        hwnd = u32.GetForegroundWindow()
        buf = ctypes.create_unicode_buffer(512)
        u32.GetWindowTextW(hwnd, buf, 512)
        tid = u32.GetWindowThreadProcessId(hwnd, None)

        class GUITHREADINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("hwndActive", wintypes.HWND),
                ("hwndFocus", wintypes.HWND),
                ("hwndCapture", wintypes.HWND),
                ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND),
                ("hwndCaret", wintypes.HWND),
                ("rcCaret", wintypes.RECT),
            ]

        gti = GUITHREADINFO()
        gti.cbSize = ctypes.sizeof(GUITHREADINFO)
        ok = u32.GetGUIThreadInfo(tid, ctypes.byref(gti))
        print(f"OK  「{buf.value}」 焦点hwnd={gti.hwndFocus} 光标hwnd={gti.hwndCaret} ok={bool(ok)}")
        return True
    except Exception as e:
        print("FAIL:", e)
        return False


if __name__ == "__main__":
    print("Python", sys.version.split()[0], "|", sys.executable)
    print("-" * 60)
    a = probe_uia()
    b = probe_hook()
    c = probe_win32()
    print("-" * 60)
    print("结果: UIA=%s  键盘钩子=%s  Win32=%s" % (a, b, c))
