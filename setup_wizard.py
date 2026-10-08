"""首次使用向导（tkinter）。

做三件事：
  1. 报告本机情况（系统 / 显卡 / 内存），并推荐一个合适的本地模型
  2. 让使用者二选一：
       本地 Ollama —— 完全离线，一键下载安装 + 自动拉模型
       在线 API   —— 填服务商和 Key，立刻可用
  3. 写 config.json，可选创建桌面快捷方式、开机自启

所有耗时操作都在后台线程，界面不卡。
"""
from __future__ import annotations

import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))

import config as cfgmod          # noqa: E402
import hardware                  # noqa: E402
import license as licmod         # noqa: E402
import ollama_setup              # noqa: E402
import portable_setup            # noqa: E402

FONT = ("Microsoft YaHei UI", 10)
FONT_B = ("Microsoft YaHei UI", 10, "bold")
FONT_TITLE = ("Microsoft YaHei UI", 15, "bold")
FONT_SMALL = ("Microsoft YaHei UI", 9)

# 在线 API 预设：名称 -> (base_url, 默认模型, 申请地址)
API_PRESETS = {
    "DeepSeek（深度求索）": ("https://api.deepseek.com/v1", "deepseek-chat", "platform.deepseek.com"),
    "阿里云百炼（通义）": ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus", "bailian.console.aliyun.com"),
    "硅基流动 SiliconFlow": ("https://api.siliconflow.cn/v1", "Qwen/Qwen2.5-7B-Instruct", "cloud.siliconflow.cn"),
    "月之暗面 Kimi": ("https://api.moonshot.cn/v1", "moonshot-v1-8k", "platform.moonshot.cn"),
    "智谱 GLM": ("https://open.bigmodel.cn/api/paas/v4", "glm-4-flash", "open.bigmodel.cn"),
    "OpenAI": ("https://api.openai.com/v1", "gpt-4o-mini", "platform.openai.com"),
    "自定义（自己填）": ("", "", ""),
}

# 免费版可用的小模型 / Pro 解锁的更强模型
FREE_MODELS = [
    "qwen2.5:1.5b-instruct-q4_K_M",
    "qwen2.5:3b-instruct-q4_K_M",
]
PRO_MODELS = [
    "qwen2.5:7b-instruct-q4_K_M",
    "hunyuan-mt:7b",
    "seed-x:7b",
    "qwen2.5:14b-instruct-q4_K_M",
]
MODEL_CHOICES = PRO_MODELS + FREE_MODELS


class Wizard(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("本地双语翻译 · 首次使用")
        self.geometry("780x720")
        self.minsize(720, 620)
        self._center()
        self.attributes("-topmost", True)
        self.after(1200, lambda: self.attributes("-topmost", False))

        self.q: queue.Queue = queue.Queue()
        self.busy = False
        self.info = hardware.summarize()

        self.mode = tk.StringVar(value="local")
        self.model = tk.StringVar()
        self.api_name = tk.StringVar(value=list(API_PRESETS)[0])
        self.api_base = tk.StringVar()
        self.api_key = tk.StringVar()
        self.api_model = tk.StringVar()
        self.want_lnk = tk.BooleanVar(value=True)
        self.want_auto = tk.BooleanVar(value=False)

        self._build()
        self.after(120, self._drain)
        self.after(200, self._initial_probe)

    def _center(self) -> None:
        self.update_idletasks()
        w, h = 780, 720
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2 - 20)
        self.geometry(f"{w}x{h}+{x}+{y}")

    # ------------------------------------------------------------------ UI #
    def _build(self) -> None:
        pad = {"padx": 18}

        head = ttk.Frame(self)
        head.pack(fill="x", padx=18, pady=(16, 8))
        ttk.Label(head, text="本地双语翻译", font=FONT_TITLE).pack(anchor="w")
        ttk.Label(
            head,
            text="打中文的时候，屏幕上自动弹出一个中英对照的悬浮窗。先花一分钟配置好。",
            font=FONT_SMALL, foreground="#555",
        ).pack(anchor="w", pady=(2, 0))

        self._sep()

        # ① 本机情况 ------------------------------------------------------ #
        box = ttk.LabelFrame(self, text=" ① 这台电脑的情况 ", padding=10)
        box.pack(fill="x", **pad, pady=(0, 10))
        info = self.info
        gpu = info["gpu_name"]
        vram = f"{info['vram_mb'] / 1024:.0f} GB" if info["vram_mb"] else "—"
        text = (
            f"系统：{info['os']} · {info['bits']} 位\n"
            f"显卡：{gpu}（显存 {vram}）\n"
            f"内存：{info['ram_gb']} GB"
        )
        ttk.Label(box, text=text, font=FONT, justify="left").pack(anchor="w")
        rec, rec_size, why = hardware.recommend_model(info)
        self.recommended = rec
        ttk.Label(
            box, text=f"→ 建议使用：{rec}（{rec_size}）　{why}",
            font=FONT_SMALL, foreground="#0a6b3d",
        ).pack(anchor="w", pady=(6, 0))
        self.model.set(rec)

        # ② 引擎 ---------------------------------------------------------- #
        box = ttk.LabelFrame(self, text=" ② 翻译引擎（选一个） ", padding=10)
        box.pack(fill="x", **pad, pady=(0, 10))

        ttk.Radiobutton(
            box, text="本地 Ollama —— 文字不出这台电脑，断网也能用",
            variable=self.mode, value="local", command=self._sync_mode,
        ).grid(row=0, column=0, sticky="w")

        self.local_frame = ttk.Frame(box)
        self.local_frame.grid(row=1, column=0, sticky="ew", padx=(24, 0), pady=(4, 12))
        self.local_status = ttk.Label(self.local_frame, text="正在检测…", font=FONT)
        self.local_status.pack(anchor="w")

        row = ttk.Frame(self.local_frame)
        row.pack(anchor="w", pady=(6, 0))
        ttk.Label(row, text="模型：", font=FONT).pack(side="left")
        self.cmb_model = ttk.Combobox(
            row, textvariable=self.model, values=MODEL_CHOICES, width=34, font=FONT,
        )
        self.cmb_model.pack(side="left")
        self.btn_local = ttk.Button(
            row, text="一键装好（装 Ollama + 下模型）", command=self._do_local,
        )
        self.btn_local.pack(side="left", padx=(10, 0))
        ttk.Label(
            self.local_frame,
            text="首次要下载，稍慢；之后开机即用。模型越大译文越准，也越吃显卡。",
            font=FONT_SMALL, foreground="#777",
        ).pack(anchor="w", pady=(6, 0))

        ttk.Radiobutton(
            box, text="在线 API —— 填个 Key 立刻能用，但文字会发给服务商",
            variable=self.mode, value="api", command=self._sync_mode,
        ).grid(row=2, column=0, sticky="w", pady=(6, 0))

        self.api_frame = ttk.Frame(box)
        self.api_frame.grid(row=3, column=0, sticky="ew", padx=(24, 0), pady=(4, 0))
        grid = ttk.Frame(self.api_frame)
        grid.pack(anchor="w")

        ttk.Label(grid, text="服务商", font=FONT).grid(row=0, column=0, sticky="w", pady=2)
        self.cmb_api = ttk.Combobox(
            grid, textvariable=self.api_name, values=list(API_PRESETS),
            width=24, state="readonly", font=FONT,
        )
        self.cmb_api.grid(row=0, column=1, sticky="w", padx=(8, 0), pady=2)
        self.cmb_api.bind("<<ComboboxSelected>>", lambda _e: self._apply_preset())

        ttk.Label(grid, text="接口地址", font=FONT).grid(row=1, column=0, sticky="w", pady=2)
        ttk.Entry(grid, textvariable=self.api_base, width=46, font=FONT).grid(
            row=1, column=1, sticky="w", padx=(8, 0), pady=2)

        ttk.Label(grid, text="API Key", font=FONT).grid(row=2, column=0, sticky="w", pady=2)
        ttk.Entry(grid, textvariable=self.api_key, width=46, show="*", font=FONT).grid(
            row=2, column=1, sticky="w", padx=(8, 0), pady=2)

        ttk.Label(grid, text="模型名", font=FONT).grid(row=3, column=0, sticky="w", pady=2)
        ttk.Entry(grid, textvariable=self.api_model, width=46, font=FONT).grid(
            row=3, column=1, sticky="w", padx=(8, 0), pady=2)

        self.btn_api = ttk.Button(grid, text="测试连接", command=self._test_api)
        self.btn_api.grid(row=4, column=1, sticky="w", padx=(8, 0), pady=(6, 0))
        self._apply_preset()

        # ②-尾 翻译语言对 ------------------------------------------------ #
        self.lang_frame = ttk.Frame(box)
        self.lang_frame.grid(row=4, column=0, sticky="ew", padx=(24, 0), pady=(8, 0))
        ttk.Label(self.lang_frame, text="翻译语言对：", font=FONT).pack(side="left")
        self._lang_code_by_display = {d: c for c, d, _p in licmod.LANGUAGES}
        code_to_disp = {c: d for c, d, _p in licmod.LANGUAGES}
        self.src_var = tk.StringVar(value=code_to_disp.get("zh", "zh"))
        self.tgt_var = tk.StringVar(value=code_to_disp.get("en", "en"))
        self.cmb_src = ttk.Combobox(self.lang_frame, textvariable=self.src_var,
                                   width=16, state="disabled")
        self.cmb_src.pack(side="left", padx=(0, 4))
        ttk.Label(self.lang_frame, text="→", font=FONT).pack(side="left")
        self.cmb_tgt = ttk.Combobox(self.lang_frame, textvariable=self.tgt_var,
                                   width=16, state="disabled")
        self.cmb_tgt.pack(side="left", padx=(4, 0))
        self.lang_hint = ttk.Label(self.lang_frame, text="", font=FONT_SMALL,
                                  foreground="#8a5a00")
        self.lang_hint.pack(side="left", padx=(10, 0))
        self._refresh_langpair()

        # ③ 许可证 -------------------------------------------------------- #
        licbox = ttk.LabelFrame(self, text=" ③ 许可证（Pro） ", padding=10)
        licbox.pack(fill="x", **pad, pady=(0, 10))
        self.lic_state = ttk.Label(licbox, text="", font=FONT_SMALL,
                                   wraplength=690, justify="left")
        self.lic_state.pack(anchor="w")
        lrow = ttk.Frame(licbox)
        lrow.pack(fill="x", pady=(6, 0))
        self.lic_key = tk.StringVar()
        ttk.Entry(lrow, textvariable=self.lic_key, width=30, font=FONT).pack(side="left")
        ttk.Button(lrow, text="激活", command=self._do_activate).pack(side="left", padx=(8, 0))
        ttk.Label(lrow, text="（还没买就先跳过，免费版也能用）", font=FONT_SMALL,
                  foreground="#777").pack(side="left", padx=(8, 0))

        # ④ 附带 ---------------------------------------------------------- #
        box = ttk.LabelFrame(self, text=" ④ 顺手做掉 ", padding=10)
        box.pack(fill="x", **pad, pady=(0, 10))
        ttk.Checkbutton(box, text="在桌面创建快捷方式", variable=self.want_lnk).pack(anchor="w")
        self.chk_auto = ttk.Checkbutton(
            box, text="开机自动启动（登录后自己待命，不弹黑窗口）", variable=self.want_auto,
        )
        self.chk_auto.pack(anchor="w", pady=(4, 0))

        # 日志 + 按钮 ------------------------------------------------------ #
        logbox = ttk.LabelFrame(self, text=" 进度 ", padding=6)
        logbox.pack(fill="both", expand=True, padx=18, pady=(0, 10))
        self.log_text = tk.Text(logbox, height=7, font=FONT_SMALL, wrap="word",
                                background="#fafafa", relief="flat")
        self.log_text.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(logbox, command=self.log_text.yview)
        sb.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=sb.set, state="disabled")

        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=18, pady=(0, 16))
        self.btn_done = ttk.Button(bar, text="保存并启动", command=self._finish)
        self.btn_done.pack(side="right")
        ttk.Button(bar, text="只保存", command=lambda: self._finish(launch=False)).pack(
            side="right", padx=(0, 8))
        self.btn_close = ttk.Button(bar, text="退出", command=self.destroy)
        self.btn_close.pack(side="left")

        self._sync_mode()
        self._refresh_license()

    def _sep(self) -> None:
        ttk.Separator(self).pack(fill="x", padx=18, pady=(10, 10))

    # -------------------------------------------------------------- helper #
    def log(self, msg: str) -> None:
        self.q.put(("log", msg))

    def _drain(self) -> None:
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self.log_text.configure(state="normal")
                    self.log_text.insert("end", payload + "\n")
                    self.log_text.see("end")
                    self.log_text.configure(state="disabled")
                elif kind == "local_status":
                    self.local_status.configure(text=payload)
                elif kind == "busy":
                    self._set_busy(payload)
                elif kind == "msg":
                    messagebox.showinfo("提示", payload)
                elif kind == "msg_err":
                    messagebox.showerror("出错了", payload)
                elif kind == "license":
                    self._refresh_license()
        except queue.Empty:
            pass
        self.after(120, self._drain)

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        for w in (self.btn_local, self.btn_api, self.btn_done, self.btn_close):
            try:
                w.configure(state=state)
            except Exception:
                pass

    def _bg(self, fn) -> None:
        if self.busy:
            return
        self._set_busy(True)

        def runner() -> None:
            try:
                fn()
            except Exception as e:
                self.log(f"× 出错：{type(e).__name__}: {e}")
            finally:
                self.q.put(("busy", False))

        threading.Thread(target=runner, daemon=True).start()

    # ------------------------------------------------------------ actions #
    def _apply_preset(self) -> None:
        base, model, _ = API_PRESETS.get(self.api_name.get(), ("", "", ""))
        self.api_base.set(base)
        self.api_model.set(model)
        self.api_key.set("")

    def _sync_mode(self) -> None:
        local = self.mode.get() == "local"
        self._set_enabled(self.local_frame, local)
        self._set_enabled(self.api_frame, not local)

    # ------------------------------------------------------------- license #
    def _refresh_license(self) -> None:
        """按授权状态刷新：模型档位、开机自启开关、状态文案。"""
        pro = licmod.is_pro()
        if pro:
            st = licmod.load_state()
            who = st.get("email") or st.get("key", "")
            self.lic_state.configure(
                text=f"Pro 已激活：{who}　（无限翻译 · 7B+ 大模型 · 开机自启已解锁）",
                foreground="#0a6b3d")
        else:
            self.lic_state.configure(
                text=(f"免费版：每日 {licmod.FREE_DAILY_LIMIT} 次翻译，只能用 3B 及以下的小模型，"
                      f"不支持开机自启。\n已购买 Pro？把邮件里的许可证密钥粘到右边点「激活」。"
                      f"购买地址：{licmod.PRODUCT_URL}"),
                foreground="#8a5a00")

        try:
            self.cmb_model.configure(values=MODEL_CHOICES if pro else FREE_MODELS)
        except Exception:
            pass
        if not licmod.model_allowed(self.model.get()):
            self.model.set(self.recommended if licmod.model_allowed(self.recommended)
                           else FREE_MODELS[-1])
        try:
            if pro:
                self.chk_auto.configure(state="normal")
            else:
                self.want_auto.set(False)
                self.chk_auto.configure(state="disabled")
        except Exception:
            pass

        try:
            self._refresh_langpair()
        except Exception:
            pass

    def _refresh_langpair(self) -> None:
        """按档位刷新语言对下拉框：Free/Pro 锁中文→英文，Max 全开。"""
        max_tier = licmod.is_max()
        code_to_disp = {c: d for c, d, _p in licmod.LANGUAGES}
        disp_list = [d for _c, d, _p in licmod.LANGUAGES]
        if max_tier:
            self.cmb_src.configure(values=disp_list, state="readonly")
            self.cmb_tgt.configure(values=disp_list, state="readonly")
            self.lang_hint.configure(
                text="Max：可任意组合语言对（日→英、中→日、英→中等）")
        else:
            self.cmb_src.configure(values=[code_to_disp["zh"]], state="disabled")
            self.cmb_tgt.configure(values=[code_to_disp["en"]], state="disabled")
            self.src_var.set(code_to_disp["zh"])
            self.tgt_var.set(code_to_disp["en"])
            self.lang_hint.configure(
                text="免费/Pro 固定为 中文→英文；Max 解锁任意语言互译")

    def _do_activate(self) -> None:
        key = self.lic_key.get().strip()
        if not key:
            messagebox.showinfo("提示", "请先粘贴许可证密钥。")
            return
        self.lic_state.configure(text="正在向 Gumroad 验证，请稍候…", foreground="#555")

        def work() -> None:
            ok, msg = licmod.activate(key)
            self.log(("✓ " if ok else "× ") + msg)
            self.q.put(("license", None))

        threading.Thread(target=work, daemon=True).start()
        if local:
            self.cmb_model.configure(state="normal")
        else:
            self.cmb_api.configure(state="readonly")

    def _set_enabled(self, frame: tk.Misc, on: bool) -> None:
        state = "normal" if on else "disabled"
        for c in frame.winfo_children():
            try:
                c.configure(state=state)  # type: ignore[call-arg]
            except Exception:
                pass
            self._set_enabled(c, on)

    # ---------------------------------------------------------- 初次探测 #
    def _initial_probe(self) -> None:
        self.log("开始检查本机环境…")
        self.log(f"系统 {self.info['os']}，显卡 {self.info['gpu_name']}，内存 {self.info['ram_gb']} GB")
        if not hardware.is_windows():
            self.log("⚠ 本程序依赖 Windows 的窗口接口，当前系统不是 Windows，无法运行。")
            self.q.put(("msg_err", "这个程序只能在 Windows 上运行。"))
            return

        def work() -> None:
            installed = ollama_setup.is_installed()
            alive = ollama_setup.server_alive()
            if alive:
                models = ollama_setup.local_models()
                msg = f"✓ Ollama 正在运行，本机有 {len(models)} 个模型"
                self.q.put(("local_status", msg))
                self.log(msg + (f"：{', '.join(models[:5])}" if models else ""))
                if models:
                    self.log("→ 如果已有合适的模型，直接点右下角「保存并启动」就行。")
            elif installed:
                msg = "✓ 已安装 Ollama（服务没在运行，保存后启动程序时会自动拉起）"
                self.q.put(("local_status", msg))
                self.log(msg)
            else:
                msg = "✗ 这台电脑还没装 Ollama"
                self.q.put(("local_status", msg))
                self.log(msg + "，点上面的「一键装好」会自动下载安装并下好模型。")

        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------ 本地路 #
    def _do_local(self) -> None:
        model = (self.model.get() or "").strip()
        if not model:
            messagebox.showwarning("提示", "先选一个模型。")
            return

        def work() -> None:
            # 1) 装 Ollama
            if not ollama_setup.is_installed():
                exe = ollama_setup.download_installer(self.log)
                ok, why = ollama_setup.install_silent(exe, self.log)
                if not ok:
                    self.q.put(("msg_err", f"Ollama 安装失败：{why}\n可以手动到 ollama.com 下载安装后再回来。"))
                    return
            else:
                self.log("已经装过 Ollama，跳过安装。")

            # 2) 起服务
            if not ollama_setup.server_alive():
                self.log("正在启动 Ollama 服务…")
                ok, why = ollama_setup.start_server()
                if not ok or not ollama_setup.wait_server(90):
                    self.q.put(("msg_err", f"Ollama 服务起不来：{why}"))
                    return
            self.log("✓ Ollama 服务已就绪")

            # 3) 拉模型
            puller = ollama_setup.Puller()
            ok, why = puller.pull(model, self.log)
            if not ok:
                self.q.put(("msg_err", f"模型下载失败：{why}"))
                return
            self.q.put(("local_status", f"✓ 本地引擎已就绪，模型 {model}"))
            self.q.put(("msg", "本地引擎装好了。点右下角「保存并启动」开始使用。"))

        self.log(f"目标模型：{model}")
        self._bg(work)

    # ------------------------------------------------------------ 在线路 #
    def _test_api(self) -> None:
        def work() -> None:
            import engines

            base = self.api_base.get().strip()
            key = self.api_key.get().strip()
            model = self.api_model.get().strip()
            if not base:
                self.q.put(("msg_err", "先填接口地址。"))
                return
            ecfg = {
                "type": "openai", "base_url": base, "api_key": key or "none",
                "model": model, "timeout": 20,
            }
            self.log(f"正在测试 {base} …")
            eng = engines.build_engine(ecfg)
            ok, why = eng.health()
            if not ok:
                self.q.put(("msg_err", f"连不上：{why}"))
                return
            models = []
            try:
                models = eng.list_models()
            except Exception:
                pass
            self.log(f"✓ 连接成功，服务返回 {len(models)} 个模型")
            if models:
                self.log("可用模型（前 8 个）：" + ", ".join(models[:8]))
                if model not in models:
                    self.log(f"⚠ 你填的「{model}」不在列表里，确认一下名字是否正确。")
            self.q.put(("msg", "连接正常。"))

        self._bg(work)

    # --------------------------------------------------------------- 完成 #
    def _finish(self, launch: bool = True) -> None:
        mode = self.mode.get()
        cfg = cfgmod.load()
        eng = cfg.setdefault("engine", {})

        if mode == "local":
            model = (self.model.get() or "").strip()
            if not model:
                messagebox.showwarning("提示", "先选一个模型。")
                return
            if not licmod.model_allowed(model):
                messagebox.showwarning(
                    "这个模型需要 Pro",
                    licmod.model_message(model) + "\n\n"
                    "免费版请换成 3B 及以下的小模型，或先在上面的「许可证」里激活 Pro。")
                return
            eng.update({
                "type": "openai",
                "base_url": "http://127.0.0.1:11434/v1",
                "api_key": "local",
                "model": model,
                "timeout": 120,
            })
            self.log(f"引擎 = 本地 Ollama · {model}")
        else:
            base = self.api_base.get().strip()
            key = self.api_key.get().strip()
            model = self.api_model.get().strip()
            if not (base and key and model):
                messagebox.showwarning("提示", "接口地址、API Key、模型名都要填。")
                return
            eng.update({
                "type": "openai", "base_url": base, "api_key": key,
                "model": model, "timeout": 120,
            })
            self.log(f"引擎 = 在线 API · {base} · {model}")

        # 语言对：Free/Pro 强制中文→英文；Max 用界面所选
        if licmod.is_max():
            src = self._lang_code_by_display.get(self.src_var.get(), "zh")
            tgt = self._lang_code_by_display.get(self.tgt_var.get(), "en")
        else:
            src, tgt = "zh", "en"
        eng["source_lang"] = src
        eng["target_lang"] = tgt
        self.log(f"语言对 = {src} → {tgt}")

        try:
            cfgmod.save(cfg)
            self.log(f"已写入配置：{cfgmod.CONFIG_PATH}")
        except Exception as e:
            messagebox.showerror("出错了", f"配置写入失败：{e}")
            return

        if self.want_lnk.get():
            try:
                for p in portable_setup.make_shortcuts():
                    self.log(f"桌面快捷方式：{p.name}")
            except Exception as e:
                self.log(f"× 快捷方式创建失败：{e}")

        if self.want_auto.get() and not licmod.is_pro():
            self.log("开机自启属于 Pro 功能，已跳过（免费版不支持）")
            try:
                cfg.setdefault("app", {})["autostart"] = False
                cfgmod.save(cfg)
            except Exception:
                pass
        else:
            try:
                ok, msg = portable_setup.autostart(self.want_auto.get())
                self.log(("开机自启：" if ok else "开机自启设置失败：") + str(msg))
            except Exception as e:
                self.log(f"× 开机自启设置失败：{e}")

        self.log("配置完成 ✓")
        if launch:
            self._launch()

    def _launch(self) -> None:
        py = Path(sys.executable)
        pythonw = py.with_name("pythonw.exe")
        exe = pythonw if pythonw.is_file() else py
        try:
            subprocess.Popen(
                [str(exe), str(APP_DIR / "main.py")],
                cwd=str(APP_DIR),
                creationflags=0x00000008 | 0x00000200,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL, close_fds=True,
            )
            self.log("已启动。看屏幕右下角出现「译 · 待机」小圆球就成了。")
            messagebox.showinfo(
                "搞定", "已经启动。\n\n看屏幕右下角的小圆球，"
                        "然后随便找个输入框打几个中文字试试。",
            )
            self.destroy()
        except Exception as e:
            messagebox.showerror("出错了", f"启动失败：{e}")


def main() -> int:
    if not hardware.is_windows():
        print("This program only runs on Windows.")
        return 1
    app = Wizard()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
