"""真实应用联调：在记事本里模拟「输入法上屏 + 停顿」，看悬浮窗是否弹出。

做法：
  1. 打开记事本，拿到它的编辑框（UIA）
  2. 注入几次退格 —— 让键盘钩子感知到「正在打字」，退格不会污染输入法候选
  3. 用 UIA 往编辑框写入中文 —— 等价于输入法把汉字上屏
  4. 静默等待防抖时间，检查运行中的程序日志里是否出现新的翻译记录
"""
from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path

import uiautomation as auto
from pynput import keyboard

APP_DIR = Path(__file__).resolve().parent
LOG = APP_DIR / "logs" / "app.log"

SAMPLE = "这个方案在离线环境下也能稳定运行，我已经测试过了。"
TRIGGER = "这个方案在离线环境下也能稳定运行，我已经测试过了。"
# 末句切分后应该只剩最后一句


def log_lines() -> list[str]:
    try:
        return LOG.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return []


def find_edit(pid: int, timeout: float = 15.0):
    """在记事本窗口里找到多行编辑框。"""
    end = time.time() + timeout
    while time.time() < end:
        win = auto.ControlFromHandle(0)
        for w in auto.GetRootControl().GetChildren():
            try:
                if w.ProcessId != pid:
                    continue
                if "记事本" not in w.Name and "Notepad" not in w.Name:
                    continue
                for c in w.GetChildren():
                    if c.ControlType == auto.ControlType.DocumentControl or \
                       c.ControlType == auto.ControlType.EditControl:
                        return w, c
                    for gc in c.GetChildren():
                        if gc.ControlType in (auto.ControlType.DocumentControl,
                                              auto.ControlType.EditControl):
                            return w, gc
            except Exception:
                continue
        time.sleep(0.5)
    return None, None


def main() -> int:
    before = log_lines()
    print(f"运行前的日志行数：{len(before)}")

    print("启动记事本…")
    p = subprocess.Popen(["notepad.exe"])
    time.sleep(2.0)

    win, edit = find_edit(p.pid)
    if edit is None:
        print("!! 没找到记事本的编辑框")
        p.terminate()
        return 1
    print(f"找到编辑框：{edit.ControlTypeName} / ClassName={edit.ClassName!r}")

    # 聚焦
    try:
        win.SetActive()
        win.SetTopmost(False)
        edit.SetFocus()
    except Exception as e:
        print(f"  （聚焦时的小问题，忽略：{e}）")
    time.sleep(0.8)

    # 1) 注入打字活动信号
    kb = keyboard.Controller()
    for _ in range(4):
        kb.press(keyboard.Key.backspace)
        kb.release(keyboard.Key.backspace)
        time.sleep(0.06)
    print("已注入 4 次退格（打字信号）")

    # 2) 立刻上屏中文（等价于输入法提交）
    t_commit = time.time()
    ok = False
    try:
        vp = edit.GetValuePattern()
        vp.SetValue(SAMPLE)
        ok = True
        print(f"已通过 UIA 写入中文：{SAMPLE}")
    except Exception as e:
        print(f"  ValuePattern 写入失败（{e}），改试键盘输入")

    if not ok:
        p.terminate()
        return 1

    # 3) 等防抖 + 推理
    print("静默等待触发…")
    found = None
    end = time.time() + 20
    while time.time() < end:
        time.sleep(0.4)
        cur = log_lines()
        new = cur[len(before):]
        for line in new:
            if "->" in line and re.search(r"\(\d+ms\)", line):
                found = line
                break
        if found:
            break

    print()
    dt = time.time() - t_commit
    if found:
        print(f"=== 悬浮窗已触发（写入后 {dt:.1f}s）===")
        print("   日志:", found.strip())
    else:
        print(f"=== 未触发（等了 {dt:.1f}s）===")
        print("   新增日志：")
        for line in log_lines()[len(before):]:
            print("     ", line.strip())

    # 收尾：关掉记事本，不保存
    try:
        edit.SetFocus()
        win.SetActive()
    except Exception:
        pass
    p.terminate()

    print()
    print("=== 悬浮窗窗口是否可见 ===")
    vis = False
    for w in auto.GetRootControl().GetChildren():
        try:
            if w.ClassName in ("TkTopLevel", "TkChild") and w.IsOffscreen is False:
                nm = w.Name or ""
                if nm in ("tk", "本地双语翻译"):
                    r = w.BoundingRectangle
                    print(f"   可见窗口 {w.ClassName} title={nm!r} "
                          f"位置=({r.left},{r.top}) 尺寸={r.width()}x{r.height()}")
                    vis = True
        except Exception:
            continue
    if not vis:
        print("   （此刻没有可见的悬浮窗 —— 可能已按 auto_hide_ms 自动淡出）")

    return 0 if found else 2


if __name__ == "__main__":
    sys.exit(main())
