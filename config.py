"""配置管理：默认值、深度合并、读写 config.json。"""
from __future__ import annotations

import copy
import json
import threading
from pathlib import Path
from typing import Any

APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"
LOG_DIR = APP_DIR / "logs"

_lock = threading.RLock()

DEFAULTS: dict[str, Any] = {
    "engine": {
        # openai  = 任意 OpenAI 兼容端点（Ollama /v1、LM Studio、llama.cpp、vLLM、OneAPI）
        # ollama  = Ollama 原生 /api/chat
        # mock    = 本地假引擎，仅用于自检，不联网不调模型
        "type": "openai",
        "base_url": "http://127.0.0.1:11434/v1",
        "api_key": "local",
        "model": "qwen2.5:7b-instruct-q4_K_M",
        "timeout": 120,
        "temperature": 0.2,
        "top_p": 0.9,
        "source_lang": "zh",             # auto | zh | en | ja | ko | ...（见 license.LANGUAGES）
        "target_lang": "en",             # 目标语言代码；Max 档可任意切换
        "style": "natural",  # natural | native | literal
        "extra_body": {},
    },
    "trigger": {
        "auto_translate": True,      # 停止打字后自动翻译
        "debounce_ms": 300,          # 停止打字多久后才翻译（越小越快，越小也越容易打断）
        "instant_popup": True,       # 一按下第一个字符就立刻弹出悬浮窗（不等翻译结果）
        "min_chars": 2,              # 少于该字数不翻译
        "max_chars": 600,            # 送入模型的原文最大长度（从尾部截取）
        "require_cjk": True,         # 原文不含中日韩字符则跳过（纯英文不翻）
        "hotkey": "ctrl+alt+t",      # 手动翻译快捷键
        "hotkey_mode": "selection",  # selection（翻译选中文本）| clipboard（翻译剪贴板）
        "cooldown_ms": 150,          # 两次自动翻译之间的最小间隔
    },
    "context": {
        "reader": "auto",            # auto | uia | win32
        "take": "last_sentence",     # last_sentence | last_line | tail | all
        "tail_chars": 200,
    },
    "overlay": {
        "position": "follow_caret",  # follow_caret | follow_mouse | fixed
        "fixed_x": 1200,
        "fixed_y": 240,
        "width": 480,
        "opacity": 0.97,
        "font_family": "Microsoft YaHei UI",
        "src_font_size": 11,
        "dst_font_size": 15,
        "accent": "#185FA5",
        "auto_hide_ms": 0,           # 0 = 不自动隐藏（窗口一直留在屏幕上）
        "show_source": True,
        "show_footer": True,
        "hide_on_typing": False,     # 再次开始打字时立即隐藏（默认关闭：窗口常驻）
        "streaming": True,           # 流式输出：译文逐字浮现，体感更快
    },
    "app": {
        "show_pill": True,       # 屏幕上常驻的小状态球（右键有菜单）
        "start_minimized": True,
        "autostart": False,          # 开机自动启动（写入 HKCU Run）
        "autostart_delay_s": 0,      # 登录后额外等几秒再启动，0 = 只等桌面就绪
        "log_level": "INFO",
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """把 override 合并进 base 的副本；dict 递归，其余直接覆盖。"""
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load() -> dict:
    """读取配置；文件不存在或损坏时回落到默认值（并补全缺失项）。"""
    with _lock:
        if CONFIG_PATH.exists():
            try:
                raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    return _deep_merge(DEFAULTS, raw)
            except Exception:
                pass
        cfg = copy.deepcopy(DEFAULTS)
        _write(cfg)
        return cfg


def save(cfg: dict) -> None:
    with _lock:
        _write(cfg)


def _write(cfg: dict) -> None:
    try:
        CONFIG_PATH.write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


def get(cfg: dict, path: str, default: Any = None) -> Any:
    """按 "engine.model" 形式取嵌套值。"""
    cur: Any = cfg
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur
