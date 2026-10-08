"""本地双语翻译 —— 打中文时在悬浮窗里实时显示英文对照。

全程只与本机端口通信，不向任何外部服务器发送数据。
"""
from __future__ import annotations

import argparse
import ctypes
import logging
import queue
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import autostart as autostart_mod
import config as cfgmod
import context_reader as ctxmod
import engines as engmod
import license as licmod
from overlay import FloatingUI
from settings_ui import SettingsWindow
from watcher import TypingWatcher

APP_NAME = "LocalBilingualTranslator"


# --------------------------------------------------------------------------- #
def _norm_lang(code: str) -> str:
    """把配置里的语言值规整成 license.LANGUAGES 里的代码（zh/en/ja…/auto）。

    兼容旧配置里写「English」「Chinese」等英文名的情形；未知值原样返回。
    """
    code = (code or "").strip().lower()
    if not code:
        return ""
    codes = {c for c, _d, _p in licmod.LANGUAGES}
    if code in codes:
        return code
    for c, disp, prompt in licmod.LANGUAGES:
        if code == prompt.lower() or code == disp.lower():
            return c
    return code


# --------------------------------------------------------------------------- #
def setup_logging(level: str) -> logging.Logger:
    cfgmod.LOG_DIR.mkdir(exist_ok=True)
    log = logging.getLogger("lbt")
    log.setLevel(getattr(logging, level.upper(), logging.INFO))
    if not log.handlers:
        fh = logging.FileHandler(cfgmod.LOG_DIR / "app.log", encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(fh)
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        log.addHandler(sh)
    return log


def single_instance() -> bool:
    """用命名互斥体保证只跑一个实例。"""
    try:
        k32 = ctypes.windll.kernel32
        k32.CreateMutexW(None, False, APP_NAME)
        return k32.GetLastError() != 183  # ERROR_ALREADY_EXISTS
    except Exception:
        return True


def is_running() -> bool:
    """只查询另一个进程是否已经持有互斥体（不创建）。"""
    try:
        k32 = ctypes.windll.kernel32
        SYNCHRONIZE = 0x00100000
        h = k32.OpenMutexW(SYNCHRONIZE, False, APP_NAME)
        if h:
            k32.CloseHandle(h)
            return True
    except Exception:
        pass
    return False


# --------------------------------------------------------------------------- #
class App:
    def __init__(self, cfg: dict, log: logging.Logger) -> None:
        self.cfg = cfg
        self.log = log
        self.engine = engmod.build_engine(cfg.get("engine", {}))
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="translate")
        self.last_text = ""
        self.last_fire = 0.0
        self._busy = False
        self.engine_ready = False
        self._last_ready_check = 0.0
        self._lock = threading.Lock()

        self.ui = FloatingUI(
            cfg,
            on_settings=self.open_settings,
            on_quit=self.quit,
            on_toggle_pause=self.toggle_pause,
            on_probe=self.probe_and_report,
            on_autostart=self.set_autostart,
        )
        self.settings = SettingsWindow(
            self.ui.root,
            cfg,
            on_saved=self.reload,
            on_test=self.test_sample,
            font_family=cfg.get("overlay", {}).get("font_family", "Microsoft YaHei UI"),
        )
        self.watcher = TypingWatcher(
            cfg,
            on_pause=self.on_typing_paused,
            on_hotkey=self.on_hotkey,
            on_typing=self.on_typing,
            on_state=lambda s: self.ui.post(("status", s)),
        )
        self._ui_q = self.ui.out_q

    # ------------------------------------------------------------------ #
    def start(self) -> None:
        """先把界面挂起来，就绪流程放后台，避免开机时白等。"""
        self.ui.sync_autostart(autostart_mod.is_enabled())
        self.ui.post(("status", "启动中"))
        self.watcher.start()
        threading.Thread(target=self._ui_worker, daemon=True).start()
        threading.Thread(target=self._boot, daemon=True).start()
        self.ui.run()

    def _boot(self) -> None:
        """后台完成：引擎就绪 → 模型适配 → 自检 → 预热。"""
        threading.Thread(target=self._license_check, daemon=True).start()
        self._ensure_engine()
        self._autofit_model()
        self._sync_autostart(silent=True)

        eng = self.cfg.get("engine", {})
        base = eng.get("base_url")
        ok, msg = self.engine.health()
        self.engine_ready = bool(ok)
        self.log.info("引擎 %s: %s | %s", self.engine.label, "OK" if ok else "不可用", msg)
        self.log.info("当前配置：%s @ %s", eng.get("model"), base)
        if ok:
            self.ui.post(("status", "待机"))
        else:
            self.log.warning("引擎未就绪：%s", msg)
            self.ui.post(("status", "引擎未就绪"))
            hint = (
                "右键屏幕上的状态球 → 启动本机引擎，或从开始菜单打开 Ollama。"
                if engmod.find_ollama_exe()
                else "本机还没装本地推理服务，装一个 Ollama 或 LM Studio 即可。"
            )
            self.ui.post(
                ("error", "没有连接到本机翻译服务", f"{base} 无响应。{hint}", None)
            )
            return
        self._warn_model_tier()
        self._warmup()

    def _license_check(self) -> None:
        """后台复查许可证（放在线程里，别卡住界面）。"""
        try:
            if licmod.needs_recheck():
                ok, msg = licmod.refresh()
                self.log.info("许可证复查：%s", msg)
            self.log.info("授权状态：%s", licmod.describe())
        except Exception as e:
            self.log.debug("许可证复查失败：%s", e)

    def _warn_model_tier(self) -> None:
        """免费版配了 7B+ 大模型时提前说明，别让用户以为是坏了。"""
        model = str(self.cfg.get("engine", {}).get("model", "") or "")
        if licmod.model_allowed(model):
            return
        self.log.info("当前模型 %s 高于免费版档位", model)
        self.ui.post((
            "error", "当前模型属于 Pro",
            licmod.model_message(model) + "\n\n可在「设置 → 翻译引擎 → 模型名称」换成 qwen2.5:3b。",
            None,
        ))

    def _ensure_engine(self) -> None:
        """本机引擎没在跑就顺手拉起来，省得用户先手动开 Ollama。

        开机时 Ollama 自己也在自启，先给它几秒，避免两边同时拉起。
        """
        ok, _ = self.engine.health()
        if ok or self.engine.label == "mock":
            return
        for _ in range(6):
            time.sleep(1.0)
            ok, _ = self.engine.health()
            if ok:
                self.log.info("引擎已在运行（应该是随系统自启起来的）")
                return
        started, info = engmod.start_ollama()
        if started:
            self.log.info("检测到引擎未运行，已拉起：%s", info)
            self.ui.post(("status", "启动引擎中"))
            if engmod.wait_for_health(self.engine, 45):
                self.log.info("引擎已就绪")
        else:
            self.log.debug("未自动启动引擎：%s", info)

    def _warmup(self) -> None:
        """提前把模型读进显存，让第一次真实翻译也是秒出。"""
        if self.engine.label == "mock":
            return
        self.ui.post(("status", "预热中"))
        ok, info, ms = self.engine.warmup()
        if ok:
            self.log.info("模型已预热（%d ms）", ms)
            self.ui.post(("status", "待机"))
        else:
            self.log.warning("预热失败：%s", info)
            self.ui.post(("status", "引擎未就绪"))

    def _autofit_model(self) -> None:
        """配置里的模型不存在时，自动改用本机已有的模型。"""
        changed = engmod.autofit_model(self.cfg, self.engine)
        if changed:
            self.log.info("模型「%s」不可用，自动改用「%s」",
                          self.cfg["engine"].get("model"), changed)
            cfgmod.save(self.cfg)
            self.engine = engmod.build_engine(self.cfg["engine"])

    # ------------------------------------------------------------------ #
    # 开机自启
    # ------------------------------------------------------------------ #
    def _sync_autostart(self, silent: bool = False) -> None:
        """让注册表里的自启项与配置保持一致。"""
        want = bool(self.cfg.get("app", {}).get("autostart", False))
        if want and not licmod.is_pro():
            # 开机自启是 Pro 功能：免费版顺手关掉，别留半开状态
            self.log.info("开机自启属于 Pro 功能，已自动关闭")
            want = False
            self.cfg.setdefault("app", {})["autostart"] = False
            cfgmod.save(self.cfg)
        has = autostart_mod.is_enabled()
        if want and not has:
            ok, info = autostart_mod.set_autostart(want, int(
                self.cfg.get("app", {}).get("autostart_delay_s", 0)))
            self._autostart_result(ok, info, want, silent)
        elif not want and has:
            ok, info = autostart_mod.set_autostart(False)
            self._autostart_result(ok, info, want, silent)
        elif silent:
            self.log.info("开机自启：%s", autostart_mod.describe())
        self.ui.sync_autostart(autostart_mod.is_enabled())

    def _autostart_result(self, ok: bool, info: str, want: bool, silent: bool) -> None:
        if ok:
            self.log.info("开机自启已%s：%s", "开启" if want else "关闭", info)
            if not silent:
                self.ui.post(("status", "已设自启" if want else "已取消自启"))
        else:
            self.log.warning("设置开机自启失败：%s", info)
            if not silent:
                self.ui.post(("error", "设置开机自启失败", info, None))

    def set_autostart(self, on: bool) -> bool:
        """由状态球菜单调用，立即生效并写回配置。"""
        if on and not licmod.is_pro():
            self.log.info("开机自启属于 Pro 功能，已拦截")
            self.ui.post((
                "error", "开机自启是 Pro 功能",
                "免费版不支持开机自启。\n升级 Pro 后就能开机自动待命：\n"
                + licmod.PRODUCT_URL,
                None,
            ))
            self.ui.sync_autostart(autostart_mod.is_enabled())
            return False
        delay = int(self.cfg.get("app", {}).get("autostart_delay_s", 0))
        ok, info = autostart_mod.set_autostart(on, delay)
        self.cfg.setdefault("app", {})["autostart"] = bool(on if ok else not on)
        cfgmod.save(self.cfg)
        self._autostart_result(ok, info, on, silent=False)
        self.ui.sync_autostart(autostart_mod.is_enabled())
        self.log.info("自启项现在：%s", autostart_mod.describe())
        return ok and on

    # ------------------------------------------------------------------ #
    def _ui_worker(self) -> None:
        """处理悬浮窗投递过来的请求。"""
        while True:
            try:
                msg = self._ui_q.get()
            except Exception:
                return
            try:
                if msg[0] == "clipboard_translate":
                    text = ctxmod.get_clipboard()
                    if text.strip():
                        self._dispatch(text.strip(), None, prefix="剪贴板")
                elif msg[0] == "start_engine":
                    self.start_engine()
                elif msg[0] == "quit":
                    return
            except Exception as e:
                self.log.error("ui worker: %s", e)

    def start_engine(self) -> None:
        """手动拉起本机引擎并刷新状态。"""
        self.ui.post(("status", "启动引擎中"))
        ok, info = engmod.start_ollama()
        if not ok:
            self.log.warning("启动引擎失败：%s", info)
            self.ui.show_translation(
                src="启动本机引擎失败",
                dst=f"{info}。请手动从开始菜单启动 Ollama，或改用其他本地服务。",
                engine="engine", caret=None,
            )
            self.ui.post(("status", "引擎未就绪"))
            return
        if engmod.wait_for_health(self.engine, 45):
            self.log.info("引擎已就绪")
            self.engine_ready = True
            self._autofit_model()
            threading.Thread(target=self._warmup, daemon=True).start()
        else:
            self.log.warning("引擎启动超时")
            self.engine_ready = False
            self.ui.show_translation(
                src="引擎启动超时", dst="请手动打开 Ollama 后再试。", engine="engine", caret=None
            )
            self.ui.post(("status", "引擎未就绪"))

    # ------------------------------------------------------------------ #
    # 触发
    # ------------------------------------------------------------------ #
    def on_typing(self) -> None:
        """刚按下第一个字符：立刻把窗口顶出来，别让用户觉得"没反应"。"""
        ov = self.cfg.get("overlay", {})
        if ov.get("hide_on_typing", False):
            self.ui.post(("hide",))
            return
        if not self.cfg.get("trigger", {}).get("instant_popup", True):
            return
        self.ui.post(("popup", None))

    def on_typing_paused(self) -> None:
        trig = self.cfg.get("trigger", {})
        if not trig.get("auto_translate", True):
            return
        now = time.monotonic()
        if (now - self.last_fire) * 1000 < int(trig.get("cooldown_ms", 300)):
            return

        ctx = ctxmod.read_focused_context(self.cfg.get("context", {}).get("reader", "auto"))
        if not ctx.ok:
            self.log.debug("读取失败：%s", ctx.error)
            return

        # 焦点不在输入框上（比如在刷网页时按了键），读到的是整页 UI 文字，
        # 里面混着按钮、计数、别人的评论——这种不该翻译。
        if ctxmod.looks_like_ui_noise(ctx.text):
            self.log.info("忽略界面噪声文本（焦点不在输入框）：%s", ctx.control)
            return

        conf = self.cfg.get("context", {})
        text = ctxmod.extract_unit(
            ctx.text,
            mode=conf.get("take", "last_sentence"),
            tail_chars=int(conf.get("tail_chars", 200)),
            min_chars=int(trig.get("min_chars", 2)),
        )
        if not self._valid(text):
            return
        self.last_fire = now
        self.log.debug("触发翻译：%r（来源 %s）", text[:40], ctx.control)
        self._dispatch(text, ctx.caret)

    def on_hotkey(self) -> None:
        mode = self.cfg.get("trigger", {}).get("hotkey_mode", "selection")
        self.log.info("手动快捷键触发（%s）", mode)
        if mode == "clipboard":
            text = ctxmod.get_clipboard().strip()
            caret = None
        else:
            text, caret = ctxmod.read_selection()
        if not text:
            self.ui.post(("error", "没有取到文本", "选中一段文字再按快捷键，或在设置里改成翻译剪贴板", None))
            return
        text = ctxmod.extract_unit(
            text, mode=self.cfg.get("context", {}).get("take", "last_sentence")
        )
        self._dispatch(text, caret, prefix="手动")

    def _valid(self, text: str) -> bool:
        trig = self.cfg.get("trigger", {})
        if len(text) < int(trig.get("min_chars", 2)):
            return False
        if len(text) > int(trig.get("max_chars", 600)):
            text = text[-int(trig.get("max_chars", 600)) :]
        # 只有「源语言是中文」时才要求原文含中日韩字符；翻英文/日文等不需要
        src = self.cfg.get("engine", {}).get("source_lang", "zh")
        if trig.get("require_cjk", True) and src == "zh" and not ctxmod.has_cjk(text):
            return False
        return True

    def _dispatch(self, text: str, caret, prefix: str = "") -> None:
        trig = self.cfg.get("trigger", {})
        if len(text) < int(trig.get("min_chars", 2)):
            return
        maxc = int(trig.get("max_chars", 600))
        if len(text) > maxc:
            text = text[-maxc:]
        src = self.cfg.get("engine", {}).get("source_lang", "zh")
        if trig.get("require_cjk", True) and src == "zh" and not ctxmod.has_cjk(text):
            return

        # 引擎没起来就直接说清楚，别让请求挂在超时上
        if not self.engine_ready:
            self.log.warning("引擎未就绪，跳过翻译：%r", text[:20])
            now = time.monotonic()
            if now - self._last_ready_check > 5.0:
                self._last_ready_check = now
                threading.Thread(target=self._recheck_engine, daemon=True).start()
            self.ui.post((
                "error", text,
                "本机翻译服务没在运行。右键屏幕上的状态球 → 启动本机引擎。",
                caret,
            ))
            self.ui.post(("status", "引擎未就绪"))
            return

        with self._lock:
            if text == self.last_text and not prefix:
                return
            self.last_text = text
            if self._busy:
                self.log.debug("上一轮还没结束，跳过")
                return
            self._busy = True

        # ① 语言对权限：Free / Pro 仅允许「中文 → English」，任意互译是 Max 功能
        eng_cfg = self.cfg.get("engine", {})
        slang = _norm_lang(eng_cfg.get("source_lang", "zh"))
        tlang = _norm_lang(eng_cfg.get("target_lang", "en"))
        if not licmod.pair_allowed(slang, tlang):
            with self._lock:
                self._busy = False
            self.log.info("语言对 %s→%s 属于 Max 功能，已拦截", slang, tlang)
            self.ui.post(("error", text, licmod.pair_message(slang, tlang), caret))
            self.ui.post(("status", "需要 Max"))
            return

        # ② 免费版只能用 3B 及以下的小模型，7B+ 大模型是 Pro 功能
        model = str(self.cfg.get("engine", {}).get("model", "") or "")
        if not licmod.model_allowed(model):
            with self._lock:
                self._busy = False
            self.log.info("模型 %s 属于 Pro 功能，已拦截", model)
            self.ui.post(("error", text, licmod.model_message(model), caret))
            self.ui.post(("status", "需要 Pro"))
            return

        # ② 免费版每日额度（Pro 不限）
        allowed, used = licmod.consume()
        if not allowed:
            with self._lock:
                self._busy = False
            self.log.info("免费额度已用完（%d/%d）", used, licmod.FREE_DAILY_LIMIT)
            self.ui.post(("error", text, licmod.quota_message(), caret))
            self.ui.post(("status", "额度已用完"))
            return

        self.ui.post(("pending", text, caret))
        self.ui.post(("status", "翻译中"))
        self.pool.submit(self._translate_job, text, caret)

    def _recheck_engine(self) -> None:
        """引擎后来被手动拉起来时，自动恢复可用状态。"""
        ok, msg = self.engine.health()
        if ok:
            self.engine_ready = True
            self.log.info("引擎已恢复：%s", msg)
            self._autofit_model()
            self.ui.post(("status", "待机"))

    def _translate_job(self, text: str, caret) -> None:
        eng = self.cfg.get("engine", {})
        tlang = _norm_lang(eng.get("target_lang", "en"))
        slang = _norm_lang(eng.get("source_lang", "zh"))
        style = eng.get("style", "natural")
        use_stream = bool(self.cfg.get("overlay", {}).get("streaming", True))

        t0 = time.perf_counter()
        got = ""
        first_ms = 0
        if use_stream:
            try:
                for _piece, acc in self.engine.run_stream(text, tlang, style, slang):
                    if not got:
                        first_ms = int((time.perf_counter() - t0) * 1000)
                        self.log.debug("首字 %.0f ms", first_ms)
                    got = acc
                    self.ui.post(("delta", text, acc, caret))
            except BaseException as e:
                self.log.debug("流式翻译不可用，退回普通请求：%s", e)
        with self._lock:
            self._busy = False

        if got:
            ms = int((time.perf_counter() - t0) * 1000)
            self.log.info(
                "[%s] %s -> %s（共 %dms，首字 %dms）",
                self.engine.label, text[:30], got[:40], ms, first_ms,
            )
            self.ui.post(
                ("show", (text,), {"dst": got, "engine": self.engine.label,
                                   "elapsed_ms": ms, "caret": caret, "first_ms": first_ms})
            )
            self.ui.post(("status", "待机"))
            return

        # 流式一个字符都没拿到 → 走普通请求，好给出明确的错误
        try:
            res = self.engine.run(text, tlang, style, slang)
        except BaseException as e:
            res = engmod.TranslationResult(error=f"{type(e).__name__}: {e}",
                                           engine=self.engine.label)

        if res.ok:
            self.log.info("[%s] %s -> %s (%dms)", res.engine, text[:30],
                          res.dst[:40], res.elapsed_ms)
            self.ui.post(
                ("show", (text,), {"dst": res.dst, "engine": res.engine,
                                   "elapsed_ms": res.elapsed_ms, "caret": caret})
            )
            self.ui.post(("status", "待机"))
        else:
            self.log.error("翻译失败：%s", res.error)
            self.ui.post(("error", text, res.error, caret))
            self.ui.post(("status", "出错"))

    # ------------------------------------------------------------------ #
    def test_sample(self, text: str) -> None:
        self._dispatch(text, None, prefix="测试")

    # ------------------------------------------------------------------ #
    def open_settings(self) -> None:
        self.settings.open()

    def reload(self, new_cfg: dict) -> None:
        self.cfg.clear()
        self.cfg.update(new_cfg)
        self.engine = engmod.build_engine(self.cfg.get("engine", {}))
        self.watcher.reload_hotkey()
        self.ui.apply_live(new_cfg)
        ok, msg = self.engine.health()
        self.engine_ready = bool(ok)
        self.log.info("重载配置，引擎 %s", "OK" if ok else f"不可用({msg})")
        self.ui.post(("status", "待机" if ok else "引擎未就绪"))
        self._sync_autostart(silent=False)

    def probe_and_report(self) -> None:
        def work() -> None:
            found = engmod.probe_local_endpoints()
            if found:
                lines = "\n".join(
                    f"{f['name']}  {f['host']}  {', '.join(f['models'][:3])}" for f in found
                )
                self.ui.show_translation(
                    src="检测到本机翻译服务：", dst=lines, engine="probe", caret=None
                )
            else:
                self.ui.show_translation(
                    src="未发现本机翻译服务",
                    dst="启动 Ollama / LM Studio / llama.cpp 其中之一，再试一次。"
                        "也可右键状态球 → 打开设置 → 检测本机服务。",
                    engine="probe", caret=None,
                )

        threading.Thread(target=work, daemon=True).start()

    def toggle_pause(self) -> bool:
        p = not self.watcher.paused
        self.watcher.set_paused(p)
        self.log.info("自动翻译%s", "已暂停" if p else "已恢复")
        return p

    def quit(self) -> None:
        self.log.info("退出")
        try:
            self.watcher.stop()
        except Exception:
            pass
        self.ui.post(("quit",))


# --------------------------------------------------------------------------- #
def run_cli(args) -> int:
    cfg = cfgmod.load()
    if args.engine:
        cfg["engine"]["type"] = args.engine

    if getattr(args, "activate", None):
        ok, msg = licmod.activate(args.activate)
        print(f"ACTIVATED={'OK' if ok else 'FAILED'} {msg}")
        return 0 if ok else 1

    if getattr(args, "deactivate", False):
        licmod.deactivate()
        print("DEACTIVATED")
        return 0

    if getattr(args, "license_status", False):
        st = licmod.load_state()
        print(f"PLAN={'PRO' if licmod.is_pro() else 'FREE'}")
        if st.get("key"):
            print(f"KEY={st.get('key')}")
            print(f"EMAIL={st.get('email', '')}")
        print(f"TODAY_USED={licmod.usage_today()}/{licmod.FREE_DAILY_LIMIT}")
        if st.get("invalid"):
            print(f"NOTE={st.get('last_error', 'license invalid')}")
        return 0

    if args.set_autostart:
        if args.set_autostart == "on" and not licmod.is_pro():
            print("AUTOSTART=FAILED 开机自启属于 Pro 功能")
            return 1
        on = args.set_autostart == "on"
        delay = int(cfg.get("app", {}).get("autostart_delay_s", 0))
        ok, info = autostart_mod.set_autostart(on, delay)
        cfg.setdefault("app", {})["autostart"] = bool(on if ok else not on)
        cfgmod.save(cfg)
        # 用 ASCII 键值输出，避免在管道/重定向下中文编码错乱
        print(f"AUTOSTART={'ON' if on else 'OFF'}" if ok else f"AUTOSTART=FAILED {info}")
        if ok and on:
            print(f"COMMAND={info}")
        return 0 if ok else 1

    if args.autostart_status:
        print(f"AUTOSTART={'ON' if autostart_mod.is_enabled() else 'OFF'}")
        cmd = autostart_mod.current_command()
        if cmd:
            print(f"COMMAND={cmd}")
        return 0

    if args.running:
        # 供 启动.bat 判断，避免重复启动、也让用户知道到底起没起
        running = is_running()
        print("RUNNING" if running else "STOPPED")
        return 0 if running else 1

    log = setup_logging(cfg.get("app", {}).get("log_level", "INFO"))

    if args.probe:
        found = engmod.probe_local_endpoints()
        if not found:
            print("未在本机常见端口发现翻译服务。")
            print("可尝试：Ollama(:11434) / LM Studio(:1234) / llama.cpp(:8080) / vLLM(:8000)")
            return 1
        for f in found:
            print(f"● {f['name']:<18} {f['host']:<28} 模型: {', '.join(f['models'][:6])}")
        return 0

    if args.test:
        eng = engmod.build_engine(cfg["engine"])
        changed = engmod.autofit_model(cfg, eng)
        if changed:
            cfgmod.save(cfg)
            eng = engmod.build_engine(cfg["engine"])
        ok, msg = eng.health()
        print(f"引擎: {eng.label} | {cfg['engine'].get('model')} @ {cfg['engine'].get('base_url')}")
        print(f"健康检查: {'OK' if ok else '不可用'}  {msg}")
        t0 = time.perf_counter()
        res = eng.run(args.test,
                      _norm_lang(cfg["engine"].get("target_lang", "en")),
                      cfg["engine"].get("style", "natural"),
                      _norm_lang(cfg["engine"].get("source_lang", "zh")))
        print(f"耗时: {(time.perf_counter()-t0)*1000:.0f} ms")
        if res.ok:
            print(f"原文: {args.test}")
            print(f"译文: {res.dst}")
            return 0
        print("失败:", res.error)
        return 2

    return -1


def main() -> int:
    ap = argparse.ArgumentParser(description="本地双语翻译（悬浮窗）")
    ap.add_argument("--probe", action="store_true", help="检测本机翻译服务")
    ap.add_argument("--test", metavar="TEXT", help="翻译一段文字后退出")
    ap.add_argument("--engine", choices=["openai", "ollama", "mock"], help="临时覆盖引擎")
    ap.add_argument("--set-autostart", choices=["on", "off"], help="开启/关闭开机自启")
    ap.add_argument("--activate", metavar="KEY", help="用 Gumroad 许可密钥激活 Pro")
    ap.add_argument("--deactivate", action="store_true", help="解除本机的 Pro 绑定")
    ap.add_argument("--license-status", action="store_true", help="查看许可证状态与今日用量")
    ap.add_argument("--autostart-status", action="store_true", help="查看开机自启状态")
    ap.add_argument("--running", action="store_true", help="查询是否已有实例在运行")
    ap.add_argument("--autostart", action="store_true", help="开机自启模式（等桌面就绪后静默启动）")
    ap.add_argument("--delay", type=int, default=-1, help="额外等待秒数（配合 --autostart）")
    args = ap.parse_args()

    rc = run_cli(args)
    if rc >= 0:
        return rc

    if not single_instance():
        print("已经有一个实例在运行了（看屏幕上的状态球）。")
        return 0

    cfg = cfgmod.load()
    log = setup_logging(cfg.get("app", {}).get("log_level", "INFO"))

    if args.autostart:
        delay = args.delay if args.delay >= 0 else int(
            cfg.get("app", {}).get("autostart_delay_s", 0))
        log.info("开机自启模式：等待桌面就绪…")
        ready = autostart_mod.wait_for_shell(extra_s=float(max(0, delay)))
        log.info("桌面%s，开始启动", "已就绪" if ready else "等待超时")

    log.info("启动；配置文件 %s", cfgmod.CONFIG_PATH)
    App(cfg, log).start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
