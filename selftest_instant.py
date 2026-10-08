"""验证「瞬间弹出」与「永不自动消失」这两条新行为。

不连模型、不连键盘，只把悬浮窗的状态机跑一遍。
"""
import sys
import traceback

import config as cfgmod
from overlay import FloatingUI

FAILED: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  ' + extra) if extra else ''}")
    if not cond:
        FAILED.append(name)


def visible(ui: FloatingUI) -> bool:
    try:
        return ui.win.state() == "normal"
    except Exception:
        return False


def main() -> int:
    cfg = cfgmod.load()
    print(f"配置：auto_hide_ms={cfg['overlay']['auto_hide_ms']} "
          f"hide_on_typing={cfg['overlay']['hide_on_typing']} "
          f"instant_popup={cfg['trigger']['instant_popup']} "
          f"debounce={cfg['trigger']['debounce_ms']}ms "
          f"streaming={cfg['overlay'].get('streaming')}")

    ui = FloatingUI(
        cfg,
        on_settings=lambda: None,
        on_quit=lambda: None,
        on_toggle_pause=lambda: False,
        on_probe=lambda: None,
    )

    steps = []

    # --- 1. 还没翻译过任何东西时，按下第一个字就弹窗 -------------------- #
    def s1() -> None:
        check("初始状态：窗口隐藏", not visible(ui))
        ui.popup()
        ui.root.update_idletasks()
        check("instant popup：打字即弹出", visible(ui))
        check("instant popup 占位文案", "正在翻译" in ui.dst_label.cget("text"),
              repr(ui.dst_label.cget("text")))

    # --- 2. 流式译文逐字刷新 ------------------------------------------- #
    def s2() -> None:
        ui.show_translation("我刚刚提交了一个 bug 修复", pending=True)
        check("pending：显示原文", ui.src_label.cget("text") == "我刚刚提交了一个 bug 修复")
        check("pending：显示翻译中", ui.dst_label.cget("text") == "翻译中…")
        for i, chunk in enumerate(["I ", "just ", "submitted ", "a ", "bug ", "fix"]):
            ui.update_dst("我刚刚提交了一个 bug 修复", chunk * 1 if i == 0 else
                          "I just submitted a bug fix"[: 8 * (i + 1)])
        ui.root.update_idletasks()
        check("流式更新：译文已写入",
              ui.dst_label.cget("text").startswith("I just submitted"),
              repr(ui.dst_label.cget("text")))

    # --- 3. 窗口可见时再 popup 不应该清掉已有内容 ---------------------- #
    def s3() -> None:
        before = ui.dst_label.cget("text")
        ui.popup()
        ui.root.update_idletasks()
        check("已可见时 popup 不清空内容", ui.dst_label.cget("text") == before)

    # --- 4. 3.5 秒后仍未自动隐藏（auto_hide_ms=0） ---------------------- #
    def s4() -> None:
        check("auto_hide_ms=0：一直不自动消失", visible(ui))

    # --- 5. 用户手动关掉后，打字不再自动弹出来 -------------------------- #
    def s5() -> None:
        ui.hide_overlay(user=True)
        ui.root.update_idletasks()
        check("手动关闭后窗口隐藏", not visible(ui))
        ui.popup()
        ui.root.update_idletasks()
        check("手动关闭后 popup 不打扰", not visible(ui))

    # --- 6. 下一次真实翻译会重新显示，并解除「用户已关闭」 --------------- #
    def s6() -> None:
        ui.show_translation(
            "这个方案在离线环境下也能稳定运行",
            dst="This approach runs stably offline.",
            engine="openai · qwen2.5:7b",
            elapsed_ms=180, first_ms=60,
        )
        ui.root.update_idletasks()
        check("新译文会重新显示窗口", visible(ui))
        check("用户关闭标记已重置", ui._user_closed is False)
        meta = ui.meta.cget("text")
        check("底部显示首字延迟", "首字" in meta, repr(meta))

    def finish() -> None:
        ui.post(("quit",))

    steps = [(300, s1), (900, s2), (1400, s3), (3500, s4), (4100, s5), (4700, s6), (5400, finish)]
    for ms, fn in steps:
        ui.root.after(ms, fn)

    ui.run()

    print()
    if FAILED:
        print(f"不通过 {len(FAILED)} 项：{FAILED}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
