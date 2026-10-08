"""Ollama 的检测 / 下载 / 静默安装 / 拉模型。

只在首次使用向导里用到。全程只跟 ollama.com 和本机 11434 端口打交道。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200

DOWNLOAD_URL = "https://ollama.com/download/OllamaSetup.exe"
HOST = "http://127.0.0.1:11434"

Progress = Callable[[str], None]
StopFlag = Callable[[], bool]


def _noop(_msg: str) -> None:
    pass


# --------------------------------------------------------------------------- #
def ollama_dirs() -> list[Path]:
    la = os.environ.get("LOCALAPPDATA", "")
    pf = os.environ.get("ProgramFiles", "")
    pf86 = os.environ.get("ProgramFiles(x86)", "")
    return [
        Path(la) / "Programs" / "Ollama",
        Path(pf) / "Ollama",
        Path(pf86) / "Ollama",
    ]


def find_cli() -> str:
    """找 ollama.exe（命令行）。"""
    found = shutil.which("ollama")
    if found:
        return found
    for d in ollama_dirs():
        exe = d / "ollama.exe"
        if exe.is_file():
            return str(exe)
    return ""


def find_app() -> str:
    """找托盘程序 ollama app.exe。"""
    for d in ollama_dirs():
        for name in ("ollama app.exe", "ollama.exe"):
            exe = d / name
            if exe.is_file():
                return str(exe)
    return ""


def is_installed() -> bool:
    return bool(find_cli() or find_app())


def server_alive(timeout: float = 1.5) -> bool:
    import requests

    s = requests.Session()
    s.trust_env = False
    try:
        r = s.get(HOST + "/api/tags", timeout=timeout)
        return r.status_code < 400
    except Exception:
        return False


def local_models() -> list[str]:
    import requests

    s = requests.Session()
    s.trust_env = False
    try:
        r = s.get(HOST + "/api/tags", timeout=4)
        return [m.get("name", "") for m in r.json().get("models", [])]
    except Exception:
        return []


def start_server() -> tuple[bool, str]:
    """把 Ollama 拉起来（托盘程序会顺带起服务）。"""
    if server_alive():
        return True, "服务已在运行"
    app = find_app()
    if not app:
        return False, "找不到 ollama app.exe"
    try:
        subprocess.Popen(
            [app],
            cwd=os.path.dirname(app),
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, close_fds=True,
        )
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    return True, "已发出启动命令"


def wait_server(timeout: float = 60.0, stop: Optional[StopFlag] = None) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if server_alive():
            return True
        if stop and stop():
            return False
        time.sleep(1.0)
    return False


# --------------------------------------------------------------------------- #
def download_installer(progress: Progress = _noop,
                       stop: Optional[StopFlag] = None) -> Path:
    """下载 OllamaSetup.exe 到临时目录。"""
    import requests

    dest_dir = Path(tempfile.gettempdir()) / "ollama-setup"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "OllamaSetup.exe"
    if dest.is_file() and dest.stat().st_size > 50 * 1024 * 1024:
        progress("已有下载好的安装包，直接使用")
        return dest

    progress("正在下载 Ollama 安装包（约 700 MB，视网速可能要几分钟）…")
    s = requests.Session()
    with s.get(DOWNLOAD_URL, stream=True, timeout=30) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        last = 0.0
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=512 * 1024):
                if stop and stop():
                    raise RuntimeError("已取消")
                if not chunk:
                    continue
                f.write(chunk)
                got += len(chunk)
                now = time.time()
                if now - last >= 0.4:
                    last = now
                    if total:
                        progress(
                            f"下载中 {got / 1048576:.0f} / {total / 1048576:.0f} MB"
                            f"（{got * 100 // total}%）"
                        )
                    else:
                        progress(f"下载中 {got / 1048576:.0f} MB")
    progress("安装包下载完成")
    return dest


def install_silent(exe: Path, progress: Progress = _noop) -> tuple[bool, str]:
    """静默安装（装到当前用户目录，不需要管理员权限）。"""
    if not exe.is_file():
        return False, "安装包不存在"
    progress("正在静默安装 Ollama（约 1 分钟）…")
    last = "安装失败"
    for flags in (["/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES"],
                  ["/SILENT", "/NORESTART"]):
        try:
            r = subprocess.run(
                [str(exe), *flags],
                timeout=600, creationflags=CREATE_NO_WINDOW,
            )
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
            continue
        if r.returncode == 0:
            progress("Ollama 安装完成")
            return True, "ok"
        last = f"安装器退出码 {r.returncode}"
    return False, last


# --------------------------------------------------------------------------- #
class Puller:
    """跑 `ollama pull <model>`，把进度回调出去，支持中途停止。"""

    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._stopped = False

    def stop(self) -> None:
        self._stopped = True
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except Exception:
                pass

    @property
    def stopped(self) -> bool:
        return self._stopped

    def pull(self, model: str, progress: Progress = _noop) -> tuple[bool, str]:
        cli = find_cli()
        if not cli:
            return False, "找不到 ollama.exe"

        if not server_alive():
            start_server()
            if not wait_server(60, lambda: self._stopped):
                return False, "Ollama 服务没起来"

        if model in local_models():
            progress(f"模型 {model} 已存在，无需下载")
            return True, "已存在"

        progress(f"开始下载模型 {model} …")
        try:
            self._proc = subprocess.Popen(
                [cli, "pull", model],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                creationflags=CREATE_NO_WINDOW,
                env={**os.environ, "OLLAMA_HOST": HOST},
            )
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"

        assert self._proc.stdout is not None
        tail = ""
        for line in self._proc.stdout:
            if self._stopped:
                self.stop()
                return False, "已取消"
            line = line.strip()
            if line:
                tail = line
                progress(_pretty_pull_line(line))
        code = self._proc.wait()
        if code == 0:
            progress(f"模型 {model} 就绪")
            return True, "ok"
        return False, f"下载失败（退出码 {code}）{tail}"


def _pretty_pull_line(line: str) -> str:
    """把 ollama 的进度条压成一行好看的中文。"""
    for ch in ("\x1b",):
        line = line.replace(ch, "")
    low = line.lower()
    if "pulling manifest" in low:
        return "正在获取模型清单…"
    if "verifying sha256" in low:
        return "正在校验文件…"
    if "writing manifest" in low:
        return "正在写入清单…"
    if "success" in low:
        return "下载完成，正在收尾…"
    if "error" in low or "failed" in low:
        return f"出错：{line[:120]}"
    if "%" in line:
        return f"下载模型：{line[:100]}"
    return line[:100]
