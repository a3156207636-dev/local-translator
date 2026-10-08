"""把项目打成一个「解压即用」的便携分享包。

产物：
    dist/本地双语翻译/            <- 目录（可直接拷给别人）
    dist/本地双语翻译-便携版.zip   <- 上传网盘用

包里自带一份裁剪过的 Python 运行时（含 tkinter），对方电脑不需要装任何东西。
所有给用户看的 .bat / .txt 都用 GBK 编码写入，中文在 cmd 里才不会乱码。

用法：
    python tools/package_portable.py            # 全量重建
    python tools/package_portable.py --no-zip   # 不压缩，只出目录
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
DIST = PROJECT / "dist"
PKG_NAME = "本地双语翻译"
PKG_DIR = DIST / PKG_NAME
ZIP_PATH = DIST / f"{PKG_NAME}-便携版.zip"

# 源 Python（带 tkinter 的完整安装）
PY_CANDIDATES = [
    Path.home() / "AppData/Local/Programs/Python/Python314",
    Path(r"C:\Python314"),
    Path(r"C:\Program Files\Python314"),
]

PY_ROOT_FILES = [
    "python.exe", "pythonw.exe", "python3.dll", "python314.dll",
    "vcruntime140.dll", "vcruntime140_1.dll", "LICENSE.txt",
]

# Lib 下不打包的目录（按名字匹配，任意层级）
LIB_SKIP = {
    "test", "tests", "idlelib", "lib2to3", "turtledemo",
    "site-packages", "distutils",
}

# DLLs 下不打包的文件（测试用的扩展）
DLLS_SKIP_PREFIX = ("_test", "xxlimited")

APP_FILES = [
    # 主程序
    "main.py", "config.py", "engines.py", "overlay.py", "watcher.py",
    "context_reader.py", "settings_ui.py", "autostart.py", "restart.py",
    # 许可证 / 免费版配额
    "license.py",
    # 首次使用相关
    "setup_wizard.py", "hardware.py", "ollama_setup.py", "portable_setup.py",
    # 自检
    "verify.py",
    # 元信息
    "requirements.txt", "README.md",
]

DEP_PACKAGES = [
    "requests>=2.31.0",
    "pynput>=1.7.6",
    "uiautomation>=2.0.20",
    "pyperclip>=1.8.2",
    "pywin32>=306",
]


def log(msg: str) -> None:
    print(msg, flush=True)


def find_python() -> Path:
    for p in PY_CANDIDATES:
        if (p / "python.exe").is_file():
            return p
    raise SystemExit("找不到带 tkinter 的完整 Python 安装，请修改 PY_CANDIDATES")


# --------------------------------------------------------------------------- #
def copy_runtime(src: Path, dst: Path) -> None:
    """裁剪复制 Python 运行时。"""
    log(f"[1/6] 复制 Python 运行时  {src}  ->  {dst}")
    if dst.exists():
        bak = dst.parent / f"_runtime_旧_{int(time.time())}"
        dst.rename(bak)
        log(f"      旧 runtime 先改名为 {bak.name}")
    dst.mkdir(parents=True)

    for name in PY_ROOT_FILES:
        f = src / name
        if f.is_file():
            shutil.copy2(f, dst / name)
    (dst / "Lib").mkdir(exist_ok=True)
    (dst / "Lib" / "site-packages").mkdir(exist_ok=True)

    # DLLs
    dll_dst = dst / "DLLs"
    dll_dst.mkdir(exist_ok=True)
    for f in (src / "DLLs").iterdir():
        if f.is_file() and not f.name.startswith(DLLS_SKIP_PREFIX):
            shutil.copy2(f, dll_dst / f.name)

    # tcl / tk（tkinter 的界面资源，必须带上）
    if (src / "tcl").is_dir():
        shutil.copytree(
            src / "tcl", dst / "tcl",
            ignore=shutil.ignore_patterns("demos", "*.chm"),
        )

    # Lib（标准库）
    def ignore(_dir: str, names: list[str]) -> set[str]:
        return {n for n in names if n in LIB_SKIP}

    shutil.copytree(src / "Lib", dst / "Lib", ignore=ignore, dirs_exist_ok=True)
    log(f"      runtime 体积 {du(dst):.1f} MB")


def install_deps(runtime: Path) -> None:
    """用便携 runtime 自己装依赖，这样 pywin32 的 post-install 会正常执行。"""
    log("[2/6] 安装依赖（pip 会联网下载，第一次要几分钟）")
    py = runtime / "python.exe"

    r = subprocess.run([str(py), "-m", "ensurepip", "--upgrade"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        log("      ensurepip 输出：" + (r.stdout or "")[-400:] + (r.stderr or "")[-400:])

    cmd = [str(py), "-m", "pip", "install", "--no-cache-dir", "--disable-pip-version-check",
           *DEP_PACKAGES]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        log(r.stdout[-2000:])
        log(r.stderr[-2000:])
        raise SystemExit("依赖安装失败")
    tail = [ln for ln in (r.stdout or "").splitlines() if ln.strip()][-3:]
    for ln in tail:
        log("      " + ln)
    log(f"      runtime 体积 {du(runtime):.1f} MB")


def copy_app(dst: Path) -> None:
    log("[3/6] 复制程序代码")
    app = dst / "app"
    app.mkdir(parents=True, exist_ok=True)
    missing = []
    for name in APP_FILES:
        src = PROJECT / name
        if not src.is_file():
            missing.append(name)
            continue
        shutil.copy2(src, app / name)
    (app / "logs").mkdir(exist_ok=True)
    if missing:
        log("      警告：这些文件没找到 → " + ", ".join(missing))


def copy_icons(dst: Path) -> None:
    src = PROJECT / "assets"
    if src.is_dir():
        shutil.copytree(
            src, dst / "assets",
            ignore=shutil.ignore_patterns("*_preview.png", "png"),
            dirs_exist_ok=True,
        )


# --------------------------------------------------------------------------- #
BAT_MAIN = r"""@echo off
chcp 936 >nul
cd /d "%~dp0"

if not exist "runtime\python.exe" (
  echo.
  echo   运行时文件缺失，请重新解压完整的压缩包。
  echo.
  pause
  exit /b 1
)

if not exist "app\config.json" (
  echo.
  echo   第一次使用，先做一下配置...
  echo.
  "runtime\python.exe" "app\setup_wizard.py"
  exit /b 0
)

"runtime\python.exe" "app\main.py" --running >nul 2>&1
if %errorlevel%==0 (
  echo.
  echo   已经在运行了 —— 看屏幕右下角的小圆球。
  echo.
  ping -n 3 127.0.0.1 >nul
  exit /b 0
)

start "" "runtime\pythonw.exe" "app\main.py"
echo.
echo   已启动。看屏幕右下角：出现「译 · 待机」小圆球就是好了。
echo   然后在任意输入框里打几个中文字试试。
echo.
ping -n 4 127.0.0.1 >nul
exit /b 0
"""

BAT_SETUP = r"""@echo off
chcp 936 >nul
cd /d "%~dp0"
echo.
echo   首次使用配置向导
echo   ================================================
echo   会帮你选好翻译引擎、建桌面快捷方式。
echo   如果是本地 Ollama 方案，要下载安装包和模型，请耐心等。
echo.
"runtime\python.exe" "app\setup_wizard.py"
exit /b 0
"""

BAT_DEBUG = r"""@echo off
chcp 936 >nul
cd /d "%~dp0"
echo.
echo   调试模式：窗口里会打印全部日志和报错。
echo   关掉这个窗口 = 退出程序。
echo.
"runtime\python.exe" "app\main.py" %*
echo.
echo   程序已退出。
pause >nul
exit /b 0
"""

BAT_PROBE = r"""@echo off
chcp 936 >nul
cd /d "%~dp0"
echo.
echo   环境自检：看看翻译服务通不通
echo   ================================================
"runtime\python.exe" "app\main.py" --probe
echo.
pause >nul
exit /b 0
"""

BAT_LNK = r"""@echo off
chcp 936 >nul
cd /d "%~dp0"
"runtime\python.exe" "app\portable_setup.py" shortcuts
echo.
pause >nul
exit /b 0
"""

BAT_AUTOSTART_ON = r"""@echo off
chcp 936 >nul
cd /d "%~dp0"
"runtime\python.exe" "app\portable_setup.py" autostart-on
echo.
pause >nul
exit /b 0
"""

BAT_AUTOSTART_OFF = r"""@echo off
chcp 936 >nul
cd /d "%~dp0"
"runtime\python.exe" "app\portable_setup.py" autostart-off
echo.
pause >nul
exit /b 0
"""

TXT_GUIDE = """本地双语翻译 · 便携版
================================================

这是什么
    打中文的时候，屏幕上自动弹出一个悬浮窗，把这句话翻成英文给你看。
    翻译全部在本机完成（或者用你自己填的 API），代码开源、不联网上报。

怎么用（三步）
    1. 把整个文件夹解压到任意位置，别放在 C:\\Program Files 里（那里不让写文件）
    2. 双击「启动.bat」
       · 第一次会弹出配置向导，跟着走：选本地 Ollama 或填在线 API
       · 之后每次双击就是直接启动
    3. 看屏幕右下角出现「译 · 待机」的小圆球，就成功了。
       随便找个输入框打几个中文字，悬浮窗会自己弹出来。

几个常用文件
    启动.bat .............. 平时就双击这个
    首次使用.bat .......... 想重新配置引擎（换模型、换 API）时点这个
    调试启动.bat .......... 出问题时用它，窗口里能看到报错
    检查环境.bat .......... 看看翻译服务通不通
    创建桌面快捷方式.bat ... 在桌面放一个图标，不用每次翻文件夹
    开启开机自启.bat ...... 开机自动待命
    app\\logs\\app.log ..... 运行日志
    README.md ............. 更详细的说明（模型选择、参数调节、常见问题）

常见问题
    Q: 双击没反应？
    A: 看屏幕右下角有没有小圆球 —— 有就是在运行（程序在托盘待命）。
       没有的话用「调试启动.bat」看报错。

    Q: 悬浮窗不出来？
    A: 只有检测到中文才会翻译。先打几个汉字试试。

    Q: 第一次翻译很慢？
    A: 本地模型要加载进显存，第一句可能等几秒，之后就快了。

    Q: 换了台电脑 / 挪了文件夹，快捷方式失效？
    A: 删掉旧快捷方式，双击「创建桌面快捷方式.bat」重来一次。

    Q: 想彻底删掉？
    A: 关掉托盘小圆球（右键 → 退出），双击「关闭开机自启.bat」，
       删掉桌面快捷方式，再删掉这个文件夹就行。程序不写注册表。

隐私
    本地模式：模型跑在你自己电脑上，文字不经过任何网络。
    在线模式：文字会发给你自己填的那家服务商，请自行评估。
"""


def write_scripts(dst: Path) -> None:
    log("[4/6] 生成启动脚本与说明")
    files = {
        "启动.bat": BAT_MAIN,
        "首次使用.bat": BAT_SETUP,
        "调试启动.bat": BAT_DEBUG,
        "检查环境.bat": BAT_PROBE,
        "创建桌面快捷方式.bat": BAT_LNK,
        "开启开机自启.bat": BAT_AUTOSTART_ON,
        "关闭开机自启.bat": BAT_AUTOSTART_OFF,
        "使用说明.txt": TXT_GUIDE,
    }
    for name, text in files.items():
        (dst / name).write_bytes(text.replace("\n", "\r\n").encode("gbk", "replace"))


def make_zip(src: Path, out: Path) -> None:
    log("[6/6] 压缩")
    if out.exists():
        out.unlink()
    out.parent.mkdir(parents=True, exist_ok=True)

    # 预先算好 deflate 压缩级别：文本用 9，已经是压缩格式的直接存储
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in sorted(src.rglob("*")):
            if f.is_dir():
                continue
            rel = Path(src.name) / f.relative_to(src)
            z.write(f, rel.as_posix())
    log(f"      {out}  ({out.stat().st_size / 1048576:.1f} MB)")


# --------------------------------------------------------------------------- #
def du(path: Path) -> float:
    total = 0
    for f in path.rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            pass
    return total / 1048576


def runtime_ready(runtime: Path) -> bool:
    """runtime 是否已经是装好依赖的状态。"""
    sp = runtime / "Lib" / "site-packages"
    return (
        (runtime / "python.exe").is_file()
        and (runtime / "DLLs" / "_tkinter.pyd").is_file()
        and (runtime / "tcl").is_dir()
        and (sp / "requests").is_dir()
        and (sp / "win32com").is_dir()
        and (sp / "pynput").is_dir()
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-zip", action="store_true", help="只生成目录，不压缩")
    ap.add_argument("--fresh", action="store_true",
                    help="旧版本改名为备份后全量重建（默认就地覆盖，不删任何东西）")
    ap.add_argument("--rebuild-runtime", action="store_true", help="强制重做 Python 运行时")
    args = ap.parse_args()

    py_src = find_python()
    log(f"源 Python：{py_src}")

    DIST.mkdir(parents=True, exist_ok=True)
    if PKG_DIR.exists() and args.fresh:
        bak = DIST / f"_{PKG_NAME}_旧版本"
        if bak.exists():
            shutil.rmtree(bak, ignore_errors=True)
        PKG_DIR.rename(bak)
        log(f"旧版本已改名为 {bak.name}（确认没问题后手动删掉即可）")
    PKG_DIR.mkdir(parents=True, exist_ok=True)

    runtime = PKG_DIR / "runtime"
    if args.rebuild_runtime or args.fresh or not runtime_ready(runtime):
        copy_runtime(py_src, runtime)
        install_deps(runtime)
    else:
        log(f"[1-2/6] runtime 已就绪，跳过（体积 {du(runtime):.1f} MB，"
            f"要重建加 --rebuild-runtime）")

    copy_app(PKG_DIR)
    copy_icons(PKG_DIR)
    write_scripts(PKG_DIR)

    log("[5/6] 自检：便携运行时能不能正常起来")
    check = (
        "import tkinter,sys;"
        "import requests,pynput,uiautomation,pyperclip,win32com.client;"
        "r=tkinter.Tk();r.withdraw();r.update();r.destroy();"   # 真建一次窗口，确认 tcl 资源找得到
        "print('OK', sys.version.split()[0], 'tk', tkinter.TkVersion, '窗口创建成功')"
    )
    r = subprocess.run(
        [str(runtime / "python.exe"), "-c", check],
        capture_output=True, text=True, cwd=str(PKG_DIR),
    )
    print("      " + (r.stdout or r.stderr).strip()[:300])
    if r.returncode != 0:
        raise SystemExit("自检失败，便携运行时有问题")

    log(f"      包体体积 {du(PKG_DIR):.1f} MB")
    if not args.no_zip:
        make_zip(PKG_DIR, ZIP_PATH)
    log("完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
