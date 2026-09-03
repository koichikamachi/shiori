#!/usr/bin/env python3
"""
提出用の画像を作る（スクリーンショットの代わり）

    python tools/render_node_image.py

50行のJSONを画面に収めて、なお読める大きさにするのは難しい。
ファイルの中身をそのまま、色と大きさを整えて画像にする。
中身は data/nodes/*.json から読むので、書き換えは起きない。
"""
import json
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FONT_DIR = Path("/usr/share/fonts/opentype/noto")

BG = (24, 26, 32)
FG = (208, 214, 224)
LINENO = (92, 99, 112)
GUTTER = (30, 33, 40)
KEY = (122, 190, 255)
STR = (166, 220, 150)
NUM = (222, 178, 122)
LIT = (200, 140, 220)
PUNCT = (150, 156, 168)

SIZE = 26
LEAD = 42
PAD = 40
GUT = 90
MAXW = 132          # 1行の最大文字数（超えたら折る）


def load_font(bold=False, size=SIZE):
    name = "NotoSansCJK-Bold.ttc" if bold else "NotoSansCJK-Regular.ttc"
    path = FONT_DIR / name
    for idx in range(12):
        try:
            f = ImageFont.truetype(str(path), size, index=idx)
            if "Mono" in (f.getname()[0] or "") and "JP" in (f.getname()[0] or ""):
                return f
        except Exception:
            break
    return ImageFont.truetype(str(path), size, index=0)


def tokenize(line):
    """JSONの1行を、色を付ける単位に切り分ける。"""
    out, i = [], 0
    pat = re.compile(r'("(?:[^"\\]|\\.)*")\s*:|("(?:[^"\\]|\\.)*")|(-?\d+(?:\.\d+)?)|(true|false|null)')
    for m in pat.finditer(line):
        if m.start() > i:
            out.append((line[i:m.start()], PUNCT))
        if m.group(1):
            out.append((m.group(1), KEY))
            out.append((line[m.end(1):m.end()], PUNCT))
        elif m.group(2):
            out.append((m.group(2), STR))
        elif m.group(3):
            out.append((m.group(3), NUM))
        else:
            out.append((m.group(4), LIT))
        i = m.end()
    if i < len(line):
        out.append((line[i:], PUNCT))
    return out


def visual_len(s):
    """全角は2、半角は1として数える。"""
    return sum(2 if ord(c) > 0x2E80 else 1 for c in s)


def fold(line, limit=MAXW):
    """長い行を、字下げを保ったまま折る。"""
    if visual_len(line) <= limit:
        return [line]
    indent = len(line) - len(line.lstrip())
    head, rest = line[:indent], line[indent:]
    parts, cur, w = [], "", 0
    for c in rest:
        cw = 2 if ord(c) > 0x2E80 else 1
        if w + cw > limit - indent - 2:
            parts.append(cur)
            cur, w = "", 0
        cur += c
        w += cw
    if cur:
        parts.append(cur)
    return [head + parts[0]] + [head + "  " + p for p in parts[1:]]


def render(src_lines, out_path, title):
    font = load_font()
    fb = load_font(bold=True)
    tf = load_font(bold=True, size=SIZE + 4)

    lines = []
    for n, raw in enumerate(src_lines, 1):
        for k, piece in enumerate(fold(raw.rstrip("\n"))):
            lines.append((n if k == 0 else None, piece))

    # 幅は一番長い行から決める
    probe = Image.new("RGB", (10, 10))
    d0 = ImageDraw.Draw(probe)
    width = max(d0.textlength(t, font=font) for _, t in lines)
    W = int(GUT + PAD + width + PAD)
    H = int(PAD + LEAD + 20 + len(lines) * LEAD + PAD)

    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, GUT, H], fill=GUTTER)

    d.text((GUT + PAD // 2, PAD - 8), title, font=tf, fill=(255, 255, 255))
    d.line([(0, PAD + LEAD), (W, PAD + LEAD)], fill=(60, 64, 74), width=2)

    y = PAD + LEAD + 20
    for no, text in lines:
        if no is not None:
            s = str(no)
            d.text((GUT - PAD // 2 - d.textlength(s, font=font), y), s, font=font, fill=LINENO)
        x = GUT + PAD // 2
        for tok, color in tokenize(text):
            d.text((x, y), tok, font=font, fill=color)
            x += d.textlength(tok, font=font)
        y += LEAD

    img.save(out_path)
    return img.size


if __name__ == "__main__":
    src = ROOT / "docs" / "submission" / "screenshot_node.json"
    out = ROOT / "docs" / "submission" / "img_node.png"
    size = render(src.read_text(encoding="utf-8").splitlines(),
                  out, "data/nodes/nodes_self.json — SELF_BIZ_SUCCESSION_TAX")
    print(f"{out} {size[0]}x{size[1]}")
