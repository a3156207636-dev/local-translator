"""硬件 / 系统探测：判断这台机器适合跑多大的本地翻译模型。

全部只读本机信息，不联网。nvidia-smi 不存在时安静跳过（核显机器上很常见）。
"""
from __future__ import annotations

import ctypes
import platform
import subprocess
import sys
from typing import Any

CREATE_NO_WINDOW = 0x08000000

# 推荐档位：(最低显存 MB, 模型, 下载体积说明, 理由)
TIERS = [
    (8000, "qwen2.5:7b-instruct-q4_K_M", "约 4.7 GB", "独显显存充足，译文质量最好"),
    (4500, "qwen2.5:3b-instruct-q4_K_M", "约 2.0 GB", "独显显存一般，速度与质量平衡"),
    (0,    "qwen2.5:1.5b-instruct-q4_K_M", "约 1.0 GB", "无独显或显存较小，纯 CPU 也能跑"),
]

NO_GPU_TIER = (0, "qwen2.5:1.5b-instruct-q4_K_M", "约 1.0 GB", "没有独显，小模型更流畅")


def _run(cmd: list[str], timeout: float = 8.0) -> str:
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
        return r.stdout or ""
    except Exception:
        return ""


# --------------------------------------------------------------------------- #
def is_windows() -> bool:
    return sys.platform.startswith("win")


def system_info() -> dict[str, Any]:
    ver = platform.version()
    try:
        wv = sys.getwindowsversion()
        label = f"Windows {wv.major}.{wv.minor} (build {wv.build})"
    except Exception:
        label = platform.system() + " " + ver
    return {
        "os": label,
        "arch": platform.machine(),
        "python": platform.python_version(),
        "bits": 64 if sys.maxsize > 2 ** 32 else 32,
    }


def gpu_info() -> list[dict[str, Any]]:
    """列出 NVIDIA 显卡。没有 nvidia-smi 就返回空列表。"""
    out = _run([
        "nvidia-smi",
        "--query-gpu=name,memory.total,driver_version",
        "--format=csv,noheader,nounits",
    ])
    gpus: list[dict[str, Any]] = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            continue
        try:
            vram = int(float(parts[1]))
        except ValueError:
            vram = 0
        gpus.append({
            "name": parts[0],
            "vram_mb": vram,
            "driver": parts[2] if len(parts) > 2 else "",
        })
    return gpus


def total_ram_gb() -> float:
    """物理内存总量（GB）。"""
    try:
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        st = MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return round(st.ullTotalPhys / (1024 ** 3), 1)
    except Exception:
        pass
    return 0.0


def summarize() -> dict[str, Any]:
    """给向导用的一份汇总。"""
    gpus = gpu_info()
    vram = max((g["vram_mb"] for g in gpus), default=0)
    return {
        **system_info(),
        "gpus": gpus,
        "vram_mb": vram,
        "gpu_name": gpus[0]["name"] if gpus else "未检测到 NVIDIA 独显",
        "ram_gb": total_ram_gb(),
    }


def recommend_model(info: dict[str, Any] | None = None) -> tuple[str, str, str]:
    """按硬件挑一个模型。返回 (模型名, 下载体积, 理由)。"""
    info = info or summarize()
    vram = int(info.get("vram_mb") or 0)
    ram = float(info.get("ram_gb") or 0)

    if vram <= 0:
        if ram >= 16:
            return (
                "qwen2.5:3b-instruct-q4_K_M",
                "约 2.0 GB",
                "没有独显但内存充足，3B 纯 CPU 也能用",
            )
        return NO_GPU_TIER[1], NO_GPU_TIER[2], NO_GPU_TIER[3]

    for need, model, size, why in TIERS:
        if vram >= need:
            return model, size, why
    return NO_GPU_TIER[1], NO_GPU_TIER[2], NO_GPU_TIER[3]
