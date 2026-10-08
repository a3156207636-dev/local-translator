"""读取当前焦点输入框里的文本（含已上屏的中文），并给出光标屏幕坐标。

三级策略：
  1. UI Automation —— 覆盖浏览器、Electron、WPF、WinUI、原生控件
  2. Win32 GetGUIThreadInfo + WM_GETTEXT —— 覆盖传统原生编辑框
  3. 剪贴板 —— 手动快捷键模式的兜底
"""
from __future__ import annotations

import ctypes
import os
import re
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Optional

import uiautomation as auto

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

_tls = threading.local()


def ensure_uia() -> None:
    """UI Automation 是 COM 组件，每个线程都要初始化一次。"""
    if getattr(_tls, "ready", False):
        return
    _tls.ready = True
    try:
        auto.InitializeUIAutomationInCurrentThread()
    except Exception:
        pass


CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")

_SENT_END = "。！？!?…\n"
_SENT_SOFT = "，,；;、：:"

# --------------------------------------------------------------------------- #
# UI 噪声识别
# --------------------------------------------------------------------------- #
# 网页/播放器界面上常见的按钮、计数、提示文字。用户真正打字时几乎不会
# 同时出现好几个；一旦出现，说明读到的不是输入框，而是整个页面。
UI_NOISE_TOKENS = (
    "展开", "收起", "加载中", "查看更多", "精彩评论", "评论并", "回复",
    "点赞", "点踩", "转发", "分享到", "举报", "复制链接", "清屏", "连播",
    "弹幕", "超清", "高清", "流畅", "自动连播", "播放量", "听抖音",
    "汽水音乐", "直播切片", "打开App", "打开APP", "下载App", "下载APP",
    "扫码登录", "客户端", "前往设置", "消息私信", "创作者中心",
    "播放中", "倍速", "全屏",
)

# 强特征：数字组成的界面元素（计数 / 时间戳 / 倍速），句子几乎不会含这些
_UI_NUM_RES = (
    re.compile(r"\b\d{1,2}:\d{2}\s*/\s*\d{1,2}:\d{2}\b"),     # 00:00 / 34:30
    re.compile(r"\b\d+\.\d+x\b"),                             # 2.0x 倍速
    re.compile(r"\d(\.\d+)?\s*万\b"),                         # 2.0万
    re.compile(r"\b\d[\d,]{3,}\b"),                           # 1,723 / 20000
)


def looks_like_ui_noise(text: str) -> bool:
    """判断一段文本是不是界面文字而非用户输入。

    自动触发时用它把关：读到的原文里同时出现多个 UI 标志物
    （按钮文字 + 计数/时间戳/倍速），基本可以断定焦点不在输入框上，
    此时不该翻译，否则就会出现「把整个评论区都翻译了」的事故。
    """
    if not text:
        return False
    t = text
    token_hits = sum(1 for tok in UI_NOISE_TOKENS if tok in t)
    regex_hits = sum(1 for rx in _UI_NUM_RES if rx.search(t))
    # 时间戳 / 倍速这类强特征出现两次以上，基本必是播放器界面
    if regex_hits >= 2:
        return True
    if token_hits >= 3 and regex_hits >= 1:
        return True
    if token_hits >= 5:
        return True
    return False

VK_CONTROL = 0x11
VK_C = 0x43
KEYEVENTF_KEYUP = 0x0002
GWL_EXSTYLE = -20


@dataclass
class FocusContext:
    """一次读取的结果。"""

    text: str = ""
    caret: Optional[tuple[int, int, int, int]] = None  # 屏幕坐标 left, top, right, bottom
    source: str = ""
    control: str = ""
    ok: bool = False
    error: str = ""


# --------------------------------------------------------------------------- #
# 文本切分
# --------------------------------------------------------------------------- #
def has_cjk(text: str) -> bool:
    return bool(CJK_RE.search(text or ""))


def _last_sentence(text: str, min_chars: int = 2) -> str:
    """取最后一句。

    按句末标点切分，而不是逗号——否则「今天不错，我们走吧。」会被切掉前半句。
    末尾就是句末标点时，返回整个最后一句（含标点）。
    """
    seg = text.rstrip()
    if not seg:
        return ""
    n = len(seg)
    cuts = [i + 1 for i, ch in enumerate(seg) if ch in _SENT_END]

    # 只在「非结尾」的切点之后取片段；若句末标点收尾，则整句都算最后一句
    inner = [c for c in cuts if c < n]
    idx = len(inner) - 1
    while True:
        start = inner[idx] if idx >= 0 else 0
        cand = seg[start:].strip()
        if len(cand) >= min_chars or idx <= 0:
            return cand
        idx -= 1


def _split_tail(seg: str, min_chars: int) -> str:
    """软切分：长句里用逗号/顿号做兜底切分（仅在明确需要时使用）。"""
    softs = [i for i, ch in enumerate(seg) if ch in _SENT_SOFT]
    for pos in reversed(softs):
        cand = seg[pos + 1 :].strip()
        if len(cand) >= min_chars:
            return cand
    return seg.strip()


def extract_unit(text: str, mode: str = "last_sentence", tail_chars: int = 200,
                 min_chars: int = 2) -> str:
    """按模式从整段文本里取出要翻译的那一小块。"""
    if not text:
        return ""
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    if mode == "all":
        out = t.strip()
    elif mode == "tail":
        out = t[-tail_chars:].strip()
    elif mode == "last_line":
        out = ""
        for line in reversed(t.split("\n")):
            if line.strip():
                out = line.strip()
                break
    elif mode == "last_clause":
        out = _split_tail(t[-400:], min_chars)
    else:  # last_sentence
        out = _last_sentence(t[-800:], min_chars=min_chars)
    return out.strip()


# --------------------------------------------------------------------------- #
# 剪贴板
# --------------------------------------------------------------------------- #
def get_clipboard() -> str:
    try:
        import pyperclip

        return pyperclip.paste() or ""
    except Exception:
        return ""


def set_clipboard(text: str) -> bool:
    try:
        import pyperclip

        pyperclip.copy(text)
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# UI Automation
# --------------------------------------------------------------------------- #
def _own_foreground() -> bool:
    """前台窗口是否属于本进程（避免把悬浮窗自己当输入框）。"""
    try:
        hwnd = user32.GetForegroundWindow()
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value == os.getpid()
    except Exception:
        return False


def _rect_of(rects) -> Optional[tuple[int, int, int, int]]:
    if not rects:
        return None
    try:
        r = rects[-1]
        l, t, rr, b = (r.left, r.top, r.right, r.bottom)
        if rr - l <= 0 or b - t <= 0:
            l, t, rr, b = (r[0], r[1], r[2], r[3])
        if rr - l <= 0 or b - t <= 0:
            return None
        return (int(l), int(t), int(rr), int(b))
    except Exception:
        return None


def _element_caret(el) -> Optional[tuple[int, int, int, int]]:
    try:
        tp = el.GetPattern(auto.PatternId.TextPattern)
    except Exception:
        return None
    if tp is None:
        return None
    try:
        sel = tp.GetSelection()
        if sel:
            return _rect_of(sel[0].GetBoundingRectangles())
    except Exception:
        pass
    return None


def read_uia() -> FocusContext:
    """用 UI Automation 读焦点控件的文本。"""
    ensure_uia()
    ctx = FocusContext(source="uia")
    try:
        el = auto.GetFocusedControl()
    except Exception as e:
        ctx.error = f"GetFocusedControl: {e}"
        return ctx
    if el is None:
        ctx.error = "没有焦点控件"
        return ctx

    try:
        ctx.control = f"{el.ControlTypeName}:{el.Name or ''}"[:80]
    except Exception:
        pass

    text = None
    caret = None

    # TextPattern：能拿到光标前后的精确文本，优先
    try:
        tp = el.GetPattern(auto.PatternId.TextPattern)
    except Exception:
        tp = None

    if tp is not None:
        try:
            sel = tp.GetSelection()
            if sel:
                caret = _rect_of(sel[0].GetBoundingRectangles())
                doc = tp.DocumentRange
                work = doc.Clone()
                moved = work.MoveEndpointByRange(
                    auto.TextPatternRangeEndpoint.End,
                    sel[0],
                    auto.TextPatternRangeEndpoint.Start,
                )
                if moved:
                    text = work.GetText(4000)
        except Exception:
            pass
        if not text:
            try:
                text = tp.DocumentRange.GetText(4000)
            except Exception:
                pass

    # ValuePattern：标准输入框、浏览器 <input>/<textarea>
    if not text:
        try:
            vp = el.GetPattern(auto.PatternId.ValuePattern)
            if vp is not None and vp.Value:
                text = vp.Value
        except Exception:
            pass

    if caret is None:
        caret = _element_caret(el)

    if caret is None:
        try:
            r = el.BoundingRectangle
            if r and r.right > r.left:
                caret = (int(r.left), int(r.bottom), int(r.right), int(r.bottom))
        except Exception:
            pass

    if text:
        ctx.text = text
        ctx.caret = caret
        ctx.ok = True
    else:
        ctx.error = "焦点控件未提供文本"
    return ctx


# --------------------------------------------------------------------------- #
# Win32 兜底
# --------------------------------------------------------------------------- #
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


def read_win32() -> FocusContext:
    ctx = FocusContext(source="win32")
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        ctx.error = "无前台窗口"
        return ctx
    tid = user32.GetWindowThreadProcessId(hwnd, None)
    gti = GUITHREADINFO()
    gti.cbSize = ctypes.sizeof(GUITHREADINFO)
    if not user32.GetGUIThreadInfo(tid, ctypes.byref(gti)):
        ctx.error = "GetGUIThreadInfo 失败"
        return ctx

    target = gti.hwndFocus or gti.hwndCaret or hwnd
    text = ""
    try:
        text = auto.GetEditText(target) or ""
    except Exception as e:
        ctx.error = f"GetEditText: {e}"

    caret = None
    pt = wintypes.POINT()
    if user32.GetCaretPos(ctypes.byref(pt)):
        if user32.ClientToScreen(target, ctypes.byref(pt)):
            h = 18
            caret = (int(pt.x), int(pt.y), int(pt.x) + 2, int(pt.y) + h)

    if not caret:
        r = wintypes.RECT()
        if user32.GetWindowRect(hwnd, ctypes.byref(r)):
            caret = (int(r.left), int(r.bottom) - 24, int(r.right), int(r.bottom))

    if text:
        ctx.text = text
        ctx.caret = caret
        ctx.ok = True
    elif not ctx.error:
        ctx.error = "焦点控件无文本"
    return ctx


def read_focused_context(reader: str = "auto") -> FocusContext:
    """按配置读取焦点上下文。"""
    if _own_foreground():
        return FocusContext(error="前台窗口是本程序自身")

    order = ["uia", "win32"] if reader == "auto" else [reader]
    last = FocusContext(error="未读取")
    for name in order:
        try:
            ctx = read_uia() if name == "uia" else read_win32()
        except Exception as e:
            ctx = FocusContext(source=name, error=str(e))
        if ctx.ok and ctx.text:
            return ctx
        last = ctx
    return last


# --------------------------------------------------------------------------- #
# 选中文本（手动快捷键模式）
# --------------------------------------------------------------------------- #
def _send_ctrl_c() -> None:
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    time.sleep(0.02)
    user32.keybd_event(VK_C, 0, 0, 0)
    time.sleep(0.02)
    user32.keybd_event(VK_C, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.02)
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)


def read_selection(timeout: float = 0.45) -> tuple[str, Optional[tuple[int, int, int, int]]]:
    """复制当前选中文本并还原剪贴板。返回 (文本, 光标矩形)。"""
    caret = None
    try:
        ctx = read_uia()
        caret = ctx.caret
    except Exception:
        pass

    saved = get_clipboard()
    set_clipboard("\x00__lt_marker__")
    _send_ctrl_c()

    deadline = time.time() + timeout
    text = ""
    while time.time() < deadline:
        time.sleep(0.05)
        cur = get_clipboard()
        if cur and cur != "\x00__lt_marker__":
            text = cur
            break

    if saved:
        time.sleep(0.05)
        set_clipboard(saved)
    return text.replace("\x00", "").strip(), caret
