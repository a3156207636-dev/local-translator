"""设置窗口：按配置项自动生成表单。"""
from __future__ import annotations

import threading
import tkinter as tk
import webbrowser
from tkinter import font as tkfont, messagebox, ttk
from typing import Any, Callable, Optional

import config as cfgmod
import engines as engmod
import license as licmod

SPEC: list[tuple[str, str, str, Any]] = [
    ("__license__", "许可证", "", None),
    ("__section__", "翻译引擎", "", None),
    ("engine.type", "引擎类型", "combo", ["openai", "ollama", "mock"]),
    ("engine.base_url", "接口地址", "text", None),
    ("engine.model", "模型名称", "text", None),
    ("engine.api_key", "API Key", "text", None),
    ("__langpair__", "翻译语言对", "", None),
    ("engine.style", "翻译风格", "combo", ["natural", "native", "literal"]),
    ("engine.temperature", "温度", "float", None),
    ("engine.timeout", "超时（秒）", "float", None),
    ("__section__", "触发方式", "", None),
    ("trigger.auto_translate", "停止打字后自动翻译", "bool", None),
    ("trigger.instant_popup", "按下第一个字就弹出窗口", "bool", None),
    ("trigger.debounce_ms", "停顿判定（毫秒）", "int", None),
    ("trigger.min_chars", "最短字数", "int", None),
    ("trigger.max_chars", "最长字数", "int", None),
    ("trigger.require_cjk", "原文本无中文则跳过", "bool", None),
    ("trigger.hotkey", "手动快捷键", "text", None),
    ("trigger.hotkey_mode", "手动快捷键行为", "combo", ["selection", "clipboard"]),
    ("__section__", "取词范围", "", None),
    ("context.reader", "读取方式", "combo", ["auto", "uia", "win32"]),
    ("context.take", "翻译哪一段", "combo",
     ["last_sentence", "last_clause", "last_line", "tail", "all"]),
    ("__section__", "悬浮窗外观", "", None),
    ("overlay.position", "出现位置", "combo", ["follow_caret", "follow_mouse", "fixed"]),
    ("overlay.width", "宽度（像素）", "int", None),
    ("overlay.opacity", "不透明度", "float", None),
    ("overlay.font_family", "字体", "text", None),
    ("overlay.src_font_size", "原文字号", "int", None),
    ("overlay.dst_font_size", "译文字号", "int", None),
    ("overlay.auto_hide_ms", "自动隐藏（毫秒，0=不隐藏）", "int", None),
    ("overlay.hide_on_typing", "再次打字时隐藏窗口", "bool", None),
    ("overlay.streaming", "流式输出（译文逐字浮现）", "bool", None),
    ("overlay.accent", "强调色", "text", None),
    ("overlay.show_source", "显示原文", "bool", None),
    ("overlay.show_footer", "显示底部信息栏", "bool", None),
    ("__section__", "启动", "", None),
    ("app.autostart", "开机自动启动", "bool", None),
    ("app.autostart_delay_s", "登录后额外等待（秒）", "int", None),
    ("app.show_pill", "显示状态球", "bool", None),
    ("__section__", "行为", "", None),
    ("trigger.cooldown_ms", "两次翻译最小间隔（毫秒）", "int", None),
]


class SettingsWindow:
    def __init__(
        self,
        parent: tk.Misc,
        cfg: dict,
        on_saved: Callable[[dict], None],
        on_test: Callable[[str], None],
        font_family: str = "Microsoft YaHei UI",
    ) -> None:
        self.parent = parent
        self.cfg = cfg
        self.on_saved = on_saved
        self.on_test = on_test
        self.font_family = font_family
        self.vars: dict[str, tk.Variable] = {}
        self._open = False

    # ------------------------------------------------------------------ #
    def open(self) -> None:
        if self._open:
            return
        self._open = True

        win = tk.Toplevel(self.parent)
        self.win = win
        win.title("本地双语翻译 · 设置")
        win.configure(bg="#F4F6F8")
        win.geometry("620x720")
        win.protocol("WM_DELETE_WINDOW", self._close)

        head = tk.Frame(win, bg="#F4F6F8")
        head.pack(fill="x", padx=18, pady=(16, 8))
        tk.Label(head, text="设置", font=(self.font_family, 15, "bold"),
                 bg="#F4F6F8", fg="#1F2933").pack(side="left")
        self.status = tk.Label(head, text="", font=(self.font_family, 9),
                               bg="#F4F6F8", fg="#6B7680")
        self.status.pack(side="right")

        body = tk.Frame(win, bg="#F4F6F8")
        body.pack(fill="both", expand=True, padx=18)

        canvas = tk.Canvas(body, bg="#F4F6F8", highlightthickness=0)
        sb = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg="#F4F6F8")
        inner.bind("<Configure>",
                   lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw", width=560)
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        canvas.bind_all("<MouseWheel>",
                        lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))

        for path, label, kind, extra in SPEC:
            if path == "__section__":
                sec = tk.Label(inner, text=label, font=(self.font_family, 10, "bold"),
                               bg="#F4F6F8", fg="#185FA5", anchor="w")
                sec.pack(fill="x", pady=(14, 4))
                continue
            if path == "__license__":
                self._add_license_block(inner)
                continue
            if path == "__langpair__":
                self._add_langpair_block(inner)
                continue
            self._add_row(inner, path, label, kind, extra)

        foot = tk.Frame(win, bg="#F4F6F8")
        foot.pack(fill="x", padx=18, pady=14)

        self._button(foot, "保存并应用", self._save, primary=True).pack(side="right")
        self._button(foot, "关闭", self._close).pack(side="right", padx=(0, 8))
        self._button(foot, "检测本机服务", self._probe).pack(side="left")
        self._button(foot, "测试翻译", self._test).pack(side="left", padx=(8, 0))

        win.attributes("-topmost", True)
        win.after(300, lambda: win.attributes("-topmost", False))
        win.focus_force()

    # ------------------------------------------------------------------ #
    def _button(self, parent: tk.Misc, text: str, cmd, primary: bool = False) -> tk.Label:
        bg = "#185FA5" if primary else "#EDEFF2"
        fg = "#FFFFFF" if primary else "#3A434C"
        lb = tk.Label(parent, text=text, font=(self.font_family, 10), bg=bg, fg=fg,
                      padx=14, pady=6, cursor="hand2")
        hover = "#144C86" if primary else "#E0E4E8"
        lb.bind("<Button-1>", lambda _e: cmd())
        lb.bind("<Enter>", lambda _e: lb.configure(bg=hover))
        lb.bind("<Leave>", lambda _e: lb.configure(bg=bg))
        return lb

    # ------------------------------------------------------------------ #
    # 许可证
    # ------------------------------------------------------------------ #
    def _add_license_block(self, parent: tk.Misc) -> None:
        wrap = tk.Frame(parent, bg="#EAF1FA", highlightthickness=1,
                        highlightbackground="#C7D9EC")
        wrap.pack(fill="x", pady=(10, 4))

        head = tk.Frame(wrap, bg="#EAF1FA")
        head.pack(fill="x", padx=12, pady=(10, 0))
        tk.Label(head, text="许可证", font=(self.font_family, 10, "bold"),
                 bg="#EAF1FA", fg="#185FA5").pack(side="left")
        self.lic_state = tk.Label(head, text="", font=(self.font_family, 9),
                                  bg="#EAF1FA", fg="#3A434C")
        self.lic_state.pack(side="right")

        row = tk.Frame(wrap, bg="#EAF1FA")
        row.pack(fill="x", padx=12, pady=(6, 2))
        self.lic_key = tk.StringVar()
        self.lic_entry = tk.Entry(row, textvariable=self.lic_key,
                                  font=(self.font_family, 10), width=28,
                                  relief="solid", bd=1)
        self.lic_entry.pack(side="left", fill="x", expand=True)
        self.lic_btn = self._button(row, "激活", self._on_license_button, primary=True)
        self.lic_btn.pack(side="left", padx=(8, 0))

        self.lic_hint = tk.Label(wrap, text="", font=(self.font_family, 9),
                                 bg="#EAF1FA", fg="#6B7680", justify="left",
                                 wraplength=520, anchor="w")
        self.lic_hint.pack(fill="x", padx=12, pady=(4, 0))

        self.lic_link = tk.Label(wrap, text="升级 Pro / 购买许可证 →",
                                 font=(self.font_family, 9, "underline"),
                                 bg="#EAF1FA", fg="#185FA5", cursor="hand2")
        self.lic_link.pack(anchor="w", padx=12, pady=(2, 10))
        self._lic_link_target = licmod.PRODUCT_URL
        self.lic_link.bind(
            "<Button-1>", lambda _e: webbrowser.open(self._lic_link_target))
        self._refresh_license_ui()

    def _refresh_license_ui(self, hint: str = "") -> None:
        pro = licmod.is_pro()
        if pro:
            st = licmod.load_state()
            who = st.get("email") or st.get("key", "")
            is_max = licmod.is_max()
            self.lic_state.configure(
                text="Max 已激活" if is_max else "Pro 已激活", fg="#2E7D32")
            self.lic_key.set("")
            self.lic_entry.configure(state="disabled")
            self.lic_btn.configure(text="解除绑定")
            if not hint:
                if is_max:
                    hint = (f"无限翻译 · 任意语言互译 · 7B+ 大模型 · 开机自启\n绑定：{who}")
                else:
                    hint = (f"无限翻译 · 已解锁 7B+ 大模型 · 开机自启可用\n绑定：{who}")
            self.lic_hint.configure(text=hint, fg="#2E7D32")
        else:
            self.lic_state.configure(
                text=f"免费版 · 今日 {licmod.usage_today()}/{licmod.FREE_DAILY_LIMIT}",
                fg="#B26A00")
            self.lic_entry.configure(state="normal")
            self.lic_btn.configure(text="激活")
            if not hint:
                hint = (f"免费版：每日 {licmod.FREE_DAILY_LIMIT} 次翻译，只能用 3B 及以下的小模型，"
                        f"不支持开机自启。\n把购买后收到的密钥粘贴到上面 → 点「激活」即可解锁 Pro / Max。")
            self.lic_hint.configure(text=hint, fg="#6B7680")

        # 升级链接：免费→Pro，Pro→Max，已是 Max 则隐藏
        if licmod.is_max():
            self._lic_link_target = ""
            self.lic_link.configure(text="")
        elif licmod.is_pro():
            self._lic_link_target = licmod.MAX_PRODUCT_URL
            self.lic_link.configure(text="升级到 Max（任意语言互译）→")
        else:
            self._lic_link_target = licmod.PRODUCT_URL
            self.lic_link.configure(text="升级 Pro / 购买许可证 →")

    def _on_license_button(self) -> None:
        if licmod.is_pro():
            licmod.deactivate()
            self._refresh_license_ui(hint="已解除绑定，本机将回到免费版。")
            return
        key = self.lic_key.get().strip()
        if not key:
            self._refresh_license_ui(hint="请先粘贴 Gumroad 发来的许可证密钥。")
            return
        self.lic_hint.configure(text="正在向 Gumroad 验证，请稍候…", fg="#6B7680")

        def work() -> None:
            ok, msg = licmod.activate(key)
            try:
                self.win.after(0, lambda: self._refresh_license_ui(hint=msg))
                # Max 激活后放开语言对下拉框
                self.win.after(0, self._refresh_langpair)
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------------ #
    def _add_row(self, parent: tk.Misc, path: str, label: str, kind: str, extra: Any) -> None:
        row = tk.Frame(parent, bg="#F4F6F8")
        row.pack(fill="x", pady=3)
        tk.Label(row, text=label, font=(self.font_family, 10), bg="#F4F6F8",
                 fg="#3A434C", width=22, anchor="w").pack(side="left")

        cur = cfgmod.get(self.cfg, path)
        if kind == "bool":
            var: tk.Variable = tk.BooleanVar(value=bool(cur))
            tk.Checkbutton(row, variable=var, bg="#F4F6F8", activebackground="#F4F6F8",
                           highlightthickness=0).pack(side="left")
        elif kind == "combo":
            var = tk.StringVar(value=str(cur))
            ttk.Combobox(row, textvariable=var, values=extra, width=32,
                         state="readonly").pack(side="left", fill="x", expand=True)
        else:
            var = tk.StringVar(value="" if cur is None else str(cur))
            tk.Entry(row, textvariable=var, font=(self.font_family, 10), width=34,
                     relief="solid", bd=1).pack(side="left", fill="x", expand=True)
        self.vars[path] = var

        if kind in ("int", "float"):
            var._kind = kind  # type: ignore[attr-defined]

    # ------------------------------------------------------------------ #
    # 语言对选择器（受档位限制）
    # ------------------------------------------------------------------ #
    def _add_langpair_block(self, parent: tk.Misc) -> None:
        wrap = tk.Frame(parent, bg="#F4F6F8")
        wrap.pack(fill="x", pady=3)
        tk.Label(wrap, text="翻译语言对", font=(self.font_family, 10), bg="#F4F6F8",
                 fg="#3A434C", width=22, anchor="w").pack(side="left")

        max_tier = licmod.is_max()
        code_to_disp = {c: d for c, d, _p in licmod.LANGUAGES}
        self._lang_code_by_display = {d: c for c, d, _p in licmod.LANGUAGES}
        disp_list = [d for _c, d, _p in licmod.LANGUAGES]

        cur_src = cfgmod.get(self.cfg, "engine.source_lang") or "zh"
        cur_tgt = cfgmod.get(self.cfg, "engine.target_lang") or "en"
        cur_src_d = code_to_disp.get(cur_src, cur_src)
        cur_tgt_d = code_to_disp.get(cur_tgt, cur_tgt)

        row = tk.Frame(wrap, bg="#F4F6F8")
        row.pack(side="left")

        self.src_var = tk.StringVar(value=cur_src_d)
        self.tgt_var = tk.StringVar(value=cur_tgt_d)
        if max_tier:
            values = disp_list
            state = "readonly"
        else:
            values = [code_to_disp["zh"], code_to_disp["en"]]
            state = "disabled"
            self.src_var.set(code_to_disp["zh"])
            self.tgt_var.set(code_to_disp["en"])

        cb_src = ttk.Combobox(row, textvariable=self.src_var, values=values,
                             width=16, state=state)
        cb_src.pack(side="left")
        tk.Label(row, text="→", bg="#F4F6F8", font=(self.font_family, 10)).pack(
            side="left", padx=4)
        cb_tgt = ttk.Combobox(row, textvariable=self.tgt_var, values=values,
                             width=16, state=state)
        cb_tgt.pack(side="left")

        # 注册进 vars，让 _collect 一起保存
        self.vars["engine.source_lang"] = self.src_var
        self.vars["engine.target_lang"] = self.tgt_var
        self._langpair_widgets = (cb_src, cb_tgt)

        self.lang_lock = tk.Label(wrap, text="", font=(self.font_family, 9),
                                  bg="#F4F6F8", fg="#B26A00", wraplength=200)
        self.lang_lock.pack(side="left", padx=(10, 0))
        if not max_tier:
            self.lang_lock.configure(
                text=f"免费/Pro 固定为 中文→英文；任意语言互译是 Max 功能")

        self._refresh_langpair_lock_link(max_tier, wrap)

    def _refresh_langpair_lock_link(self, max_tier: bool, parent: tk.Misc) -> None:
        if max_tier:
            return
        link = tk.Label(parent, text="升级 Max →", font=(self.font_family, 9, "underline"),
                        bg="#F4F6F8", fg="#185FA5", cursor="hand2")
        link.pack(side="left", padx=(2, 0))
        link.bind("<Button-1>", lambda _e: webbrowser.open(licmod.MAX_PRODUCT_URL))

    def _refresh_langpair(self) -> None:
        """激活 Max 后把下拉框放开成全部语言。"""
        max_tier = licmod.is_max()
        code_to_disp = {c: d for c, d, _p in licmod.LANGUAGES}
        cb_src, cb_tgt = self._langpair_widgets
        if max_tier:
            disp_list = [d for _c, d, _p in licmod.LANGUAGES]
            cb_src.configure(values=disp_list, state="readonly")
            cb_tgt.configure(values=disp_list, state="readonly")
            self.lang_lock.configure(text="", fg="#6B7680")
        else:
            cb_src.configure(values=[code_to_disp["zh"]], state="disabled")
            cb_tgt.configure(values=[code_to_disp["en"]], state="disabled")
            self.src_var.set(code_to_disp["zh"])
            self.tgt_var.set(code_to_disp["en"])

    # ------------------------------------------------------------------ #
    def _collect(self) -> dict:
        new = cfgmod.load()
        for path, var in self.vars.items():
            kind = getattr(var, "_kind", None)
            raw = var.get()
            if path in ("engine.source_lang", "engine.target_lang"):
                # 下拉框显示中文名，存配置时换回代码
                val: Any = self._lang_code_by_display.get(raw, raw)
            elif kind == "int":
                try:
                    val = int(float(raw))
                except Exception:
                    continue
            elif kind == "float":
                try:
                    val = float(raw)
                except Exception:
                    continue
            else:
                val = raw
            node = new
            parts = path.split(".")
            for p in parts[:-1]:
                node = node.setdefault(p, {})
            node[parts[-1]] = val
        return new

    def _save(self) -> None:
        new = self._collect()
        cfgmod.save(new)
        self.cfg.clear()
        self.cfg.update(new)
        try:
            self.on_saved(new)
        except Exception:
            pass
        self.status.configure(text="已保存", fg="#2E7D32")
        self.win.after(1500, lambda: self.status.configure(text=""))

    def _test(self) -> None:
        self._save()
        sample = "这个方案在离线环境下也能稳定运行。"
        self.status.configure(text="测试中…", fg="#6B7680")
        self.on_test(sample)

    def _probe(self) -> None:
        self.status.configure(text="扫描本机端口…", fg="#6B7680")

        def work() -> None:
            found = engmod.probe_local_endpoints()
            self.win.after(0, lambda: self._show_probe(found))

        threading.Thread(target=work, daemon=True).start()

    def _show_probe(self, found: list[dict]) -> None:
        self.status.configure(text="")
        if not found:
            messagebox.showinfo(
                "检测结果",
                "没有在本机常见端口上发现翻译服务。\n\n"
                "常见端口：\n"
                "  Ollama      127.0.0.1:11434\n"
                "  LM Studio   127.0.0.1:1234\n"
                "  llama.cpp   127.0.0.1:8080\n\n"
                "先启动其中一个，再回来检测。",
                parent=self.win,
            )
            return

        dlg = tk.Toplevel(self.win)
        dlg.title("本机可用翻译服务")
        dlg.configure(bg="#F4F6F8")
        dlg.geometry("520x340")
        tk.Label(dlg, text="选中一项后点「使用」，会自动填好地址与模型",
                 font=(self.font_family, 9), bg="#F4F6F8", fg="#6B7680").pack(
            anchor="w", padx=14, pady=(12, 6))

        lb = tk.Listbox(dlg, font=("Consolas", 10), relief="solid", bd=1)
        lb.pack(fill="both", expand=True, padx=14)
        rows: list[tuple[str, str, str]] = []
        for item in found:
            for m in item["models"] or [""]:
                rows.append((item["name"], item["host"], m))
                lb.insert("end", f"  {item['name']:<18} {item['host']:<26} {m}")

        def use() -> None:
            sel = lb.curselection()
            if not sel:
                return
            name, host, model = rows[sel[0]]
            kind = "ollama" if name == "Ollama" else "openai"
            base = host + ("/v1" if kind == "openai" else "")
            self.vars["engine.type"].set(kind)          # type: ignore[union-attr]
            self.vars["engine.base_url"].set(base)      # type: ignore[union-attr]
            if model:
                self.vars["engine.model"].set(model)    # type: ignore[union-attr]
            dlg.destroy()
            self.status.configure(text=f"已选用 {name}", fg="#2E7D32")

        bar = tk.Frame(dlg, bg="#F4F6F8")
        bar.pack(fill="x", padx=14, pady=12)
        self._button(bar, "使用", use, primary=True).pack(side="right")
        self._button(bar, "取消", dlg.destroy).pack(side="right", padx=(0, 8))

    def _close(self) -> None:
        self._open = False
        try:
            self.win.destroy()
        except Exception:
            pass
