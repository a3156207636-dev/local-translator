"""重启工具：结束正在运行的实例，再重新启动一个。

启动方式用 `cmd /c start` 中转，让新进程脱离当前命令行会话——这样关掉
这个窗口后工具还会继续待在后台。启动后会等两秒确认它真的活着。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
MAIN = APP_DIR / "main.py"


def _pythonw() -> Path:
    """当前解释器的无控制台版本。

    开发环境是 `.venv\\Scripts\\python.exe`，便携包是 `runtime\\python.exe`，
    两种布局都能靠 `sys.executable` 自动对上，不用写死路径。
    """
    exe = Path(sys.executable)
    cand = exe.with_name("pythonw.exe")
    return cand if cand.is_file() else exe


PYTHONW = _pythonw()

# 简体中文控制台的代码页是 936，把标准输出固定成 GBK，
# 否则中文在 cmd 窗口里会显示成乱码。
if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="gbk", errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


# --------------------------------------------------------------------------- #
def list_instances() -> list[int]:
    """找出所有在跑本工具的 pythonw 进程。"""
    try:
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        svc = win32com.client.Dispatch(
            "WbemScripting.SWbemLocator"
        ).ConnectServer(".", "root\\cimv2")
        rows = svc.ExecQuery(
            "SELECT ProcessId, CommandLine FROM Win32_Process WHERE Name='pythonw.exe'"
        )
    except Exception:
        return _fallback_list()

    target = str(APP_DIR).lower()
    pids: list[int] = []
    try:
        for p in rows:
            cl = p.CommandLine or ""
            if "main.py" in cl and target in cl.lower():
                pids.append(int(p.ProcessId))
    except Exception:
        return _fallback_list()
    return pids


def _fallback_list() -> list[int]:
    """没有 pywin32 时退化成按「互斥体是否被占用」判断。"""
    try:
        exe = Path(sys.executable)
        r = subprocess.run(
            [str(exe), str(MAIN), "--running"],
            capture_output=True, text=True, timeout=20, cwd=str(APP_DIR),
        )
        if r.stdout.strip().endswith("RUNNING"):
            print("  （有实例在运行，但拿不到 PID；只能用互斥体判断）")
            return [-1]
    except Exception:
        pass
    return []


def kill(pid: int) -> bool:
    if pid < 0:
        print("  无法结束：拿不到 PID。请从右下角状态球右键退出。")
        return False
    try:
        import win32api
        import win32con
        import win32process

        h = win32api.OpenProcess(win32con.PROCESS_TERMINATE, False, pid)
        win32process.TerminateProcess(h, 0)
        win32api.CloseHandle(h)
        return True
    except Exception:
        pass
    try:
        return subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"],
            capture_output=True, timeout=20,
        ).returncode == 0
    except Exception:
        return False


def spawn(args: list[str]) -> None:
    """用 cmd /c start 中转启动，脱离当前会话。"""
    if not PYTHONW.is_file():
        raise FileNotFoundError(f"找不到 {PYTHONW}，请先运行「首次安装.bat」")
    comspec = os.environ.get("COMSPEC") or r"C:\Windows\System32\cmd.exe"
    subprocess.Popen(
        [comspec, "/c", "start", "", str(PYTHONW), str(MAIN), *args],
        cwd=str(APP_DIR),
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        close_fds=True,
    )


# --------------------------------------------------------------------------- #
def main() -> int:
    print("本地双语翻译 · 重启")
    print("=" * 30)

    if not MAIN.is_file():
        print(f"找不到主程序：{MAIN}")
        return 1

    old = list_instances()
    if old:
        print(f"正在结束旧实例：{', '.join(str(p) for p in old)}")
        for pid in old:
            print(f"  PID {pid} {'已结束' if kill(pid) else '结束失败'}")
        time.sleep(1.5)
        left = list_instances()
        if left:
            print(f"  ⚠ 还有实例在跑：{left}")
    else:
        print("没有正在运行的实例。")

    try:
        spawn(["--autostart"])
    except Exception as e:
        print(f"启动失败：{type(e).__name__}: {e}")
        return 1

    time.sleep(2.5)
    now = list_instances()
    if now:
        print(f"已启动，PID {now}")
        print("看屏幕右下角：出现「译 · 待机」小圆球就是好了。")
        return 0
    print("启动后没检测到进程。请直接双击「启动.bat」试试。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
