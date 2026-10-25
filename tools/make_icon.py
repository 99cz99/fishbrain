# tools/make_icon.py — 生成像素大脑图标（一组配色变体）
# 16x16 像素脑，最近邻放大到 512x512。纯 stdlib（zlib+struct 写最小 PNG）。
import os
import struct
import zlib

# 像素大脑：# = 脑填充，- = 沟回褶皱（更深色），. = 背景
# 左右两半球 + 中间纵向深沟 + 横向波浪沟回
BRAIN = [
    "....................",
    "......########......",
    "....##--------##....",
    "..#--------------#..",
    ".#----------------#.",
    ".#--##------##----#.",
    ".#--#--------#----#.",
    ".#--#---##---#----#.",
    ".#--#--------#----#.",
    ".#--#---##---#----#.",
    ".#--#--------#----#.",
    ".#--#---##---#----#.",
    ".#--#--------#----#.",
    ".#--#---##---#----#.",
    ".#--##------##----#.",
    ".#----------------#.",
    "..#--------------#..",
    "....##--------##....",
    "......########......",
]

# 6 组配色：(名字, 背景, 脑填充, 褶皱)
PALETTES = [
    ("teal",   (15, 23, 42),   (45, 212, 191),  (13, 148, 136)),
    ("blue",   (15, 23, 42),   (96, 165, 250),  (37, 99, 235)),
    ("pink",   (30, 27, 75),   (244, 114, 182), (219, 39, 119)),
    ("purple", (30, 27, 75),   (167, 139, 250), (124, 58, 237)),
    ("green",  (5, 46, 22),    (74, 222, 128),  (22, 163, 74)),
    ("mono",   (31, 41, 55),   (229, 231, 235), (156, 163, 175)),
]

# 归一化：补齐每行到最大宽度，确保网格是方阵
_w = max(len(r) for r in BRAIN)
BRAIN = [r + "." * (_w - len(r)) for r in BRAIN]
BASE = len(BRAIN)  # 高（应与 _w 相等）


def _png_chunk(tag, data):
    c = struct.pack(">I", len(data)) + tag + data
    c += struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return c


def write_png(path, width, height, rgba_rows):
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)  # 8bit RGBA
    raw = b"".join(b"\x00" + row for row in rgba_rows)  # filter type 0
    idat = zlib.compress(raw, 9)
    data = sig + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", idat) + _png_chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(data)


def render(palette, scale=32):
    name, bg, fill, fold = palette
    w = BASE * scale
    rows = []
    for y in range(w):
        row = bytearray()
        for x in range(w):
            ch = BRAIN[y // scale][x // scale]
            if ch == "#":
                rgba = fill + (255,)
            elif ch == "-":
                rgba = fold + (255,)
            else:
                rgba = bg + (255,)
            row += bytes(rgba)
        rows.append(bytes(row))
    return name, w, rows


def main():
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "icons")
    os.makedirs(out_dir, exist_ok=True)
    for p in PALETTES:
        name, w, rows = render(p)
        path = os.path.join(out_dir, f"brain_{name}.png")
        write_png(path, w, w, rows)
        print(f"OK {path} ({w}x{w})")


if __name__ == "__main__":
    main()
