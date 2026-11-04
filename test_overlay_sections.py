"""回归测试：复制键在开关切换、流式长译文、超长状态文字下都要可见；emoji 文本能编码。"""
import copy
import sys

import config as cfgmod
from context_reader import fix_surrogates
from overlay import FloatingUI


def visible_in_window(ui, widget) -> bool:
    ui.root.update()
    bottom = widget.winfo_rooty() + widget.winfo_height()
    return bool(widget.winfo_ismapped()) and bottom <= ui.win.winfo_rooty() + ui.win.winfo_height()


def main() -> int:
    cfg = copy.deepcopy(cfgmod.DEFAULTS)
    cfg["overlay"].update(show_source=False, show_footer=False)
    ui = FloatingUI(cfg, lambda: None, lambda: None, lambda: False, lambda: None)
    failures = []

    def check(name, widget, want):
        got = visible_in_window(ui, widget)
        print(f"{'ok ' if got == want else 'FAIL'} {name}: visible={got}")
        if got != want:
            failures.append(name)

    def run():
        ui.show_translation("原文", dst="Translation.", engine="mock", elapsed_ms=1)
        check("关闭启动：原文隐藏", ui.src_label, False)
        check("关闭启动：复制键隐藏", ui.btn_copy, False)

        cfg["overlay"].update(show_source=True, show_footer=True)
        ui.apply_live(cfg)
        check("打开后：原文显示", ui.src_label, True)
        check("打开后：复制键显示", ui.btn_copy, True)
        order = [w.winfo_y() for w in (ui.hdr, ui.src_label, ui.dst_label, ui.footer)]
        if order != sorted(order):
            print(f"FAIL 版面顺序：{order}")
            failures.append("order")

        cfg["overlay"].update(show_source=False, show_footer=False)
        ui.apply_live(cfg)
        check("再次关闭：原文隐藏", ui.src_label, False)
        check("再次关闭：复制键隐藏", ui.btn_copy, False)

        cfg["overlay"].update(show_source=True, show_footer=True)
        ui.apply_live(cfg)
        ui.show_translation("原文", pending=True)
        height = ui.win.winfo_height()
        ui.update_dst("原文", "A long streaming translation. " * 40)
        check("流式长译文：复制键显示", ui.btn_copy, True)
        ui.root.update()
        if ui.win.winfo_height() != height:
            print("FAIL 流式途中窗口高度不应变化")
            failures.append("stream height")

        ui.show_translation("原文", dst="Short.", engine="x" * 300, elapsed_ms=1)
        check("超长状态文字：复制键显示", ui.btn_copy, True)

        ui.show_translation("原文" * 300, dst="A very long translation. " * 400, engine="x", elapsed_ms=1)
        check("超长译文：复制键显示", ui.btn_copy, True)
        ui.root.update()
        bottom = ui.btn_copy.winfo_rooty() + ui.btn_copy.winfo_height()
        if bottom > ui.win.winfo_screenheight():
            print(f"FAIL 超长译文：复制键在屏幕外（{bottom}）")
            failures.append("offscreen")
        ui.post(("quit",))

    ui.root.after(200, run)
    ui.run()

    for raw, want in (("a🎉b", "a\U0001F389b"), ("a\ud83cb", "ab"), ("修复", "修复")):
        got = fix_surrogates(raw)
        got.encode("utf-8")
        if got != want:
            print(f"FAIL fix_surrogates({raw!r}) = {got!r}")
            failures.append("surrogates")

    print("FAILED" if failures else "PASSED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
