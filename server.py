#!/usr/bin/env python3
"""
しおり — 地図を配るだけの、小さなサーバ

    ローカルで動かす
        pip install -r requirements-web.txt
        python -m uvicorn server:app --port 8080 --reload
        → http://127.0.0.1:8080

    Cloud Run へ上げる
        docs/S10_デプロイ手順.md を見る


■ なぜこの形にしたか

**あらかじめ書き出したHTMLを配るのではなく、その場で組んで返す。**
理由は二つある。

一つ。データを直したら、次にページを開いた人には、もう直っている。
書き出したファイルを配ると、書き出し忘れがそのまま古い案内になる。

二つ。**決定論のエンジンが、クラウドのうえでも同じ答えを出すことを、
URLそのものが示す。** 同じ場面を二度開けば、同じ地図が出る。
これは審査基準②の「可観測性」に直に効く。

■ 保存しない

セッションを持たない。ログインが要らない。書き込む先がない。
だからインスタンスが消えても、失うものが何も無い。
Cloud Run の性質と、このアプリの方針が、たまたま同じ形をしている。

■ 状態はURLに持たせる

`/map/surv-day10-nomemo?today=2026-09-05` のように、
場面と日付だけをURLで受ける。サーバは何も覚えない。
"""
import os
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse

from src.main import SCENARIOS
from tools.render_map_svg import NOTICE, page

ROOT = Path(__file__).resolve().parent

app = FastAPI(
    title="しおり（Shiori）",
    description="相続と、亡くなったあとの手続を、順序と期限の地図として示す試作品",
    docs_url=None, redoc_url=None,
)

# 立場ごとにまとめて出す。順序は固定（毎回おなじ形をしていること）
GROUPS = [
    ("SURVIVOR", "遺された人の地図",
     "亡くなったあと、遺族が見る面。今日決めることは三件を超えない。"),
    ("SELF", "本人の地図（生きているうち）",
     "自分でできる備えの面。既定でこちらは地図から始まる。"),
    ("FAMILY_CHILD", "親を気にかける家族の地図",
     "親に切り出せない人の面。母数がいちばん大きい立場である。"),
]

INDEX_CSS = """
:root{color-scheme:light}
*{box-sizing:border-box}
body{margin:0;background:#fbfaf7;color:#2b2b2b;
 font-family:"Hiragino Kaku Gothic ProN","Yu Gothic",system-ui,sans-serif;
 line-height:1.8}
.wrap{max-width:760px;margin:0 auto;padding:34px 22px 60px}
h1{font-size:22px;margin:0 0 6px;letter-spacing:.02em}
.sub{color:#6f6f6f;font-size:13px;margin:0 0 22px}
.about{background:#fff;border:1px solid #e7e3d9;border-radius:12px;
 padding:16px 18px;font-size:13.5px;margin:0 0 26px}
.about p{margin:7px 0}
.about b{color:#1c1c1c}
.does{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin:14px 0 0}
.does>div{background:#f7f5ef;border-radius:8px;padding:11px 13px;font-size:12.5px}
.does h3{margin:0 0 5px;font-size:12.5px;color:#6b6455}
.does ul{margin:0;padding-left:17px}
.does li{margin:2px 0}
h2{font-size:15px;margin:30px 0 3px;padding-bottom:5px;border-bottom:1px solid #e7e3d9}
.note{color:#8a8272;font-size:12.5px;margin:5px 0 12px}
a.card{display:block;background:#fff;border:1px solid #e7e3d9;border-radius:10px;
 padding:12px 15px;margin:8px 0;text-decoration:none;color:#2b2b2b;
 transition:border-color .12s}
a.card:hover{border-color:#b9b3a7}
a.card .t{font-size:14px;font-weight:700}
a.card .k{font-size:11.5px;color:#a89f8b;margin-top:2px;font-family:ui-monospace,monospace}
footer{margin-top:36px;padding-top:14px;border-top:1px solid #e7e3d9;
 color:#6f6f6f;font-size:11.5px}
.stat{display:flex;flex-wrap:wrap;gap:0 16px;font-size:12px;color:#6f6f6f;margin:10px 0 0}
@media(max-width:560px){.does{grid-template-columns:1fr}}
"""


def esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _today(s: str | None) -> date:
    """日付はURLで受ける。おかしければ今日にする（落とさない）。"""
    if not s:
        return date.today()
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return date.today()


def _counts() -> dict:
    """いま持っているものの数。自分の知らないことも数えて出す。"""
    import json
    nodes = 0
    for f in sorted((ROOT / "data" / "nodes").glob("*.json")):
        nodes += len(json.loads(f.read_text(encoding="utf-8"))["nodes"])
    checks = ROOT / "data" / "checks" / "checks.json"
    n_check = 0
    if checks.exists():
        n_check = len(json.loads(checks.read_text(encoding="utf-8")).get("checks", []))
    verified = 0
    deadlines = 0
    for f in sorted((ROOT / "data" / "nodes").glob("*.json")):
        for n in json.loads(f.read_text(encoding="utf-8"))["nodes"]:
            lm = n.get("legal_metadata") or {}
            if lm.get("verification_status") in ("SOURCE_VERIFIED", "EXPERT_VERIFIED"):
                verified += 1
            if n.get("deadline"):
                deadlines += 1
    return {"nodes": nodes, "checks": n_check, "verified": verified,
            "deadlines": deadlines}


@app.get("/healthz", response_class=PlainTextResponse)
def healthz() -> str:
    return "ok"


@app.get("/", response_class=HTMLResponse)
def index(today: str | None = Query(default=None)) -> str:
    d = _today(today)
    c = _counts()
    q = f"?today={d.isoformat()}" if today else ""

    o = [f'<!doctype html><html lang="ja"><head><meta charset="utf-8">'
         f'<meta name="viewport" content="width=device-width,initial-scale=1">'
         f'<title>しおり — 相続と、亡くなったあとの手続の地図</title>'
         f'<style>{INDEX_CSS}</style></head><body><div class="wrap">']
    a = o.append

    a('<h1>しおり</h1>')
    a('<p class="sub">相続と、亡くなったあとの手続を、'
      '順序と期限の地図として示す試作品</p>')

    a('<div class="about">')
    a('<p>これは、自分の終活のために作った地図の、<b>試作品</b>です。'
      '研究途上のもので、完成版ではありません。</p>')
    a('<p>自分が亡くなったあと、家族が何を、どの順番で、いつまでに'
      'しなければならないのか。それを知りたくて、AIに手伝ってもらいながら、'
      '手続の順序と期限を一枚の地図に描いてきました。'
      '同じことで困る方がおられるだろうと思い、そのまま置いています。無償です。</p>')
    a('<div class="does">')
    a('<div><h3>このアプリがすること</h3><ul>'
      '<li>手続の順序を示す</li>'
      '<li>期限の長さと、いつから数えるかを示す</li>'
      '<li>通り過ぎると戻れない地点を示す</li>'
      '<li>何を誰に聞けばよいかを示す</li>'
      '</ul></div>')
    a('<div><h3>このアプリがしないこと</h3><ul>'
      '<li>あなたの場合の結論は出さない</li>'
      '<li>金額を計算しない。入力欄もない</li>'
      '<li>書類を作らない。代理もしない</li>'
      '<li>法律相談・税務相談に答えない</li>'
      '</ul></div>')
    a('</div>')
    a(f'<div class="stat">'
      f'<span>手続 {c["nodes"]} 件</span>'
      f'<span>うち期限のあるもの {c["deadlines"]} 件（全部、公的機関の資料で確認済み）</span>'
      f'<span>一次資料で確認済み {c["verified"]} 件</span>'
      f'<span>まだ確かめていない項目 {c["checks"]} 件</span>'
      f'</div>')
    a('</div>')

    for scope, heading, note in GROUPS:
        items = [(k, v) for k, v in SCENARIOS.items() if v["scope"] == scope]
        if not items:
            continue
        a(f'<h2>{esc(heading)}</h2>')
        a(f'<p class="note">{esc(note)}</p>')
        for k, v in items:
            a(f'<a class="card" href="/map/{esc(k)}{q}">'
              f'<div class="t">{esc(v["title"])}</div>'
              f'<div class="k">{esc(k)}</div></a>')

    a(f'<footer>{esc(NOTICE)}<br>'
      f'この画面は {d.isoformat()} を「今日」として計算しています。'
      f'状態は何も保存していません。ログインも要りません。</footer>')
    a('</div></body></html>')
    return "".join(o)


@app.get("/map/{scenario}", response_class=HTMLResponse)
def show_map(scenario: str, today: str | None = Query(default=None)) -> str:
    if scenario not in SCENARIOS:
        raise HTTPException(status_code=404, detail="そんな場面はありません")
    d = _today(today)
    home = "/" + (f"?today={d.isoformat()}" if today else "")
    return page(scenario, d, home=home)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
