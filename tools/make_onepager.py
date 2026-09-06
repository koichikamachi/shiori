#!/usr/bin/env python3
"""
会場で人に渡す、A4一枚。

    python tools/make_onepager.py
    → docs/print/S12_一枚.html をブラウザで開いて Ctrl+P

**数は、その場でデータから数える。** 紙に手で書き写すと、必ず古くなる。
75件・109件・281本を口で言い間違えないために、機械に数えさせて刷る。

**地図そのものは刷らない。** 紙の地図は、本物の劣ったコピーである。
QRコードを読んでもらって、相手の手元で本物を開いてもらう。
そのとき「ログインを求められない」ことも、相手の目で確かめてもらえる。
"""
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

URL = "https://shiori-120582716769.asia-northeast1.run.app"


def counts() -> dict:
    nodes = verified = deadlines = 0
    by_scope = {}
    for f in sorted((ROOT / "data" / "nodes").glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        sc = d.get("actor_scope", "?")
        by_scope[sc] = by_scope.get(sc, 0) + len(d["nodes"])
        for n in d["nodes"]:
            nodes += 1
            lm = n.get("legal_metadata") or {}
            if lm.get("verification_status") in ("SOURCE_VERIFIED", "EXPERT_VERIFIED"):
                verified += 1
            if n.get("deadline"):
                deadlines += 1
    checks = json.loads((ROOT / "data" / "checks" / "checks.json")
                        .read_text(encoding="utf-8")).get("checks", [])
    conds = json.loads((ROOT / "data" / "schema" / "conditions.json")
                       .read_text(encoding="utf-8"))
    n_cond = len([k for k in conds.get("conditions", {})]) or len(conds.get("conditions", []))
    return {"nodes": nodes, "by_scope": by_scope, "verified": verified,
            "deadlines": deadlines, "checks": len(checks), "conds": n_cond}


def n_tests() -> str:
    """テストの本数も、その場で数える。数えられなければ空欄にする。"""
    try:
        r = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q"],
                           cwd=ROOT, capture_output=True, text=True, timeout=120)
        for line in reversed(r.stdout.strip().splitlines()):
            if "test" in line and "collected" in line:
                return line.split()[0]
    except Exception:
        pass
    return "—"


def qr(url: str) -> str:
    try:
        import segno
    except ImportError:
        return '<div class="noqr">QRは segno を入れると出ます</div>'
    return segno.make(url, error="m").svg_inline(scale=1, border=2, dark="#1c1c1c")


CSS = """
@page{size:A4;margin:14mm}
*{box-sizing:border-box}
body{margin:0;font-family:"Hiragino Kaku Gothic ProN","Yu Gothic",sans-serif;
 color:#1c1c1c;line-height:1.5;font-size:9.8pt}
h1{font-size:17pt;margin:0 0 2px;letter-spacing:.01em}
.sub{color:#666;font-size:9pt;margin:0 0 10px}
.rule{border:0;border-top:2px solid #1c1c1c;margin:0 0 10px}
.band{display:flex;gap:16px;align-items:center;border:1.5px solid #1c1c1c;
 border-radius:6px;padding:9px 12px;margin:0 0 11px}
.band .qr{width:78px;flex:0 0 78px}
.band .qr svg{width:100%;height:auto;display:block;shape-rendering:crispEdges}
.band .u{font-family:ui-monospace,Consolas,monospace;font-size:11.5pt;
 font-weight:700;word-break:break-all;line-height:1.35}
.band .c{font-size:8.8pt;color:#555;margin-top:4px}
h2{font-size:10.2pt;margin:11px 0 4px;padding-bottom:3px;border-bottom:1px solid #bbb}
ol,ul{margin:5px 0;padding-left:1.25em}
li{margin:3px 0}
b{color:#000}
table{border-collapse:collapse;width:100%;font-size:8.8pt;margin:5px 0 0}
td,th{border:1px solid #ccc;padding:3px 6px;text-align:left}
th{background:#f3f1ec;font-weight:600;width:38%}
.two{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.ask{background:#f7f5ef;border-left:3px solid #1c1c1c;padding:7px 10px;margin:5px 0}
.ask>b:first-child{display:block;margin-bottom:2px}
footer{margin-top:11px;padding-top:6px;border-top:1px solid #ccc;
 color:#666;font-size:8.5pt}
@media screen{body{max-width:800px;margin:26px auto;padding:0 22px}}
"""


def build() -> Path:
    c = counts()
    sc = c["by_scope"]
    t = n_tests()
    # ファイルの actor_scope を、人に言える呼び名に直す
    NAME = {"SELF": "本人", "FAMILY_CHILD": "家族",
            "RELATIVES / LEGAL_HEIR": "遺族"}
    scope_line = "／".join(f"{NAME.get(k, k)} {v}" for k, v in
                          sorted(sc.items(), key=lambda kv: -kv[1]))

    html = f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<title>しおり（Shiori）— 一枚</title><style>{CSS}</style></head><body>

<h1>しおり — 相続と、亡くなったあとの手続の地図</h1>
<p class="sub">第5回 Agentic AI Hackathon with Google Cloud ／ BKW研究所　蒲池孝一（公認会計士）</p>
<hr class="rule">

<div class="band">
  <div class="qr">{qr(URL)}</div>
  <div>
    <div class="u">{URL}</div>
    <div class="c">Cloud Run で動いています。<b>ログインは要りません。</b>
    今この場で、お手持ちのスマートフォンで開けます。</div>
  </div>
</div>

<h2>何をするものか</h2>
<p>相続と、亡くなったあとの手続を、<b>「順序」と「通り過ぎると戻れない地点」を持つ地図</b>として
先に構造化したものです。そのうえで、今日決めることを3件だけ渡します。
<b>ただし地図は、最初から全部見えます。</b></p>

<h2>設計の要点（三つ）</h2>
<ol>
<li><b>判断は生成AIにさせない。</b> 順序・戻れない地点・期限は、決定論的なプログラムが100%決める。
法律の手続で誤った案内をすると実害が出るため</li>
<li><b>保存しない。</b> 金額も名前も口座番号も、入力欄そのものが無い。ログインもCookieも無い</li>
<li><b>「いいえ」と「まだ答えていない」を混ぜない。</b> 未回答は灰色の霧として描く。
混ぜると、初日の遺族に「不動産は無いので名義変更は不要です」と断定してしまう</li>
</ol>

<div class="two">
<div>
<h2>いま動いているもの</h2>
<table>
<tr><th>手続（ノード）</th><td>{c["nodes"]}件（{scope_line}）</td></tr>
<tr><th>条件</th><td>{c["conds"]}個</td></tr>
<tr><th>テスト</th><td>{t}本（うち関門21本はキー不要）</td></tr>
<tr><th>期限を持つもの</th><td>{c["deadlines"]}件<b>（全部、一次資料で確認済み）</b></td></tr>
<tr><th>一次資料で確認済み</th><td>{c["verified"]}件</td></tr>
<tr><th><b>まだ確かめていない</b></th><td><b>{c["checks"]}件</b>（画面に出しています）</td></tr>
</table>
</div>
<div>
<h2>まだ無いもの（正直に）</h2>
<ul>
<li>ADK／Gemini の配線（9月中旬）</li>
<li>アーキテクチャ図、3分の動画</li>
<li><b>資格を持つ専門家による点検</b>（期限10件の表を先に作りました）</li>
</ul>
<h2>期限の扱い</h2>
<p>期限のある手続は{c["deadlines"]}件だけ。<b>一次資料に当たっていないと lint が通らない</b>ようにし、
画面には期限の隣に「確かめた先」と確認日を出しています。</p>
</div>
</div>

<h2>聞きたいこと</h2>
<div class="ask"><b>① 審査基準②「自律的に行動するエージェントか。可観測性やセキュリティへの配慮」</b>
判断をあえて決定論に置いています。自律性は「手続の判断」ではなく
<b>「地図の保守」</b>——未確認{c["checks"]}件を、エージェントが公的機関のサイトで調べ、
逐語引用つきの<b>修正案</b>を出す——に置く考えです。この立て方で満たしますか。</div>
<div class="ask"><b>② ADKで「調べる・読む・突き合わせる・崩す」を組む最短の形</b>
一人にまとめると、探した本人が「見つけた」と言い張ります。
とくに<b>崩す役</b>を分けたい。三役から始めて足す順でよいでしょうか。</div>
<div class="ask"><b>③ 地図を、初めて見た人に見せたときの反応</b>
説明の前に、<b>黙って見ていただきたい</b>のです。何が見えましたか。</div>

<footer>これは、自分の終活のために作った地図の<b>試作品</b>です。研究途上のもので、完成版ではありません。
個別の法律相談・税務相談ではありません。制度は変わります。
多くの項目は、まだ専門家の確認を受けていません。　／　{date.today().isoformat()} 現在</footer>

</body></html>"""

    out = ROOT / "docs" / "print" / "S12_一枚.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out


if __name__ == "__main__":
    p = build()
    print(p.relative_to(ROOT))
    print("\nブラウザで開いて Ctrl+P。A4一枚に収まる。数はデータから数えている。")
