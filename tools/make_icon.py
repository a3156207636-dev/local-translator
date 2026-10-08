"""生成桌面快捷方式用的图标（.ico）。

设计：圆角矩形 + 对角渐变 + 居中白色字形（「译」/「?」）。
每个尺寸都先按 4 倍超采样渲染再缩回去，保证边缘和字形干净。

用法：
    python tools/make_icon.py
产物：assets/*.ico
"""
from __future__ import annotations

import struct
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

APP_DIR = Path(__file__).resolve().parent.parent
ASSETS = APP_DIR / "assets"

SIZES = (256, 128, 64, 48, 32, 16)
SUPERSAMPLE = 4          # 4x 超采样
RADIUS_RATIO = 0.225     # 圆角半径 / 边长
GLYPH_RATIO = 0.66       # 字号 / 边长

FONT_BOLD = r"C:\Windows\Fonts\msyhbd.ttc"   # 微软雅黑 Bold
FONT_FALLBACK = r"C:\Windows\Fonts\msyh.ttc"

# 名称 -> (字形, 起始色, 结束色)
THEMES = {
    "main":     ("\u8bd1", (0x4F, 0x8B, 0xF7), (0x6D, 0x28, 0xD9)),   # 译 · 蓝紫
    "restart":  ("\u8bd1", (0x10, 0xB9, 0x81), (0x0E, 0x74, 0x90)),   # 译 · 青绿
    "guide":    ("?",      (0x64, 0x74, 0x8B), (0x33, 0x41, 0x55)),   # ? · 石板灰
}

GRAD_BASE = 512          # 渐变底图分辨率（够用，放大后依旧平滑）


def diagonal_gradient(w: int, h: int, c1, c2) -> Image.Image:
    """左上 -> 右下 的线性渐变。"""
    img = Image.new("RGB", (w, h))
    px = img.load()
    span = float(w + h - 2)
    for y in range(h):
        for x in range(w):
            t = (x + y) / span
            px[x, y] = (
                round(c1[0] + (c2[0] - c1[0]) * t),
                round(c1[1] + (c2[1] - c1[1]) * t),
                round(c1[2] + (c2[2] - c1[2]) * t),
            )
    return img


def pick_font(size: int) -> ImageFont.FreeTypeFont:
    for path in (FONT_BOLD, FONT_FALLBACK):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def render(size: int, glyph: str, c1, c2, grad_cache: Image.Image) -> Image.Image:
    s = size * SUPERSAMPLE

    # 渐变底 + 圆角遮罩
    grad = grad_cache.resize((s, s), Image.LANCZOS)
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, s - 1, s - 1), radius=int(s * RADIUS_RATIO), fill=255
    )
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    img.paste(grad, (0, 0), mask)

    # 内侧高光描边，让小图标更有立体感
    gloss = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(gloss).rounded_rectangle(
        (int(s * 0.012), int(s * 0.012), s - 1 - int(s * 0.012), s - 1 - int(s * 0.012)),
        radius=int(s * RADIUS_RATIO),
        outline=(255, 255, 255, 46),
        width=max(1, int(s * 0.016)),
    )
    img = Image.alpha_composite(img, gloss)

    # 居中字形（按墨迹包围盒精确居中）
    font = pick_font(int(s * GLYPH_RATIO))
    draw = ImageDraw.Draw(img)
    box = draw.textbbox((0, 0), glyph, font=font)
    gw, gh = box[2] - box[0], box[3] - box[1]
    x = (s - gw) / 2 - box[0]
    y = (s - gh) / 2 - box[1]

    shadow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).text(
        (x, y + s * 0.012), glyph, font=font, fill=(0, 0, 0, 60)
    )
    img = Image.alpha_composite(img, shadow)

    draw = ImageDraw.Draw(img)
    draw.text((x, y), glyph, font=font, fill=(255, 255, 255, 255))

    return img.resize((size, size), Image.LANCZOS)


def pack_ico(frames: list[Image.Image], out: Path) -> None:
    """把多张 PNG 直接封进 ICO 容器（Vista+ 支持 PNG 压缩的图标帧）。"""
    import io

    blobs: list[bytes] = []
    for f in frames:
        buf = io.BytesIO()
        f.save(buf, format="PNG", optimize=True)
        blobs.append(buf.getvalue())

    header = struct.pack("<HHH", 0, 1, len(frames))
    offset = len(header) + 16 * len(frames)
    entries, data = b"", b""
    for f, blob in zip(frames, blobs):
        w = 0 if f.width >= 256 else f.width
        h = 0 if f.height >= 256 else f.height
        entries += struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, len(blob), offset)
        data += blob
        offset += len(blob)

    out.write_bytes(header + entries + data)


def main() -> int:
    ASSETS.mkdir(parents=True, exist_ok=True)
    grad_cache = diagonal_gradient(GRAD_BASE, GRAD_BASE, (0, 0, 0), (255, 255, 255))

    made = []
    for name, (glyph, c1, c2) in THEMES.items():
        grad = diagonal_gradient(GRAD_BASE, GRAD_BASE, c1, c2)
        frames = [render(s, glyph, c1, c2, grad) for s in SIZES]
        out = ASSETS / f"{name}.ico"
        pack_ico(frames, out)
        frames[0].save(ASSETS / f"{name}_preview.png")
        made.append((out.name, out.stat().st_size, [f.size[0] for f in frames]))

    del grad_cache
    for name, size, sizes in made:
        print(f"  {name:14s} {size:>7,d} B   {sizes}")
    print(f"\n输出目录：{ASSETS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
