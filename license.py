"""许可证（Pro / Max）与免费版配额。

付费档位通过 Gumroad 的 license key 激活：
  * 首次激活需要联网一次（调用 Gumroad 官方验证接口）
  * 验证通过后结果缓存在本机，之后**离线也能正常使用**
  * 之后每 7 天在后台静默复查一次；订阅到期/退款/拒付会自动降回免费版

开源后的授权说明（AGPLv3）：
  本项目以 AGPLv3 协议开源，**所有翻译功能对所有人免费开放、不再做任何功能限制**——
  任意语言对互译、7B+ 大模型、无限次数、开机自启，免费版即可用。
  下面这套 license 机制现在只做一件事：登记「商业授权 / 优先级支持」状态。
  在设置里填入 Gumroad Pro / Max 密钥的用户会显示为「已激活」，并享有预编译便携版、
  优先答疑等附加服务；没买的也能用全部功能（这是「增值服务」而非「功能锁」）。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional, Tuple

APP_DIR = Path(__file__).resolve().parent
STATE_PATH = APP_DIR / "license.json"
USAGE_PATH = APP_DIR / "usage.json"

# --- 商品信息（上架后如 permalink 有变，改这里即可） ------------------------- #
PRODUCT_PERMALINK = "local-bilingual-translator-pro"
PRODUCT_NAME = "本地双语翻译 · Pro"
PRODUCT_URL = f"https://gumroad.com/l/{PRODUCT_PERMALINK}"

MAX_PRODUCT_PERMALINK = "local-bilingual-translator-max"
MAX_PRODUCT_NAME = "本地双语翻译 · Max"
MAX_PRODUCT_URL = f"https://gumroad.com/l/{MAX_PRODUCT_PERMALINK}"

FREE_PRODUCT_URL = "https://gumroad.com/l/local-bilingual-translator"
VERIFY_URL = "https://api.gumroad.com/v2/licenses/verify"

FREE_DAILY_LIMIT = 100
RECHECK_AFTER_S = 7 * 24 * 3600  # 7 天复查一次

# 档位常量
TIER_FREE = "free"
TIER_PRO = "pro"
TIER_MAX = "max"

# --- 模型档位（开源后已不再限制） ----------------------------------------- #
# 历史遗留：曾经免费版仅放行 3B 及以下的小模型。开源后不再限制，任意本地/在线
# 模型都可使用；保留常量仅为兼容旧调用与日志。
FREE_MAX_PARAMS_B = 3.0

# Pro 专属推荐模型（翻译质量更好，需要 6GB+ 显存）
PRO_MODEL_PRESETS: list[Tuple[str, str]] = [
    ("qwen2.5:7b-instruct-q4_K_M", "通用 7B，中文语感最好，6GB 显存"),
    ("hunyuan-mt:7b", "腾讯混元，WMT2025 翻译赛道冠军"),
    ("seed-x:7b", "字节跳动专用翻译模型"),
    ("qwen2.5:14b-instruct-q4_K_M", "14B 高质量版，12GB+ 显存"),
]

# --- 语言对（开源后已全开放） --------------------------------------------- #
# 所有语言均可任意组合互译（含源语言自动检测）。
# 每项 = (代码, 界面显示名, 给模型的英文说法)
LANGUAGES: list[Tuple[str, str, str]] = [
    ("auto", "自动检测", "the language you detect"),
    ("zh", "中文 Chinese", "Chinese"),
    ("en", "英语 English", "English"),
    ("ja", "日语 Japanese", "Japanese"),
    ("ko", "韩语 Korean", "Korean"),
    ("fr", "法语 French", "French"),
    ("de", "德语 German", "German"),
    ("es", "西班牙语 Spanish", "Spanish"),
    ("ru", "俄语 Russian", "Russian"),
    ("pt", "葡萄牙语 Portuguese", "Portuguese"),
    ("it", "意大利语 Italian", "Italian"),
    ("th", "泰语 Thai", "Thai"),
    ("vi", "越南语 Vietnamese", "Vietnamese"),
    ("ar", "阿拉伯语 Arabic", "Arabic"),
]

# 历史遗留：曾经非 Max 档位只允许这一组；开源后已不再限制，保留仅为兼容。
FREE_LOCKED_PAIR = ("zh", "en")

_lock = threading.RLock()


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def _read_json(path: Path) -> dict:
    try:
        if path.exists():
            d = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                return d
    except Exception:
        pass
    return {}


def _write_json(path: Path, data: dict) -> None:
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    except Exception:
        pass


def _today() -> str:
    return _dt.date.today().isoformat()


def _system_proxy() -> Optional[str]:
    """读 Windows 系统代理（IE 设置）。没开就返回 None。"""
    try:
        import winreg  # type: ignore
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        )
        enable, _ = winreg.QueryValueEx(key, "ProxyEnable")
        if not enable:
            return None
        server, _ = winreg.QueryValueEx(key, "ProxyServer")
        if not server:
            return None
        if "=" in server:  # 形如 http=127.0.0.1:7890;https=127.0.0.1:7890
            parts = dict(p.split("=", 1) for p in server.split(";") if "=" in p)
            server = parts.get("https") or parts.get("http") or next(iter(parts.values()))
        if not server.startswith(("http://", "https://", "socks")):
            server = "http://" + server
        return server
    except Exception:
        return None


def _proxy_candidates() -> list[Optional[str]]:
    """按「成功率从高到低」给出代理候选，最后一个是直连。"""
    out: list[Optional[str]] = []
    for cand in (
        _system_proxy(),
        os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        or os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy"),
        os.environ.get("ALL_PROXY") or os.environ.get("all_proxy"),
    ):
        if cand and cand not in out:
            out.append(cand)
    out.append(None)  # 直连兜底
    return out


# --------------------------------------------------------------------------- #
# 远端校验
# --------------------------------------------------------------------------- #
def _verify_with(key: str, permalink: str) -> Tuple[bool, str, dict]:
    """对某一个商品校验密钥."""
    try:
        import requests
    except Exception as e:  # pragma: no cover
        return False, f"缺少 requests 依赖：{e}", {}

    payload = {
        "product_permalink": permalink,
        "license_key": key,
        "increment_uses_count": "false",
    }

    last_err = ""
    for proxy in _proxy_candidates():
        try:
            s = requests.Session()
            s.trust_env = False
            if proxy:
                s.proxies = {"http": proxy, "https": proxy}
            r = s.post(VERIFY_URL, data=payload, timeout=20)
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            continue

        if r.status_code >= 500:
            last_err = f"Gumroad 服务异常（HTTP {r.status_code}）"
            continue
        try:
            j = r.json()
        except Exception:
            last_err = f"返回内容无法解析（HTTP {r.status_code}）"
            continue

        if j.get("success"):
            p = j.get("purchase") or {}
            if p.get("refunded"):
                return False, "该订单已退款，许可证已失效", p
            if p.get("chargebacked"):
                return False, "该订单已被拒付，许可证已失效", p
            if p.get("subscription_ended_at"):
                return False, "订阅已结束，许可证已失效", p
            p["_permalink"] = permalink
            return True, "验证通过", p

        # 明确的不匹配（换下一个商品试）
        return False, "NOT_THIS_PRODUCT", {}

    # 到这里说明网络层面就不通
    hint = ""
    if "TUNNEL" in last_err.upper() or "PROXY" in last_err.upper():
        hint = "（疑似代理问题：如本机开了代理软件，可在系统设置里打开「使用代理服务器」后重试）"
    return False, f"连不上 Gumroad{ '，' + last_err if last_err else ''}{hint}", {}


def verify(key: str) -> Tuple[bool, str, dict]:
    """调用 Gumroad 官方接口校验一个 license key。

    会依次尝试 Max、Pro 两个商品，从而判断出密钥对应的档位。
    返回 (是否有效, 说明, purchase 信息)
    """
    key = (key or "").strip()
    if not key:
        return False, "密钥为空", {}

    for permalink in (MAX_PRODUCT_PERMALINK, PRODUCT_PERMALINK):
        ok, msg, purchase = _verify_with(key, permalink)
        if ok:
            return True, msg, purchase
        if msg == "NOT_THIS_PRODUCT":
            continue
        # 网络不通 / 退款等明确失败，直接返回
        return False, msg if msg != "验证通过" else msg, purchase

    return False, ("验证未通过：密钥有误，或不属于本软件的 Pro / Max 商品"
                   f"（Pro：{PRODUCT_URL}　Max：{MAX_PRODUCT_URL}）"), {}


# --------------------------------------------------------------------------- #
# 本地状态
# --------------------------------------------------------------------------- #
def load_state() -> dict:
    with _lock:
        return _read_json(STATE_PATH)


def _save_state(st: dict) -> None:
    with _lock:
        _write_json(STATE_PATH, st)


def tier() -> str:
    """当前档位：free / pro / max。纯本地判断，不联网。"""
    st = load_state()
    if not (st.get("key") and not st.get("invalid")):
        return TIER_FREE
    return TIER_MAX if st.get("tier") == TIER_MAX else TIER_PRO


def is_pro() -> bool:
    """是否至少是付费档（Pro 或 Max）。保留此名以兼容旧调用。"""
    return tier() in (TIER_PRO, TIER_MAX)


def is_max() -> bool:
    """是否 Max 档（解锁任意语言对）。"""
    return tier() == TIER_MAX


def needs_recheck() -> bool:
    st = load_state()
    if not st.get("key"):
        return False
    return (time.time() - float(st.get("last_verified", 0))) > RECHECK_AFTER_S


def activate(key: str) -> Tuple[bool, str]:
    """激活：联网校验一次，成功后写入本地。自动识别 Pro / Max 档位。"""
    ok, msg, purchase = verify(key)
    if not ok:
        return False, msg
    permalink = purchase.get("_permalink", "")
    plan = TIER_MAX if permalink == MAX_PRODUCT_PERMALINK else TIER_PRO
    label = MAX_PRODUCT_NAME if plan == TIER_MAX else PRODUCT_NAME
    st = load_state()
    st.update({
        "key": key.strip(),
        "tier": plan,
        "permalink": permalink,
        "email": purchase.get("email", ""),
        "product": purchase.get("product_name", label),
        "plan": "subscription" if purchase.get("subscription_id") else "purchase",
        "activated_at": st.get("activated_at") or time.time(),
        "last_verified": time.time(),
        "invalid": False,
    })
    _save_state(st)
    return True, f"已激活 {label}（{st.get('email') or st['key']}）"


def deactivate() -> None:
    st = load_state()
    _write_json(STATE_PATH, {
        "key": "",
        "email": "",
        "invalid": False,
        "last_verified": 0,
        "deactivated_at": time.time(),
        "prev_key": st.get("key", ""),
    })


def refresh(silent: bool = True) -> Tuple[bool, str]:
    """后台复查。订阅被取消/退款/拒付时会自动降级到免费版。"""
    st = load_state()
    key = st.get("key")
    if not key:
        return False, "未激活"
    permalink = st.get("permalink") or PRODUCT_PERMALINK
    ok, msg, purchase = verify(key)
    if ok:
        plan = TIER_MAX if purchase.get("_permalink") == MAX_PRODUCT_PERMALINK else TIER_PRO
        st["tier"] = plan
        st["permalink"] = purchase.get("_permalink", permalink)
        st["product"] = purchase.get("product_name", st.get("product", ""))
        st["last_verified"] = time.time()
        st["invalid"] = False
        st["email"] = purchase.get("email", st.get("email", ""))
        _save_state(st)
        return True, "许可证有效"
    # 只有「明确判定别人不买了」才降级；网络不通时保留原档位，避免误伤
    hard = any(w in msg for w in ("已退款", "已拒付", "订阅已结束", "密钥无效", "验证未通过"))
    if hard:
        st["invalid"] = True
        st["last_error"] = msg
        st["last_verified"] = time.time()
        _save_state(st)
    return False, msg


# --------------------------------------------------------------------------- #
# 免费版配额
# --------------------------------------------------------------------------- #
def usage_today() -> int:
    with _lock:
        d = _read_json(USAGE_PATH)
        return int(d.get("count", 0)) if d.get("date") == _today() else 0


def remaining_today() -> Optional[int]:
    """开源版不再限制翻译次数，始终返回 None（不限量）。保留以兼容旧调用。"""
    return None


def consume() -> Tuple[bool, int]:
    """翻译前调用，记一次用量（仅用于展示，不再限制）。返回 (是否放行, 今日已用)。"""
    with _lock:
        d = _read_json(USAGE_PATH)
        if d.get("date") != _today():
            d = {"date": _today(), "count": 0}
        c = int(d.get("count", 0)) + 1
        d["count"] = c
        _write_json(USAGE_PATH, d)
    return True, c


def refund_one() -> None:
    """翻译最终失败时把这一笔用量还回去（仅展示用，无上限限制）。"""
    with _lock:
        d = _read_json(USAGE_PATH)
        if d.get("date") != _today():
            return
        d["count"] = max(0, int(d.get("count", 0)) - 1)
        _write_json(USAGE_PATH, d)


def quota_message() -> str:
    # 开源后不再限制次数，此提示仅作兼容保留，正常流程不会触发。
    return (f"翻译功能在开源版中不限次数。\n"
            f"如需商业授权 / 优先级支持，可在此购买：{PRODUCT_URL}")


# --------------------------------------------------------------------------- #
# 模型档位
# --------------------------------------------------------------------------- #
def _params_b(model: str) -> Optional[float]:
    """从模型名里读参数量，比如 qwen2.5:7b-instruct → 7.0。读不出返回 None。"""
    import re
    m = re.search(r"(\d+(?:\.\d+)?)\s*b(?![a-z0-9])", (model or "").lower())
    if m:
        try:
            return float(m.group(1))
        except Exception:
            return None
    return None


def model_allowed(model: str) -> bool:
    """开源版不再限制模型大小，任意本地 / 在线模型均可使用，始终返回 True。"""
    return True


def model_message(model: str) -> str:
    # 开源后任意模型均可使用，此提示仅作兼容保留。
    return (f"「{model}」可直接使用。\n"
            f"如需商业授权 / 优先级支持，可在此购买：{PRODUCT_URL}")



# --------------------------------------------------------------------------- #
# 语言对权限
# --------------------------------------------------------------------------- #
def _lang_name(code: str) -> str:
    for c, disp, _p in LANGUAGES:
        if c == code:
            return disp
    return code


def _lang_prompt(code: str) -> str:
    for c, _d, p in LANGUAGES:
        if c == code:
            return p
    return code


def available_pairs() -> list[Tuple[str, str]]:
    """开源版开放全部语言，返回所有可选语言对（任意互译）。"""
    return [(c, d) for c, d, _p in LANGUAGES]


def pair_allowed(src: str, tgt: str) -> bool:
    """开源版开放任意语言对互译，始终允许。"""
    return True


def pair_message(src: str, tgt: str) -> str:
    s, t = _lang_name(src), _lang_name(tgt)
    # 开源后任意语言对均可互译，此提示仅作兼容保留。
    return (f"「{s} → {t}」可直接互译。\n"
            f"如需商业授权 / 优先级支持，可在此购买：{MAX_PRODUCT_URL}")


def describe() -> str:
    """给界面用的一行状态。"""
    t = tier()
    if t == TIER_MAX:
        st = load_state()
        who = st.get("email") or st.get("key", "")
        return f"Max 已激活 · 任意语言互译（{who}）"
    if t == TIER_PRO:
        st = load_state()
        who = st.get("email") or st.get("key", "")
        return f"Pro 已激活（{who}）"
    return f"免费版（开源 · 全部功能可用）· 今日已用 {usage_today()}"


def status_line() -> str:
    return {"max": "MAX", "pro": "Pro"}.get(tier(), "FREE")
