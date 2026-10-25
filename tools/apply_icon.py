# tools/apply_icon.py — 用粉色像素脑覆盖各 mipmap 的 ic_launcher.png
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_icon import BRAIN, PALETTES, write_png

PINK = next(p for p in PALETTES if p[0] == "pink")
BASE = len(BRAIN)

SIZES = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}
RES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "android", "app", "src", "main", "res")


def render_size(size):
    _, bg, fill, fold = PINK
    scale = max(1, size // BASE)
    brain_w = BASE * scale
    off = (size - brain_w) // 2
    rows = []
    for y in range(size):
        row = bytearray()
        for x in range(size):
            px, py = x - off, y - off
            if 0 <= px < brain_w and 0 <= py < brain_w:
                ch = BRAIN[py // scale][px // scale]
                if ch == "#":
                    rgba = fill + (255,)
                elif ch == "-":
                    rgba = fold + (255,)
                else:
                    rgba = bg + (255,)
            else:
                rgba = bg + (255,)
            row += bytes(rgba)
        rows.append(bytes(row))
    return rows


for dpi, size in SIZES.items():
    path = os.path.join(RES, f"mipmap-{dpi}", "ic_launcher.png")
    write_png(path, size, size, render_size(size))
    print(f"OK {path} ({size}x{size})")
