#!/usr/bin/env python3
"""
持って行く紙を作る。

    python tools/make_printable.py

Markdown をそのまま印刷すると、`#` や `|` が文字として出て読めない。
会場で読む紙なので、表が表として出ること、行が詰まりすぎないことが要る。

出すもの
    docs/print/S05_当日の質問.html
    docs/print/S09_期限の表.html

ブラウザで開いて、そのまま Ctrl+P。
"""
import sys
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "print"

CSS = """
@page { size: A4; margin: 16mm 14mm; }
body{font-family:"Hiragino Kaku Gothic ProN","Yu Gothic",system-ui,sans-serif;
 font-size:10.5pt;line-height:1.75;color:#1c1c1c;max-width:180mm;margin:0 auto;padding:10mm}
h1{font-size:17pt;border-bottom:2px solid #333;padding-bottom:5px;margin:0 0 12px}
h2{font-size:13pt;margin:22px 0 8px;padding:5px 8px;background:#f0ede6;
 border-left:4px solid #666;page-break-after:avoid}
h3{font-size:11.5pt;margin:14px 0 6px;page-break-after:avoid}
p{margin:6px 0}
blockquote{margin:8px 0;padding:8px 12px;background:#f7f5ef;border-left:3px solid #b9b3a7;
 page-break-inside:avoid}
blockquote p{margin:4px 0}
table{border-collapse:collapse;width:100%;margin:8px 0;font-size:9.5pt;
 page-break-inside:avoid}
th,td{border:1px solid #bbb;padding:4px 7px;text-align:left;vertical-align:top}
th{background:#efece5}
code{background:#f0ede6;padding:1px 4px;border-radius:3px;font-size:9.5pt}
pre{background:#fff;border:1px solid #999;border-radius:3px;padding:14px;
 min-height:58px;page-break-inside:avoid}
pre code{background:none}
ul,ol{margin:6px 0;padding-left:22px}
li{margin:3px 0}
hr{border:0;border-top:1px solid #ccc;margin:18px 0}
strong{font-weight:700}
li input[type=checkbox]{margin-right:6px}
@media print{ body{padding:0} }
"""

TARGETS = ["S05_当日の質問.md", "S09_期限の表.md", "S10_デプロイ手順.md",
           "S11_今晩の手順.md"]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    md = markdown.Markdown(extensions=["tables", "fenced_code", "sane_lists", "nl2br"])
    made = []
    for name in TARGETS:
        src = ROOT / "docs" / name
        if not src.exists():
            print(f"ない: {name}", file=sys.stderr)
            continue
        md.reset()
        text = src.read_text(encoding="utf-8")
        # 空のコードブロックは「書き込む枠」なので、そのまま枠として残す
        body = md.convert(text)
        html = (f'<!doctype html><html lang="ja"><head><meta charset="utf-8">'
                f'<title>{src.stem}</title><style>{CSS}</style></head>'
                f'<body>{body}</body></html>')
        dst = OUT / (src.stem + ".html")
        dst.write_text(html, encoding="utf-8")
        made.append(dst)
    for d in made:
        print(f"{d.relative_to(ROOT)}")
    print("\nブラウザで開いて Ctrl+P。用紙はA4、余白は既定のままでよい。")


if __name__ == "__main__":
    main()
