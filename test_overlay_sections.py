"""回归测试：复制键在开关切换、流式长译文、超长状态文字下都要可见；emoji 文本能编码。"""
import copy
import sys
from types import SimpleNamespace

import config as cfgmod
from context_reader import fix_surrogates
from overlay import FloatingUI, _work_area


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

        # 固定：翻译不移动窗口；放不下时滚动；拖动后按新空间展开
        def expect(name, ok, detail=""):
            print(f"{'ok ' if ok else 'FAIL'} {name} {detail}")
            if not ok:
                failures.append(name)

        long_dst = "A very long translation. " * 200
        left, top, right, bottom = _work_area(100, 100)
        ui.show_translation("原文", dst="First.", engine="x", elapsed_ms=1, caret=(100, 100, 110, 120))
        ui._toggle_pin()
        ui.root.update()
        pinned_at = (ui.win.winfo_x(), ui.win.winfo_y())
        ui.show_translation("原文", dst="Second.", engine="x", elapsed_ms=1, caret=(900, 500, 910, 520))
        ui.root.update()
        expect("固定后翻译：位置不变", (ui.win.winfo_x(), ui.win.winfo_y()) == pinned_at)

        grab = SimpleNamespace(x_root=ui.win.winfo_x() + 5, y_root=ui.win.winfo_y() + 5)
        ui._drag_start(grab)
        low_y = bottom - 300
        ui._drag_move(SimpleNamespace(x_root=grab.x_root, y_root=low_y + 5))
        ui.show_translation("原文", dst=long_dst, engine="x", elapsed_ms=1, caret=(900, 500, 910, 520))
        ui.root.update()
        win_bottom = ui.win.winfo_y() + ui.win.winfo_height()
        expect("固定在下方：位置不变", ui.win.winfo_y() == low_y, f"y={ui.win.winfo_y()} 要 {low_y}")
        expect("固定在下方：不超出屏幕", win_bottom <= bottom, f"{win_bottom} <= {bottom}")
        check("固定在下方：复制键显示", ui.btn_copy, True)
        short_h = ui.win.winfo_height()

        before = ui.viewport.yview()[0]
        ui._on_wheel(SimpleNamespace(delta=-120))
        ui.root.update()
        expect("滚轮向下：内容滚动", ui.viewport.yview()[0] > before)

        grab = SimpleNamespace(x_root=ui.win.winfo_x() + 5, y_root=ui.win.winfo_y() + 5)
        ui._drag_start(grab)
        ui._drag_move(SimpleNamespace(x_root=grab.x_root, y_root=top + 13))
        ui.root.update()
        expect("拖到上方：窗口展开", ui.win.winfo_height() > short_h, f"{short_h} -> {ui.win.winfo_height()}")

        cfg["overlay"]["max_height"] = 200
        ui.apply_live(cfg)
        ui.root.update()
        expect("最长高度：不超过设定", ui.win.winfo_height() <= round(200 * ui.scale), str(ui.win.winfo_height()))
        cfg["overlay"]["max_height"] = 0

        ui.root.update()
        wide = ui.win.winfo_width() + 100
        cfg["overlay"]["width"] = round(wide / ui.scale)
        ui.apply_live(cfg)
        ui.root.update()
        expect("固定时改宽度：立即生效", abs(ui.win.winfo_width() - wide) <= 1, f"{ui.win.winfo_width()} 要 {wide}")
        ui._toggle_pin()
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
