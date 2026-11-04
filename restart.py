"""重启工具：结束正在运行的实例，再重新启动一个。

启动方式用 `cmd /c start` 中转，让新进程脱离当前命令行会话——这样关掉
这个窗口后工具还会继续待在后台。启动后会等两秒确认它真的活着。
"""
from __future__ import annotations

import json
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
    """找出所有在跑本工具的 pythonw 进程。查询失败时抛异常，由 main 报错。"""
    query = (
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
        "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\""
        " | Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
    )
    r = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", query],
        capture_output=True, encoding="utf-8", timeout=30, check=True,
    )
    rows = json.loads(r.stdout) if r.stdout.strip() else []
    if isinstance(rows, dict):  # 只有一个进程时 ConvertTo-Json 输出单个对象
        rows = [rows]
    target = str(APP_DIR).lower()
    return [
        int(p["ProcessId"]) for p in rows
        if "main.py" in (p["CommandLine"] or "") and target in p["CommandLine"].lower()
    ]


def kill(pid: int) -> bool:
    return subprocess.run(
        ["taskkill", "/PID", str(pid), "/F"],
        capture_output=True, timeout=20,
    ).returncode == 0


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

    try:
        old = list_instances()
        if old:
            print(f"正在结束旧实例：{', '.join(str(p) for p in old)}")
            for pid in old:
                # 一次启动有两个进程，结束一个时另一个可能已跟着退出，以下面复查为准
                kill(pid)
            time.sleep(1.5)
            left = list_instances()
            if left:
                # 单实例互斥体还被占用，这时启动新进程只会立刻退出
                print(f"重启失败：旧实例结束不了（PID {left}）。请从状态球右键退出后再试。")
                return 1
        else:
            print("没有正在运行的实例。")

        spawn(["--autostart"])
        time.sleep(2.5)
        now = list_instances()
    except Exception as e:
        print(f"重启失败：{type(e).__name__}: {e}")
        return 1

    if now:
        print(f"已启动，PID {now}")
        print("看屏幕右下角：出现「译 · 待机」小圆球就是好了。")
        return 0
    print("启动后没检测到进程。请直接双击「启动.bat」试试。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
