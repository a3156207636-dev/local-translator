"""双边悬浮窗 + 状态球，全部基于 tkinter，无额外 UI 依赖。

要点：
  - 无边框、置顶、半透明、可拖动
  - 通过 WS_EX_NOACTIVATE 保证不抢焦点，打字不被打断
  - 所有窗口操作都在 tkinter 主线程内完成，外部线程通过队列投递
"""
from __future__ import annotations

import ctypes
import logging
import queue
import tkinter as tk
from ctypes import wintypes
from tkinter import font as tkfont
from typing import Any, Callable, Optional

log = logging.getLogger("lbt")

GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
SW_SHOWNOACTIVATE = 4


def _apply_no_activate(win: tk.Misc) -> None:
    """给窗口加上「不激活、不进 Alt+Tab」的扩展样式。"""
    try:
        win.update_idletasks()
        u32 = ctypes.windll.user32
        hwnd = u32.GetParent(win.winfo_id())
        if not hwnd or hwnd == u32.GetDesktopWindow():
            hwnd = win.winfo_id()
        style = u32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        u32.SetWindowLongW(
            hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
        )
    except Exception:
        pass


class FloatingUI:
    """管理悬浮窗与状态球。"""

    def __init__(
        self,
        cfg: dict,
        on_settings: Callable[[], None],
        on_quit: Callable[[], None],
        on_toggle_pause: Callable[[], bool],
        on_probe: Callable[[], None],
        on_autostart: Optional[Callable[[bool], bool]] = None,
    ) -> None:
        self.cfg = cfg
        self.on_settings = on_settings
        self.on_quit = on_quit
        self.on_toggle_pause = on_toggle_pause
        self.on_probe = on_probe
        self.on_autostart = on_autostart

        self.q: queue.Queue[tuple] = queue.Queue()      # 外部线程 → 界面
        self.out_q: queue.Queue[tuple] = queue.Queue()  # 界面 → 外部线程（菜单动作等）
        self._pinned = False
        self._hide_job: Optional[str] = None
        self._last_payload: dict[str, Any] = {}
        self._last_render: dict[str, Any] = {}
        self._user_closed = False          # 用户手动关掉后，不因打字而立刻再弹
        self._styles_applied = False
        self._shown_once = False

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("本地双语翻译")

        ov = cfg.get("overlay", {})
        self.font_family = ov.get("font_family", "Microsoft YaHei UI")
        self.width = int(ov.get("width", 480))
        self.accent = ov.get("accent", "#185FA5")

        self._build_overlay()
        self._build_pill()
        self.root.after(40, self._pump)

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _build_overlay(self) -> None:
        ov = self.cfg.get("overlay", {})
        w = tk.Toplevel(self.root)
        w.withdraw()
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.attributes("-alpha", float(ov.get("opacity", 0.97)))
        w.configure(bg="#D8DDE3")
        self.win = w

        outer = tk.Frame(w, bg="#D8DDE3")
        outer.pack(fill="both", expand=True, padx=1, pady=1)

        card = tk.Frame(outer, bg="#FFFFFF")
        card.pack(fill="both", expand=True)

        accent = tk.Frame(card, bg=self.accent, width=3)
        accent.pack(side="left", fill="y")

        body = tk.Frame(card, bg="#FFFFFF")
        body.pack(side="left", fill="both", expand=True, padx=(12, 12), pady=(10, 8))
        self.body = body

        inner = self.width - 40

        self.src_font = tkfont.Font(family=self.font_family,
                                    size=int(ov.get("src_font_size", 11)))
        self.dst_font = tkfont.Font(family=self.font_family,
                                    size=int(ov.get("dst_font_size", 15)))
        self.meta_font = tkfont.Font(family=self.font_family, size=9)
        self.hdr_font = tkfont.Font(family=self.font_family, size=9, weight="bold")

        self.hdr = tk.Label(body, text="原文", font=self.hdr_font, fg="#8A9199",
                            bg="#FFFFFF", anchor="w")
        self.hdr.pack(fill="x")

        self.src_label = tk.Label(
            body, text="", font=self.src_font, fg="#5F6B76", bg="#FFFFFF",
            anchor="w", justify="left", wraplength=inner,
        )
        self.src_label.pack(fill="x", pady=(2, 0))

        self.sep = tk.Frame(body, bg="#EDEFF2", height=1)
        self.sep.pack(fill="x", pady=8)

        self.dst_label = tk.Label(
            body, text="", font=self.dst_font, fg="#1F2933", bg="#FFFFFF",
            anchor="w", justify="left", wraplength=inner,
        )
        self.dst_label.pack(fill="x")

        self.footer = tk.Frame(body, bg="#FFFFFF")
        self.footer.pack(fill="x", pady=(10, 0))

        self.meta = tk.Label(self.footer, text="", font=self.meta_font,
                             fg="#9AA3AB", bg="#FFFFFF", anchor="w")
        self.meta.pack(side="left")

        self.btn_close = self._mk_button(self.footer, "×", lambda: self.hide_overlay(user=True))
        self.btn_close.pack(side="right")
        self.btn_pin = self._mk_button(self.footer, "固定", self._toggle_pin)
        self.btn_pin.pack(side="right", padx=(0, 6))
        self.btn_copy = self._mk_button(self.footer, "复制", self._copy)
        self.btn_copy.pack(side="right", padx=(0, 6))

        if not ov.get("show_footer", True):
            self.footer.pack_forget()

        for widget in (card, body, self.hdr, self.src_label, self.dst_label,
                       self.sep, self.footer, self.meta, outer):
            widget.bind("<Button-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)

        w.bind("<Escape>", lambda _e: self.hide_overlay(user=True))

    def _mk_button(self, parent: tk.Misc, text: str, cmd: Callable[[], None]) -> tk.Label:
        lb = tk.Label(parent, text=text, font=self.meta_font, fg="#6B7680",
                      bg="#F2F4F6", padx=7, pady=2, cursor="hand2")
        lb.bind("<Button-1>", lambda _e: cmd())
        lb.bind("<Enter>", lambda _e: lb.configure(bg="#E4E8EC"))
        lb.bind("<Leave>", lambda _e: lb.configure(bg="#F2F4F6"))
        return lb

    def _build_pill(self) -> None:
        show = bool(self.cfg.get("app", {}).get("show_pill", True))
        p = tk.Toplevel(self.root)
        p.overrideredirect(True)
        p.attributes("-topmost", True)
        p.attributes("-alpha", 0.88)
        p.configure(bg="#D8DDE3")
        self.pill = p

        inner = tk.Frame(p, bg="#FFFFFF")
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        self.pill_label = tk.Label(
            inner, text="译 · 待机", font=(self.font_family, 9),
            fg="#5F6B76", bg="#FFFFFF", padx=10, pady=4, cursor="hand2",
        )
        self.pill_label.pack()

        for widget in (inner, self.pill_label):
            widget.bind("<Button-1>", self._pill_drag_start)
            widget.bind("<B1-Motion>", self._pill_drag_move)
            widget.bind("<Button-3>", self._pill_menu)

        self.menu = tk.Menu(p, tearoff=0)
        self.menu.add_command(label="暂停 / 继续", command=self._menu_pause)
        self.menu.add_command(label="打开设置", command=lambda: self.on_settings())
        self.menu.add_command(label="检测本机翻译服务", command=lambda: self.on_probe())
        self.menu.add_command(label="启动本机引擎", command=self._menu_start_engine)
        self.menu.add_separator()
        self.autostart_var = tk.BooleanVar(value=False)
        self.menu.add_checkbutton(
            label="开机自动启动", variable=self.autostart_var,
            command=self._menu_autostart,
        )
        self.menu.add_separator()
        self.menu.add_command(label="翻译剪贴板内容", command=self._menu_clip)
        self.menu.add_separator()
        self.menu.add_command(label="退出", command=lambda: self.on_quit())

        w, h = 96, 28
        sw = p.winfo_screenwidth()
        sh = p.winfo_screenheight()
        p.geometry(f"{w}x{h}+{sw - w - 24}+{sh - h - 120}")
        if not show:
            p.withdraw()
        self._pill_drag: tuple[int, int] = (0, 0)

    # ------------------------------------------------------------------ #
    # 拖动
    # ------------------------------------------------------------------ #
    def _drag_start(self, e: tk.Event) -> None:
        self._drag = (e.x_root - self.win.winfo_x(), e.y_root - self.win.winfo_y())
        self._cancel_hide()

    def _drag_move(self, e: tk.Event) -> None:
        dx, dy = getattr(self, "_drag", (0, 0))
        self.win.geometry(f"+{e.x_root - dx}+{e.y_root - dy}")

    def _pill_drag_start(self, e: tk.Event) -> None:
        self._pill_drag = (e.x_root - self.pill.winfo_x(), e.y_root - self.pill.winfo_y())

    def _pill_drag_move(self, e: tk.Event) -> None:
        dx, dy = self._pill_drag
        self.pill.geometry(f"+{e.x_root - dx}+{e.y_root - dy}")

    def _pill_menu(self, e: tk.Event) -> None:
        try:
            self.menu.tk_popup(e.x_root, e.y_root)
        finally:
            self.menu.grab_release()

    def _menu_pause(self) -> None:
        paused = self.on_toggle_pause()
        self.set_status("已暂停" if paused else "待机")

    def _menu_clip(self) -> None:
        self.post_out(("clipboard_translate",))

    def _menu_start_engine(self) -> None:
        self.post_out(("start_engine",))

    def _menu_autostart(self) -> None:
        want = bool(self.autostart_var.get())
        if self.on_autostart is None:
            return
        try:
            real = self.on_autostart(want)
        except Exception:
            real = not want
        self.autostart_var.set(bool(real))
        self.set_status("已设自启" if real else "待机")

    def sync_autostart(self, enabled: bool) -> None:
        try:
            self.autostart_var.set(bool(enabled))
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # 定位
    # ------------------------------------------------------------------ #
    def _place(self, caret: Optional[tuple[int, int, int, int]]) -> None:
        ov = self.cfg.get("overlay", {})
        mode = ov.get("position", "follow_caret")
        self.win.update_idletasks()
        w = self.win.winfo_width() or self.width
        h = self.win.winfo_height() or 120

        if mode == "fixed":
            x, y = int(ov.get("fixed_x", 1200)), int(ov.get("fixed_y", 240))
        elif mode == "follow_mouse":
            try:
                pt = wintypes.POINT()
                ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
                x, y = pt.x + 16, pt.y + 20
            except Exception:
                x, y = 400, 300
        else:  # follow_caret
            if caret:
                x, y = caret[0], caret[3] + 10
            else:
                try:
                    pt = wintypes.POINT()
                    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
                    x, y = pt.x + 16, pt.y + 20
                except Exception:
                    x, y = 400, 300

        sw = self.win.winfo_screenwidth()
        sh = self.win.winfo_screenheight()
        # 固定宽度，避免刚弹出（原文还空着）时窗口很窄、内容到了又猛地变宽
        w = max(240, min(int(self.width), sw - 16))
        try:
            h = max(70, int(self.win.winfo_reqheight()))
        except Exception:
            h = 120
        x = max(8, min(x, sw - w - 8))
        if y + h > sh - 8:
            y = max(8, (caret[1] - h - 10) if caret else (sh - h - 60))
        self.win.geometry(f"{w}x{h}+{int(x)}+{int(y)}")

    # ------------------------------------------------------------------ #
    # 展示
    # ------------------------------------------------------------------ #
    def show_translation(
        self,
        src: str,
        dst: str = "",
        engine: str = "",
        elapsed_ms: int = 0,
        caret: Optional[tuple[int, int, int, int]] = None,
        pending: bool = False,
        error: str = "",
        first_ms: int = 0,
    ) -> None:
        ov = self.cfg.get("overlay", {})
        if not ov.get("show_source", True):
            self.hdr.pack_forget()
            self.src_label.pack_forget()
            self.sep.pack_forget()

        if pending:
            self.hdr.configure(text="原文", fg="#8A9199")
            self.src_label.configure(text=src, fg="#5F6B76")
            self.dst_label.configure(text="翻译中…", fg="#9AA3AB")
            self.meta.configure(text="本地模型推理中")
        elif error:
            self.hdr.configure(text="原文", fg="#8A9199")
            self.src_label.configure(text=src, fg="#5F6B76")
            self.dst_label.configure(text="翻译失败：" + error, fg="#C0392B")
            self.meta.configure(text=engine)
        else:
            self.hdr.configure(text="原文", fg="#8A9199")
            self.src_label.configure(text=src, fg="#5F6B76")
            self.dst_label.configure(text=dst, fg="#1F2933")
            self.meta.configure(text=self._meta_line(engine, elapsed_ms, first_ms))

        self._last_payload = {"src": src, "dst": dst}
        self._last_render = {
            "src": src, "dst": dst, "engine": engine,
            "elapsed_ms": elapsed_ms, "error": error, "first_ms": first_ms,
        }
        self._user_closed = False
        self._reveal(caret)
        if not pending and not self._pinned and not error:
            self._start_hide()

    @staticmethod
    def _meta_line(engine: str, elapsed_ms: int, first_ms: int = 0) -> str:
        bits = [b for b in (engine,) if b]
        if elapsed_ms:
            s = f"{elapsed_ms} ms"
            if first_ms and first_ms < elapsed_ms:
                s += f" · 首字 {first_ms} ms"
            bits.append(s)
        return " · ".join(bits)

    def _reveal(self, caret: Optional[tuple[int, int, int, int]]) -> None:
        """把窗口显示出来并摆好位置。"""
        try:
            self.win.deiconify()
            self.win.lift()
            self.win.attributes("-topmost", True)
            if not self._styles_applied:
                _apply_no_activate(self.win)
                _apply_no_activate(self.pill)
                self._styles_applied = True
            self._place(caret)
            self._cancel_hide()
            self._shown_once = True
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # 瞬间弹出 / 流式更新
    # ------------------------------------------------------------------ #
    def popup(self, caret: Optional[tuple[int, int, int, int]] = None) -> None:
        """打字刚一开始就把窗口顶出来，不等翻译结果。

        这样「按下第一个字 → 窗口出现」几乎是同一瞬间，而不是等几百毫秒。
        已经可见时只把它提到最前，不动内容（避免把上一次译文换成占位符）。
        """
        if self._user_closed:
            return
        try:
            visible = self.win.state() == "normal"
        except Exception:
            visible = False
        if visible:
            try:
                self.win.lift()
                self.win.attributes("-topmost", True)
            except Exception:
                pass
            self._cancel_hide()
            return

        log.debug("instant popup：立刻显示窗口")
        r = self._last_render
        if r.get("src") or r.get("dst"):
            # 立刻复用上一次的内容占位，稍后被新译文覆盖
            self.show_translation(
                r.get("src", ""), dst=r.get("dst", ""), engine=r.get("engine", ""),
                elapsed_ms=int(r.get("elapsed_ms", 0)), caret=caret,
                error=r.get("error", ""), first_ms=int(r.get("first_ms", 0)),
            )
        else:
            self.show_translation("", dst="正在翻译…", caret=caret)

    def update_dst(self, src: str, dst: str,
                   caret: Optional[tuple[int, int, int, int]] = None) -> None:
        """流式刷新译文，不重新定位、不闪。"""
        try:
            if src:
                self.src_label.configure(text=src)
            self.dst_label.configure(text=dst or "…", fg="#1F2933")
            self._last_payload = {"src": src, "dst": dst}
        except Exception:
            pass
        if not self._shown_once:
            self._reveal(caret)

    def set_status(self, text: str) -> None:
        try:
            self.pill_label.configure(text=f"译 · {text}")
        except Exception:
            pass

    def _copy(self) -> None:
        txt = self._last_payload.get("dst") or ""
        if not txt:
            return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(txt)
            self.btn_copy.configure(text="已复制", bg="#D6E9DA")
            self.root.after(900, lambda: self.btn_copy.configure(text="复制", bg="#F2F4F6"))
        except Exception:
            pass

    def _toggle_pin(self) -> None:
        self._pinned = not self._pinned
        self.btn_pin.configure(text="已固定" if self._pinned else "固定",
                               bg="#D6E9DA" if self._pinned else "#F2F4F6")
        if self._pinned:
            self._cancel_hide()
        else:
            self._start_hide()

    def _start_hide(self) -> None:
        ms = int(self.cfg.get("overlay", {}).get("auto_hide_ms", 8000))
        if ms > 0:
            self._hide_job = self.root.after(ms, self.hide_overlay)

    def _cancel_hide(self) -> None:
        if self._hide_job:
            try:
                self.root.after_cancel(self._hide_job)
            except Exception:
                pass
            self._hide_job = None

    def hide_overlay(self, user: bool = False) -> None:
        self._cancel_hide()
        if user:
            # 用户主动关掉：本次输入中途不要再因为打字而弹出来
            self._user_closed = True
        try:
            self.win.withdraw()
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # 跨线程投递
    # ------------------------------------------------------------------ #
    def post(self, msg: tuple) -> None:
        """外部线程 → 界面线程。"""
        self.q.put(msg)

    def post_out(self, msg: tuple) -> None:
        """界面线程 → 外部线程（由 App 的 worker 消费）。"""
        self.out_q.put(msg)

    def _pump(self) -> None:
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "show":
                    self.show_translation(*msg[1], **msg[2])
                elif kind == "pending":
                    self.show_translation(msg[1], pending=True, caret=msg[2])
                elif kind == "error":
                    self.show_translation(msg[1], error=msg[2], caret=msg[3])
                elif kind == "popup":
                    self.popup(msg[1] if len(msg) > 1 else None)
                elif kind == "delta":
                    self.update_dst(msg[1], msg[2], msg[3] if len(msg) > 3 else None)
                elif kind == "hide":
                    self.hide_overlay()
                elif kind == "status":
                    self.set_status(msg[1])
                elif kind == "title":
                    try:
                        self.win.title(msg[1])
                    except Exception:
                        pass
                elif kind == "quit":
                    self.root.quit()
                    return
        except queue.Empty:
            pass
        except Exception:
            pass
        self.root.after(40, self._pump)

    def run(self) -> None:
        self.root.mainloop()

    # ------------------------------------------------------------------ #
    def apply_live(self, cfg: dict) -> None:
        """配置改动后尽量不动窗口地热更新外观。"""
        ov = cfg.get("overlay", {})
        try:
            self.width = int(ov.get("width", self.width))
            inner = self.width - 40
            for lb in (self.src_label, self.dst_label):
                lb.configure(wraplength=inner)
            self.src_font.configure(size=int(ov.get("src_font_size", 11)))
            self.dst_font.configure(size=int(ov.get("dst_font_size", 15)))
            self.win.attributes("-alpha", float(ov.get("opacity", 0.97)))
            self.accent = ov.get("accent", self.accent)
        except Exception:
            pass
        show_pill = bool(cfg.get("app", {}).get("show_pill", True))
        try:
            if show_pill:
                self.pill.deiconify()
            else:
                self.pill.withdraw()
        except Exception:
            pass
