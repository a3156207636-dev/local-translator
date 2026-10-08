"""悬浮窗自检：不连模型，只把界面与线程模型跑一遍，5 秒后自动退出。"""
import sys
import traceback

import config as cfgmod
from overlay import FloatingUI


def main() -> int:
    cfg = cfgmod.load()
    cfg["overlay"]["auto_hide_ms"] = 0
    cfg["app"]["show_pill"] = True

    try:
        ui = FloatingUI(
            cfg,
            on_settings=lambda: print("[cb] settings"),
            on_quit=lambda: print("[cb] quit"),
            on_toggle_pause=lambda: (print("[cb] pause"), False)[1],
            on_probe=lambda: print("[cb] probe"),
        )
    except Exception:
        traceback.print_exc()
        return 1

    print("悬浮窗已创建；2 秒后展示内容，5 秒后退出")

    def step1():
        ui.show_translation("我今天写了代码，然后测试了。这个功能很好用", pending=True, caret=None)
        print("已展示 pending 状态")

    def step2():
        ui.show_translation(
            "这个功能很好用",
            dst="This feature is really handy.",
            engine="openai · mock-model",
            elapsed_ms=418,
            caret=None,
        )
        print("已展示翻译结果")

    def step3():
        ui.show_translation("", error="连接 127.0.0.1:11434 失败", engine="openai", caret=None)
        print("已展示错误状态")

    def step4():
        ok = ui.apply_live(cfg)
        print("apply_live 通过")
        ui.hide_overlay()
        ui.post(("quit",))

    ui.root.after(400, step1)
    ui.root.after(1300, step2)
    ui.root.after(3000, step3)
    ui.root.after(4500, step4)
    ui.run()
    print("界面已正常退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
