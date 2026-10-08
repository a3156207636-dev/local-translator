"""验证：程序注入的按键能否被 pynput 全局钩子捕获。"""
import ctypes
import time

from pynput import keyboard

u32 = ctypes.windll.user32
KEYEVENTF_KEYUP = 0x0002

got = []


def on_press(key):
    got.append(("press", repr(key)))
    return True


def on_release(key):
    got.append(("release", repr(key)))
    return True


lst = keyboard.Listener(on_press=on_press, on_release=on_release)
lst.daemon = True
lst.start()
print("监听器已启动，存活:", lst.is_alive())
time.sleep(1.0)

print("注入 VK_SPACE (keybd_event) ...")
u32.keybd_event(0x20, 0, 0, 0)
time.sleep(0.05)
u32.keybd_event(0x20, 0, KEYEVENTF_KEYUP, 0)
time.sleep(1.0)

print("注入 'A' (keybd_event) ...")
u32.keybd_event(0x41, 0, 0, 0)
time.sleep(0.05)
u32.keybd_event(0x41, 0, KEYEVENTF_KEYUP, 0)
time.sleep(1.0)

print("注入 'B' (SendInput) ...")
PUL = ctypes.POINTER(ctypes.c_ulong)


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", PUL)]


class _INPUTunion(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("padding", ctypes.c_byte * 24)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("u", _INPUTunion)]


def send_input(vk, up=False):
    inp = INPUT()
    inp.type = 1
    inp.u.ki.wVk = vk
    inp.u.ki.dwFlags = KEYEVENTF_KEYUP if up else 0
    u32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


send_input(0x42, False)
time.sleep(0.05)
send_input(0x42, True)
time.sleep(1.0)

lst.stop()
print()
print("捕获到", len(got), "个事件:")
for kind, k in got:
    print(f"  {kind:8} {k}")
print()
print("结论:", "注入事件可被捕获" if got else "注入事件被过滤，钩子看不到")
