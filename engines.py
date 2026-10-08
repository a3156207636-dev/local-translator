"""翻译后端：全部指向本机端口，不经过任何外部服务器。

  openai —— 任意 OpenAI 兼容端点（Ollama /v1、LM Studio、llama.cpp、vLLM、OneAPI…）
  ollama —— Ollama 原生 /api/chat
  mock   —— 本地假引擎，仅用于管路自检，不联网、不调模型
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Optional

import requests

_QUOTE_CHARS = "「」『』“”\"'《》"

STYLE_HINTS = {
    "natural": "Use natural, idiomatic {lang} that a native speaker would actually write.",
    "native": "Rewrite it the way a native {lang} speaker would say it in this exact situation. "
              "Prioritize fluency and tone over literal fidelity.",
    "literal": "Translate literally and keep the original sentence structure, even if it reads "
               "slightly stiff.",
}

SYSTEM_TEMPLATE = """You are a professional translator.

TASK
Translate from {source} into {target}.

RULES
- Output ONLY the translation. No explanations, no notes, no quotation marks, no original text, \
no labels such as "Translation:".
- Preserve meaning, tone, register and line breaks.
- Keep proper nouns, product names and code identifiers unchanged.
- If the text is already written in {target}, return it unchanged.
- {style}"""


@dataclass
class TranslationResult:
    dst: str = ""
    error: str = ""
    engine: str = ""
    elapsed_ms: int = 0

    @property
    def ok(self) -> bool:
        return bool(self.dst) and not self.error


def _lang_prompt(code: str) -> str:
    """语言代码 -> 给模型的英文说法（如 'zh' -> 'Chinese'）。未知代码原样返回。"""
    try:
        import license as _lic  # 延迟导入，避免与 main 形成硬依赖
        for c, _d, p in _lic.LANGUAGES:
            if c == code:
                return p
    except Exception:
        pass
    return code or "the source language (auto-detect)"


def build_messages(text: str, target_lang: str, style: str, source_lang: str = "zh") -> list[dict[str, str]]:
    """source_lang / target_lang 传语言代码（'zh' / 'en' / 'ja' ... 或 'auto'）。"""
    src = _lang_prompt(source_lang) if source_lang != "auto" else "the source language (auto-detect)"
    tgt = _lang_prompt(target_lang)
    hint = STYLE_HINTS.get(style, STYLE_HINTS["natural"]).format(lang=tgt)
    system = SYSTEM_TEMPLATE.format(source=src, target=tgt, style=hint)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": text},
    ]


def clean_output(raw: str) -> str:
    """去掉模型爱加的各种包装。"""
    if not raw:
        return ""
    s = raw.strip()
    if s.startswith("```"):
        lines = s.split("\n")
        if len(lines) >= 2:
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines).strip()
    for prefix in ("Translation:", "译文：", "译文:", "翻译：", "翻译:", "英文：", "英文:"):
        if s.startswith(prefix):
            s = s[len(prefix) :].strip()
    if len(s) >= 2 and s[0] in _QUOTE_CHARS and s[-1] in _QUOTE_CHARS:
        s = s[1:-1].strip()
    return s


class _Cache:
    def __init__(self, limit: int = 300) -> None:
        self._d: OrderedDict[str, str] = OrderedDict()
        self._limit = limit
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            if key in self._d:
                self._d.move_to_end(key)
                return self._d[key]
        return None

    def put(self, key: str, value: str) -> None:
        with self._lock:
            self._d[key] = value
            self._d.move_to_end(key)
            while len(self._d) > self._limit:
                self._d.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._d.clear()


CACHE = _Cache()


def _iter_sse(resp):
    """解析 OpenAI 风格的 SSE 流，逐段产出文本。"""
    for raw in resp.iter_lines(decode_unicode=False):
        if not raw:
            continue
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        line = line.strip()
        if not line or line.startswith(":"):
            continue
        if line.startswith("data:"):
            line = line[5:].strip()
        if line == "[DONE]":
            break
        try:
            obj = json.loads(line)
        except Exception:
            continue
        choices = obj.get("choices") or []
        if not choices:
            continue
        c0 = choices[0]
        piece = (c0.get("delta") or {}).get("content")
        if piece is None:
            piece = c0.get("text") or ""
        if piece:
            yield piece


def _iter_ndjson(resp):
    """解析 Ollama 的 NDJSON 流。"""
    for raw in resp.iter_lines(decode_unicode=False):
        if not raw:
            continue
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        piece = (obj.get("message") or {}).get("content") or obj.get("response") or ""
        if piece:
            yield piece
        if obj.get("done"):
            break


class BaseEngine:
    label = "base"

    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.session = requests.Session()
        # 关键：忽略系统/环境里的 HTTP 代理。
        # 否则 http_proxy 之类的变量会把本该直连 127.0.0.1 的请求绕到代理上，
        # 既可能失败，也违背「数据不出本机」的前提。
        self.session.trust_env = False
        self._lock = threading.Lock()

    def health(self) -> tuple[bool, str]:
        return True, ""

    def list_models(self) -> list[str]:
        return []

    def translate(self, text: str, target_lang: str, style: str, source_lang: str = "zh") -> TranslationResult:
        raise NotImplementedError

    def stream(self, text: str, target_lang: str, style: str, source_lang: str = "zh"):
        """流式翻译，逐段产出文本。默认实现退化为一次性返回。"""
        res = self.translate(text, target_lang, style, source_lang)
        if res.ok:
            yield res.dst

    # ------------------------------------------------------------------ #
    def _cache_key(self, text: str, target_lang: str, style: str, source_lang: str = "zh") -> str:
        return f"{self.label}|{self.cfg.get('model')}|{source_lang}|{target_lang}|{style}|{text}"

    def run(self, text: str, target_lang: str, style: str, source_lang: str = "zh") -> TranslationResult:
        key = self._cache_key(text, target_lang, style, source_lang)
        hit = CACHE.get(key)
        if hit is not None:
            return TranslationResult(dst=hit, engine=f"{self.label} (cache)", elapsed_ms=0)

        t0 = time.perf_counter()
        try:
            res = self.translate(text, target_lang, style, source_lang)
        except BaseException as e:
            # 首次调用往往要把模型读进显存，容易超时，给一次重试机会
            if "timeout" in type(e).__name__.lower() or "timeout" in str(e).lower():
                try:
                    res = self.translate(text, target_lang, style, source_lang)
                except BaseException as e2:
                    res = TranslationResult(
                        error=f"{type(e2).__name__}: {e2}", engine=self.label
                    )
            else:
                res = TranslationResult(error=f"{type(e).__name__}: {e}", engine=self.label)
        res.elapsed_ms = int((time.perf_counter() - t0) * 1000)
        if res.ok:
            CACHE.put(key, res.dst)
        return res

    def run_stream(self, text: str, target_lang: str, style: str, source_lang: str = "zh"):
        """流式翻译，产出 (增量, 目前已完成的整段译文)。

        命中缓存时直接一次性产出。异常不抛出，只是提前结束——调用方发现
        一个字符都没拿到时，可以退回 run() 拿到明确的错误信息。
        """
        key = self._cache_key(text, target_lang, style, source_lang)
        hit = CACHE.get(key)
        if hit is not None:
            yield hit, hit
            return

        acc = ""
        try:
            for piece in self.stream(text, target_lang, style, source_lang):
                acc += piece
                yield piece, clean_output(acc)
        except BaseException as e:
            if not acc:
                raise
            # 已经吐了一部分，尽量保住这一部分
            _ = e
        final = clean_output(acc)
        if final:
            CACHE.put(key, final)

    def warmup(self) -> tuple[bool, str, int]:
        """把模型预读进显存，避免用户第一次翻译等几十秒。"""
        if self.label == "mock":
            return True, "mock", 0
        t0 = time.perf_counter()
        res = self.run("你好", self.cfg.get("target_lang", "en"),
                       self.cfg.get("style", "natural"),
                       self.cfg.get("source_lang", "zh"))
        ms = int((time.perf_counter() - t0) * 1000)
        return res.ok, res.error or res.dst[:40], ms


class OpenAIEngine(BaseEngine):
    """任意 OpenAI 兼容端点。"""

    label = "openai"

    def _url(self, path: str) -> str:
        base = (self.cfg.get("base_url") or "").rstrip("/")
        return base + path

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        key = self.cfg.get("api_key") or ""
        if key:
            h["Authorization"] = f"Bearer {key}"
        return h

    def health(self) -> tuple[bool, str]:
        try:
            r = self.session.get(self._url("/models"), headers=self._headers(), timeout=3)
            if r.status_code >= 400:
                return False, f"HTTP {r.status_code}"
            data = r.json()
            names = [m.get("id", "") for m in data.get("data", [])]
            return True, ("可用模型: " + ", ".join(names[:6])) if names else "端点可达"
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"

    def list_models(self) -> list[str]:
        try:
            r = self.session.get(self._url("/models"), headers=self._headers(), timeout=3)
            if r.status_code >= 400:
                return []
            return [m.get("id", "") for m in r.json().get("data", []) if m.get("id")]
        except Exception:
            return []

    def translate(self, text, target_lang, style, source_lang: str = "zh") -> TranslationResult:
        body: dict[str, Any] = {
            "model": self.cfg.get("model"),
            "messages": build_messages(text, target_lang, style, source_lang),
            "temperature": float(self.cfg.get("temperature", 0.2)),
            "top_p": float(self.cfg.get("top_p", 0.9)),
            "stream": False,
            "max_tokens": 1024,
        }
        body.update(self.cfg.get("extra_body") or {})
        timeout = float(self.cfg.get("timeout", 30))

        with self._lock:
            r = self.session.post(
                self._url("/chat/completions"),
                headers=self._headers(),
                data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                timeout=timeout,
            )
        if r.status_code >= 400:
            # 有些纯翻译服务只提供 /completions
            if r.status_code in (404, 405):
                return self._translate_legacy(text, target_lang, style, source_lang)
            return TranslationResult(
                error=f"HTTP {r.status_code}: {r.text[:200]}", engine=self.label
            )
        data = r.json()
        choices = data.get("choices") or []
        if not choices:
            return TranslationResult(error="响应中没有 choices", engine=self.label)
        c0 = choices[0]
        raw = (c0.get("message") or {}).get("content") or c0.get("text") or ""
        return TranslationResult(dst=clean_output(raw), engine=self.label)

    def stream(self, text, target_lang, style, source_lang: str = "zh"):
        body: dict[str, Any] = {
            "model": self.cfg.get("model"),
            "messages": build_messages(text, target_lang, style, source_lang),
            "temperature": float(self.cfg.get("temperature", 0.2)),
            "top_p": float(self.cfg.get("top_p", 0.9)),
            "stream": True,
            "max_tokens": 1024,
        }
        body.update(self.cfg.get("extra_body") or {})
        timeout = float(self.cfg.get("timeout", 30))

        with self.session.post(
            self._url("/chat/completions"),
            headers=self._headers(),
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            timeout=(5, timeout),
            stream=True,
        ) as r:
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
            yield from _iter_sse(r)

    def _translate_legacy(self, text, target_lang, style, source_lang: str = "zh") -> TranslationResult:
        msgs = build_messages(text, target_lang, style, source_lang)
        prompt = msgs[0]["content"] + "\n\n" + msgs[1]["content"]
        body = {
            "model": self.cfg.get("model"),
            "prompt": prompt,
            "temperature": float(self.cfg.get("temperature", 0.2)),
            "max_tokens": 1024,
            "stream": False,
        }
        with self._lock:
            r = self.session.post(
                self._url("/completions"),
                headers=self._headers(),
                data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                timeout=float(self.cfg.get("timeout", 30)),
            )
        if r.status_code >= 400:
            return TranslationResult(
                error=f"HTTP {r.status_code}: {r.text[:200]}", engine=self.label
            )
        data = r.json()
        choices = data.get("choices") or []
        if not choices:
            return TranslationResult(error="响应中没有 choices", engine=self.label)
        return TranslationResult(
            dst=clean_output(choices[0].get("text") or ""), engine=self.label
        )


class OllamaEngine(BaseEngine):
    """Ollama 原生接口，支持 keep_alive 让模型常驻显存。"""

    label = "ollama"

    def _host(self) -> str:
        base = (self.cfg.get("base_url") or "http://127.0.0.1:11434").rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        return base

    def health(self) -> tuple[bool, str]:
        try:
            r = self.session.get(self._host() + "/api/tags", timeout=3)
            if r.status_code >= 400:
                return False, f"HTTP {r.status_code}"
            names = [m.get("name", "") for m in r.json().get("models", [])]
            return True, ("已安装: " + ", ".join(names[:6])) if names else "Ollama 在运行，但没有模型"
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"

    def list_models(self) -> list[str]:
        try:
            r = self.session.get(self._host() + "/api/tags", timeout=3)
            if r.status_code >= 400:
                return []
            return [m.get("name", "") for m in r.json().get("models", []) if m.get("name")]
        except Exception:
            return []

    def translate(self, text, target_lang, style, source_lang: str = "zh") -> TranslationResult:
        body = {
            "model": self.cfg.get("model"),
            "messages": build_messages(text, target_lang, style, source_lang),
            "stream": False,
            "keep_alive": self.cfg.get("keep_alive", "30m"),
            "options": {
                "temperature": float(self.cfg.get("temperature", 0.2)),
                "top_p": float(self.cfg.get("top_p", 0.9)),
                "num_predict": 512,
            },
        }
        with self._lock:
            r = self.session.post(
                self._host() + "/api/chat",
                headers={"Content-Type": "application/json"},
                data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                timeout=float(self.cfg.get("timeout", 30)),
            )
        if r.status_code >= 400:
            return TranslationResult(
                error=f"HTTP {r.status_code}: {r.text[:200]}", engine=self.label
            )
        data = r.json()
        raw = (data.get("message") or {}).get("content", "")
        return TranslationResult(dst=clean_output(raw), engine=self.label)


    def stream(self, text, target_lang, style, source_lang: str = "zh"):
        body = {
            "model": self.cfg.get("model"),
            "messages": build_messages(text, target_lang, style, source_lang),
            "stream": True,
            "keep_alive": self.cfg.get("keep_alive", "30m"),
            "options": {
                "temperature": float(self.cfg.get("temperature", 0.2)),
                "top_p": float(self.cfg.get("top_p", 0.9)),
                "num_predict": 512,
            },
        }
        timeout = float(self.cfg.get("timeout", 30))
        with self.session.post(
            self._host() + "/api/chat",
            headers={"Content-Type": "application/json"},
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            timeout=(5, timeout),
            stream=True,
        ) as r:
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
            yield from _iter_ndjson(r)


class MockEngine(BaseEngine):
    """自检用：不联网、不调用模型，只把管路跑通。"""

    label = "mock"

    def health(self) -> tuple[bool, str]:
        return True, "本地假引擎（自检用）"

    def translate(self, text, target_lang, style, source_lang: str = "zh") -> TranslationResult:
        time.sleep(0.15)
        return TranslationResult(
            dst=f"[MOCK → {target_lang} / {style}] {text}  ·  {len(text)} chars",
            engine=self.label,
        )

    def stream(self, text, target_lang, style, source_lang: str = "zh"):
        """假引擎也逐段吐，方便验证流式链路。"""
        full = f"[MOCK → {target_lang} / {style}] {text}"
        for ch in full:
            time.sleep(0.004)
            yield ch


ENGINES = {"openai": OpenAIEngine, "ollama": OllamaEngine, "mock": MockEngine}


def build_engine(cfg: dict) -> BaseEngine:
    kind = (cfg.get("type") or "openai").lower()
    return ENGINES.get(kind, OpenAIEngine)(cfg)


_NOT_LLM = ("embed", "embedding", "bge", "gte", "rerank", "whisper", "tts", "vision-only")
_PREFERRED = ("qwen", "hunyuan", "seed", "tower", "glm", "deepseek", "gemma", "llama",
              "gpt-oss", "mistral", "phi")


def pick_model(models: list[str], current: str = "") -> str:
    """从本机已有模型里挑一个最适合翻译的，避免用户手动填。"""
    if not models:
        return current
    if current and current in models:
        return current

    usable = [m for m in models if not any(h in m.lower() for h in _NOT_LLM)]
    if not usable:
        usable = list(models)

    def score(m: str) -> tuple[int, int, int]:
        low = m.lower()
        instruct = 0 if ("instruct" in low or "-it" in low or ":latest" in low) else 1
        family = 0 if any(p in low for p in _PREFERRED) else 1
        return (instruct, family, len(m))

    return sorted(usable, key=score)[0]


def autofit_model(cfg: dict, engine: "BaseEngine") -> str:
    """配置里的模型不存在时，自动改用本机已有的模型，并就地写回 cfg。"""
    if engine.label == "mock":
        return ""
    models = engine.list_models()
    if not models:
        return ""
    cur = (cfg.get("engine") or {}).get("model", "")
    picked = pick_model(models, cur)
    if picked and picked != cur:
        cfg.setdefault("engine", {})["model"] = picked
        return picked
    return ""


# --------------------------------------------------------------------------- #
# 本机可用服务探测（只看 127.0.0.1，不发往外部）
# --------------------------------------------------------------------------- #
LOCAL_PROBES = [
    ("Ollama", "http://127.0.0.1:11434", "/api/tags", "ollama"),
    ("LM Studio", "http://127.0.0.1:1234", "/v1/models", "openai"),
    ("llama.cpp server", "http://127.0.0.1:8080", "/v1/models", "openai"),
    ("vLLM", "http://127.0.0.1:8000", "/v1/models", "openai"),
    ("OneAPI / NewAPI", "http://127.0.0.1:3000", "/v1/models", "openai"),
    ("Xinference", "http://127.0.0.1:9997", "/v1/models", "openai"),
]


def probe_local_endpoints(timeout: float = 1.2) -> list[dict[str, Any]]:
    """扫描常见本地推理端口，返回可用的服务与模型列表。"""
    session = requests.Session()
    session.trust_env = False  # 直连本机，不走任何代理
    found: list[dict[str, Any]] = []
    for name, host, path, kind in LOCAL_PROBES:
        try:
            r = session.get(host + path, timeout=timeout)
            if r.status_code >= 400:
                continue
            data = r.json()
            if kind == "ollama":
                models = [m.get("name", "") for m in data.get("models", [])]
            else:
                models = [m.get("id", "") for m in data.get("data", [])]
            found.append(
                {"name": name, "host": host, "kind": kind, "models": [m for m in models if m]}
            )
        except Exception:
            continue
    return found


# --------------------------------------------------------------------------- #
# Ollama 进程管理（工具启动时顺手把服务拉起来）
# --------------------------------------------------------------------------- #
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


def find_ollama_exe() -> str:
    """找 Ollama 的托盘程序。"""
    cands = [
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama app.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe"),
        os.path.join(os.environ.get("ProgramFiles", ""), "Ollama", "ollama app.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Ollama", "ollama app.exe"),
    ]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return ""


def start_ollama() -> tuple[bool, str]:
    """把 Ollama 拉起来（已运行则不会重复启动）。"""
    exe = find_ollama_exe()
    if not exe:
        return False, "本机没有安装 Ollama"
    try:
        subprocess.Popen(
            [exe],
            cwd=os.path.dirname(exe),
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            close_fds=True,
        )
        return True, exe
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def wait_for_health(engine: "BaseEngine", timeout: float = 45.0) -> bool:
    """等服务就绪。"""
    end = time.time() + timeout
    while time.time() < end:
        ok, _ = engine.health()
        if ok:
            return True
        time.sleep(1.5)
    return False
