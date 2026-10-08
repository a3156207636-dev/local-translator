"""验证分享包：解压到临时目录，在「新位置」跑一遍完整自检。

模拟对方拿到 zip 之后的真实流程：
    解压 -> 双击 启动.bat -> 程序跑起来

用法：
    python tools/verify_portable.py            # 解压到临时目录并检查
    python tools/verify_portable.py --dir X    # 指定解压目录
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
ZIP_PATH = PROJECT / "dist" / "本地双语翻译-便携版.zip"
DIST = PROJECT / "dist"

results: list[tuple[bool, str]] = []


def check(ok: bool, msg: str) -> bool:
    results.append((ok, msg))
    print(("  [OK]   " if ok else "  [FAIL] ") + msg, flush=True)
    return ok


def run_capture(cmd: list[str], cwd: str | None = None, timeout: float = 90):
    """跑子进程并按需解码（有些脚本故意用 GBK 输出给 cmd 看）。"""
    r = subprocess.run(cmd, capture_output=True, timeout=timeout, cwd=cwd)

    def dec(b: bytes | None) -> str:
        if not b:
            return ""
        for enc in ("utf-8", "gbk"):
            try:
                return b.decode(enc)
            except Exception:
                continue
        return b.decode("gbk", "replace")

    return r.returncode, dec(r.stdout), dec(r.stderr)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="")
    ap.add_argument("--e2e", action="store_true",
                    help="额外做一次真实启动（会拉起悬浮窗进程，跑完自动关掉）")
    args = ap.parse_args()

    if not ZIP_PATH.is_file():
        print("找不到 zip：", ZIP_PATH)
        return 1

    target = Path(args.dir).resolve() if args.dir else (
        DIST / ("_验证用解压_" + __import__("datetime").datetime.now().strftime("%H%M%S"))
    )
    target.mkdir(parents=True, exist_ok=True)

    print(f"1) 解压 {ZIP_PATH.name} -> {target}")
    with zipfile.ZipFile(ZIP_PATH) as z:
        names = z.namelist()
        tops = {n.split("/")[0] for n in names}
        check(len(tops) == 1, f"zip 只有一个顶层目录：{sorted(tops)}")
        check(not any(n.endswith(".pyc") and "../" in n for n in names),
              "没有路径穿越条目")
        z.extractall(target)

    root = target / sorted(tops)[0]
    print(f"2) 检查文件结构  {root}")

    must = [
        "runtime/python.exe",
        "runtime/pythonw.exe",
        "runtime/python314.dll",
        "runtime/DLLs/_tkinter.pyd",
        "runtime/DLLs/tcl90.dll",
        "runtime/tcl",
        "runtime/Lib/tkinter/__init__.py",
        "runtime/Lib/site-packages/requests",
        "runtime/Lib/site-packages/pynput",
        "runtime/Lib/site-packages/uiautomation",
        "runtime/Lib/site-packages/win32com",
        "app/main.py",
        "app/setup_wizard.py",
        "app/config.py",
        "app/engines.py",
        "app/overlay.py",
        "app/portable_setup.py",
        "assets/main.ico",
        "启动.bat",
        "首次使用.bat",
        "调试启动.bat",
        "使用说明.txt",
    ]
    for rel in must:
        check((root / rel).exists(), f"存在 {rel}")

    check(not (root / "app" / "config.json").exists(),
          "app/config.json 不存在（首次启动会走配置向导）")
    main_src = (root / "app" / "main.py").read_text(encoding="utf-8")
    check(".venv" not in main_src, "main.py 里没有硬编码 .venv 路径")

    print("3) bat 编码（GBK）")
    for name in ("启动.bat", "首次使用.bat", "调试启动.bat", "使用说明.txt"):
        p = root / name
        try:
            txt = p.read_bytes().decode("gbk")
            check("runtime" in txt or "便携" in txt, f"{name} 用 GBK 解码正常")
        except Exception as e:
            check(False, f"{name} GBK 解码失败：{e}")

    py = root / "runtime" / "python.exe"
    env_note = "（在解压后的新路径下）"

    print(f"4) 便携运行时自检 {env_note}")
    r = subprocess.run(
        [str(py), "-c",
         "import sys,tkinter,requests,pynput,uiautomation,pyperclip,win32com.client;"
         "w=tkinter.Tk();w.withdraw();w.update();w.destroy();"
         "print(sys.prefix);print('tk',tkinter.TkVersion)"],
        capture_output=True, text=True, timeout=120,
    )
    check(r.returncode == 0 and "tk" in r.stdout,
          f"tkinter 建窗 + 依赖导入成功 {r.stdout.strip().splitlines()[:2]}")

    print("5) 程序本体能不能跑起来")
    code, out, err = run_capture([str(py), str(root / "app" / "main.py"), "--running"],
                                 cwd=str(root / "app"))
    check(code == 0, f"main.py --running 退出码 {code}；输出 {out.strip()[:80]}")

    code, out, err = run_capture([str(py), str(root / "app" / "portable_setup.py"), "status"],
                                 cwd=str(root / "app"))
    check("运行时" in out, f"portable_setup.py status 正常（{out.strip().splitlines()[:1]}）")

    print("6) 配置向导能不能正常出界面（建完就关，不进主循环）")
    wiz = (
        "import sys;sys.path.insert(0,'.');"
        "import setup_wizard as sw;"
        "w=sw.Wizard();w.withdraw();w.update();w.destroy();"
        "print('wizard-ok')"
    )
    code, out, err = run_capture([str(py), "-c", wiz], cwd=str(root / "app"), timeout=120)
    check(code == 0 and "wizard-ok" in out,
          f"向导界面构建成功（{out.strip()[:100] or err.strip()[-160:]}）")

    print("7) Ollama 相关逻辑")
    oll = (
        "import sys;sys.path.insert(0,'.');"
        "import ollama_setup as o;"
        "print('installed=',o.is_installed(),'alive=',o.server_alive(),"
        "'cli=',(o.find_cli() or 'none'))"
    )
    code, out, err = run_capture([str(py), "-c", oll], cwd=str(root / "app"), timeout=90)
    check(code == 0 and "installed=" in out, f"Ollama 探测正常（{out.strip()[:110]}）")

    if args.e2e:
        print("8) 真实启动一次（屏幕上会冒出小圆球，跑完自动关掉）")
        pyc = root / "runtime" / "python.exe"
        proc = subprocess.Popen(
            [str(pyc), str(root / "app" / "main.py")],
            cwd=str(root / "app"),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )
        time.sleep(12)
        alive = proc.poll() is None
        if alive:
            check(True, "启动 12 秒后进程仍然活着（没有启动即崩）")
            try:
                proc.terminate()
                proc.wait(timeout=10)
                check(True, "已关掉测试进程")
            except Exception as e:
                check(False, f"关进程失败：{e}")
        else:
            blob = proc.stdout.read() if proc.stdout else b""
            txt = ""
            for enc in ("utf-8", "gbk"):
                try:
                    txt = blob.decode(enc)
                    break
                except Exception:
                    continue
            else:
                txt = blob.decode("gbk", "replace")
            if "已经有一个实例在运行" in txt:
                check(True, "本机已有实例在跑，新实例被单实例保护挡下（符合预期）")
                print("      注：要完整验证启动链路，先关掉本机正在运行的那个实例再跑一次")
            else:
                check(False, f"进程提前退出，输出：{txt.strip()[:200]}")

        log = root / "app" / "logs" / "app.log"
        if log.is_file() and log.stat().st_size:
            tail = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-6:]
            print("      app.log 末尾：")
            for ln in tail:
                print("        " + ln[:130])

    ok = sum(1 for f, _ in results if f)
    bad = [m for f, m in results if not f]
    print(f"\n通过 {ok}/{len(results)}")
    if bad:
        print("失败项：")
        for m in bad:
            print("  -", m)
    print(f"\n解压目录（验证完可手动删除）：{root}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
