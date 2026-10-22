"""把 firmware/epd-wiring-test/src/main.cpp 里的测试页渲染成 PNG，用于和实屏对照。

设计要点：版面数字（边框、色块、棋盘格位置等）和 5x7 字库**直接从 main.cpp 解析**，
不在本脚本里重复写一遍 —— 改了固件，预览会跟着变，不会悄悄漂移。

用法：
    python tools/preview_wiring_test.py            # 生成 docs/wiring-test-preview.png
    python tools/preview_wiring_test.py --scale 2  # 放大两倍，便于在屏幕上细看
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "firmware" / "epd-wiring-test" / "src" / "main.cpp"
OUT = ROOT / "docs" / "wiring-test-preview.png"

BLACK, WHITE, YELLOW, RED = 0, 1, 2, 3
# 四色墨水屏的实际观感取近似值；不是精确色度
PALETTE = {
    BLACK: (16, 16, 16),
    WHITE: (247, 247, 244),
    YELLOW: (226, 184, 30),
    RED: (178, 24, 24),
}


def load_defines(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    # 允许行尾注释：#define IMG_W   768   /* 可见区宽 */
    pattern = r"^#define\s+([A-Z][A-Z0-9_]*)\s+(\d+)\s*(?:/\*.*?\*/)?\s*$"
    for name, value in re.findall(pattern, text, re.M):
        out[name] = int(value)
    return out


def load_font(text: str) -> dict[str, list[int]]:
    font: dict[str, list[int]] = {}
    for ch, body in re.findall(r"\{\s*'(.)'\s*,\s*\{([^}]*)\}\s*\}", text):
        rows = [int(x, 16) for x in re.findall(r"0x([0-9A-Fa-f]{2})", body)]
        if len(rows) == 7:
            font[ch] = rows
    return font


class Page:
    def __init__(self, d: dict[str, int], font: dict[str, list[int]]):
        self.d = d
        self.font = font
        self.W = d["IMG_W"]
        self.H = d["IMG_H"]

    def text_ink(self, s: str, x0: int, y0: int, scale: int, x: int, y: int) -> int:
        adv, gh = 6 * scale, 7 * scale
        if y < y0 or y >= y0 + gh:
            return 0
        dx = x - x0
        if dx < 0:
            return 0
        idx = dx // adv
        if idx >= len(s):
            return 0
        cx = (dx % adv) // scale
        if cx >= 5:
            return 0
        rows = self.font.get(s[idx])
        if not rows:
            return 0
        ry = ((y - y0) % gh) // scale
        return (rows[ry] >> (4 - cx)) & 1

    def color(self, x: int, y: int) -> int:
        d = self.d
        W, H = self.W, self.H
        B = d["BORDER"]
        if x < B or y < B or x >= W - B or y >= H - B:
            return BLACK

        if y >= d["SW_Y0"] and y < d["SW_Y0"] + d["SW_H"] and x >= d["SW_X0"]:
            colors = [BLACK, WHITE, RED, YELLOW]
            letters = "BWRY"
            for i in range(4):
                bx = d["SW_X0"] + i * (d["SW_W"] + d["SW_GAP"])
                if not (bx <= x < bx + d["SW_W"] and d["SW_Y0"] <= y < d["SW_Y0"] + d["SW_H"]):
                    continue
                inner = (bx + 2 <= x < bx + d["SW_W"] - 2
                         and d["SW_Y0"] + 2 <= y < d["SW_Y0"] + d["SW_H"] - 2)
                if not inner:
                    return BLACK
                base = colors[i]
                ink = WHITE if base == BLACK else BLACK
                lx = bx + (d["SW_W"] - 6 * 4) // 2
                ly = d["SW_Y0"] + (d["SW_H"] - 7 * 4) // 2
                if self.text_ink(letters[i], lx, ly, 4, x, y):
                    return ink
                return base

        if self.in_box(x, y, "CHK_X0", "CHK_Y0", "CHK_W", "CHK_H"):
            return BLACK if ((x // d["CHK_CELL"]) + (y // d["CHK_CELL"])) & 1 else WHITE

        if self.in_box(x, y, "STR_X0", "STR_Y0", d["STR_W"] * d["STR_N"], "STR_H"):
            return BLACK if ((x - d["STR_X0"]) // d["STR_W"]) & 1 else WHITE

        if d["FINE_Y0"] <= y < d["FINE_Y0"] + d["FINE_H"] and d["FINE_X0"] <= x <= d["FINE_X1"]:
            return BLACK if (y - d["FINE_Y0"]) % d["FINE_STEP"] == 0 else WHITE

        if self.in_box(x, y, "FCHK_X0", "FCHK_Y0", "FCHK_W", "FCHK_H"):
            return BLACK if ((x // d["FCHK_CELL"]) + (y // d["FCHK_CELL"])) & 1 else WHITE

        if x >= W - B - 32 and x < W - B - 4 and y >= B + 4 and y < B + 32:
            return BLACK
        if x >= B + 4 and x < B + 32 and y >= H - B - 32 and y < H - B - 4:
            return RED

        if self.text_ink("WIRING OK", d["TXT1_X"], d["TXT1_Y"], d["TXT1_SCALE"], x, y):
            return BLACK
        if self.text_ink("JD79665 768X552", d["TXT2_X"], d["TXT2_Y"], d["TXT2_SCALE"], x, y):
            return RED
        if self.text_ink("TOP LEFT", d["TXT3_X"], d["TXT3_Y"], d["TXT3_SCALE"], x, y):
            return BLACK
        return WHITE

    def in_box(self, x: int, y: int, kx: str, ky: str, kw: str | int, kh: str) -> bool:
        d = self.d
        x0, y0 = d[kx], d[ky]
        w = kw if isinstance(kw, int) else d[kw]
        h = d[kh]
        return x0 <= x < x0 + w and y0 <= y < y0 + h


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", type=int, default=1)
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)

    if not SRC.exists():
        print(f"找不到 {SRC}", file=sys.stderr)
        return 2
    text = SRC.read_text(encoding="utf-8")
    defines = load_defines(text)
    font = load_font(text)

    need = ["IMG_W", "IMG_H", "BORDER", "SW_X0", "SW_Y0", "SW_W", "SW_H", "SW_GAP",
            "CHK_X0", "CHK_Y0", "CHK_W", "CHK_H", "CHK_CELL",
            "STR_X0", "STR_Y0", "STR_W", "STR_N", "STR_H",
            "FINE_X0", "FINE_X1", "FINE_Y0", "FINE_H", "FINE_STEP",
            "FCHK_X0", "FCHK_Y0", "FCHK_W", "FCHK_H", "FCHK_CELL",
            "TXT1_X", "TXT1_Y", "TXT1_SCALE", "TXT2_X", "TXT2_Y", "TXT2_SCALE",
            "TXT3_X", "TXT3_Y", "TXT3_SCALE"]
    missing = [k for k in need if k not in defines]
    if missing:
        print("main.cpp 里缺少这些常量：" + ", ".join(missing), file=sys.stderr)
        return 2
    for ch in "WIRINGOKJD7965 8X2TPLFE":
        if ch not in font:
            print(f"字库缺少字符 {ch!r}", file=sys.stderr)
            return 2

    page = Page(defines, font)
    w, h = page.W, page.H
    img = Image.new("RGB", (w, h), PALETTE[WHITE])
    px = img.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = PALETTE[page.color(x, y)]
    if args.scale > 1:
        img = img.resize((w * args.scale, h * args.scale), Image.NEAREST)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    counts = {}
    for y in range(h):
        for x in range(w):
            counts[page.color(x, y)] = counts.get(page.color(x, y), 0) + 1
    total = w * h
    print(f"已写出 {out}  ({img.width}x{img.height})")
    for code, label in ((BLACK, "黑"), (WHITE, "白"), (RED, "红"), (YELLOW, "黄")):
        print(f"  {label}: {counts.get(code, 0) * 100 / total:5.2f}%")
    if counts.get(YELLOW, 0) == 0:
        print("  !! 黄色占用为 0，测试图里应该有黄色块", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
