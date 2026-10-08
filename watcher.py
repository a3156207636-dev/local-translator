"""全局键盘监听：判断「停止打字」触发翻译，并处理手动快捷键。

注意：中文输入法上屏的汉字拿不到原始键值，所以键盘钩子只用来判断
「用户在打字 / 什么时候停下来」，真正的文本由 context_reader 从焦点控件读取。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

from pynput import keyboard

log = logging.getLogger("lbt")

_MOD_KEYS = {
    keyboard.Key.ctrl: "ctrl",
    keyboard.Key.ctrl_l: "ctrl",
    keyboard.Key.ctrl_r: "ctrl",
    keyboard.Key.alt: "alt",
    keyboard.Key.alt_l: "alt",
    keyboard.Key.alt_r: "alt",
    keyboard.Key.alt_gr: "alt",
    keyboard.Key.shift: "shift",
    keyboard.Key.shift_l: "shift",
    keyboard.Key.shift_r: "shift",
    keyboard.Key.cmd: "win",
    keyboard.Key.cmd_l: "win",
    keyboard.Key.cmd_r: "win",
}

_TEXT_KEYS = {
    keyboard.Key.space,
    keyboard.Key.backspace,
    keyboard.Key.delete,
    keyboard.Key.enter,
}


def parse_hotkey(spec: str) -> tuple[set[str], str]:
    """"ctrl+alt+t" -> ({"ctrl","alt"}, "t")"""
    mods: set[str] = set()
    key = ""
    for part in (spec or "").lower().replace(" ", "").split("+"):
        if not part:
            continue
        if part in ("ctrl", "control"):
            mods.add("ctrl")
        elif part == "alt":
            mods.add("alt")
        elif part == "shift":
            mods.add("shift")
        elif part in ("win", "cmd", "super"):
            mods.add("win")
        else:
            key = part
    return mods, key


class TypingWatcher:
    """监听键盘，静默一段时间后回调 on_pause。"""

    def __init__(
        self,
        cfg: dict,
        on_pause: Callable[[], None],
        on_hotkey: Callable[[], None],
        on_typing: Optional[Callable[[], None]] = None,
        on_state: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.cfg = cfg
        self.on_pause = on_pause
        self.on_hotkey = on_hotkey
        self.on_typing = on_typing or (lambda: None)
        self.on_state = on_state or (lambda _s: None)

        self.paused = False
        self._last_key = 0.0
        self._dirty = False
        self._mods: set[str] = set()
        self._hotkey_latched = False
        self._typing_announced = False
        self._stop = threading.Event()
        self._listener: Optional[keyboard.Listener] = None
        self._monitor: Optional[threading.Thread] = None
        self.reload_hotkey()

    # ------------------------------------------------------------------ #
    def reload_hotkey(self) -> None:
        trig = self.cfg.get("trigger", {})
        self.hk_mods, self.hk_key = parse_hotkey(trig.get("hotkey", "ctrl+alt+t"))
        self.debounce = int(trig.get("debounce_ms", 900)) / 1000.0

    def start(self) -> None:
        self._stop.clear()
        self._listener = keyboard.Listener(
            on_press=self._on_press, on_release=self._on_release, suppress=False
        )
        self._listener.daemon = True
        self._listener.start()
        self._monitor = threading.Thread(target=self._loop, name="debounce", daemon=True)
        self._monitor.start()

    def stop(self) -> None:
        self._stop.set()
        if self._listener:
            try:
                self._listener.stop()
            except Exception:
                pass

    def set_paused(self, paused: bool) -> None:
        self.paused = paused
        if paused:
            self._dirty = False

    # ------------------------------------------------------------------ #
    def _on_press(self, key) -> None:
        now = time.monotonic()

        mod = _MOD_KEYS.get(key)
        if mod:
            self._mods.add(mod)
            self._last_key = now
            return

        char = getattr(key, "char", None)
        is_text = (char is not None and char.isprintable()) or key in _TEXT_KEYS

        # 快捷键
        if not self._hotkey_latched:
            key_name = char.lower() if (char and char.isprintable()) else str(key).replace(
                "Key.", ""
            ).lower()
            if self.hk_key and key_name == self.hk_key and self.hk_mods <= self._mods:
                self._hotkey_latched = True
                self._dirty = False
                threading.Thread(target=self._safe_hotkey, daemon=True).start()
                return

        if is_text:
            self._last_key = now
            if not self._typing_announced:
                self._typing_announced = True
                try:
                    self.on_typing()
                except Exception:
                    pass
            if not self.paused:
                self._dirty = True
        else:
            self._last_key = now

    def _on_release(self, key) -> None:
        mod = _MOD_KEYS.get(key)
        if mod:
            self._mods.discard(mod)
        if self._hotkey_latched and not (self.hk_mods & self._mods):
            self._hotkey_latched = False

    def _safe_hotkey(self) -> None:
        try:
            self.on_hotkey()
        except Exception:
            log.exception("手动快捷键回调出错")

    def _loop(self) -> None:
        while not self._stop.is_set():
            time.sleep(0.03)
            try:
                now = time.monotonic()

                if self._typing_announced and (now - self._last_key) > 0.55:
                    self._typing_announced = False

                if self.paused or not self._dirty:
                    continue
                if self._mods & {"ctrl", "alt", "win"}:
                    # 正在按组合键，先不触发
                    continue
                if (now - self._last_key) < self.debounce:
                    continue

                self._dirty = False
                log.debug("停顿 %.2fs，触发翻译", now - self._last_key)
                try:
                    self.on_pause()
                except Exception:
                    log.exception("自动翻译回调出错")
            except Exception:
                log.exception("防抖循环异常")
