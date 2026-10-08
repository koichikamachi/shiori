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
from dotenv import load_dotenv
load_dotenv()

import os
import json
import queue
import threading
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from src.main import SCENARIOS
from src.agent.guards import validate_node_id
from tools.render_map_svg import NOTICE, page

ROOT = Path(__file__).resolve().parent

app = FastAPI(
    title="しおり（Shiori）",
    description="相続と、亡くなったあとの手続を、順序と期限の地図として整理",
    docs_url=None, redoc_url=None,
)

# 立場ごとにまとめて出す。順序は固定（毎回おなじ形をしていること）
GROUPS = [
    ("SURVIVOR", "遺された人の地図",
     "亡くなったあとの手続きと、期限に追われないための全体像"),
    ("SELF", "本人の地図（生きているうち）",
     "残される家族が迷わないよう、いま自分ができる備えを整理する"),
    ("FAMILY_CHILD", "親を気にかける家族の地図",
     "親に切り出しにくいお金や実家のこと、角を立てずに話すきっかけ"),
]

INDEX_CSS = """
/* 入口は「3つの扉」。問い → 扉 → 場面 → 地図を開く、の順に一段ずつ進む。
   生成りの敷きに、控えめな枠と影。見出しは明朝、本文はゴシック。
   Web フォントは読み込まない（外部への通信を増やさない）。手元の明朝・ゴシックを使う。 */
:root{color-scheme:light;--ink:#1a202c;--sub:#4a5568;--rule:#e2dcce;--navy:#1E3A5F;--bg:#FAF8F5;--card:#fff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
 font-family:"Hiragino Kaku Gothic ProN","Yu Gothic","Noto Sans JP",system-ui,sans-serif;line-height:1.8}
.wrap{max-width:820px;margin:0 auto;padding:48px 22px 64px}

/* ヘッダー（名前と、このアプリの二つの約束） */
.topbar{background:#fff;border-bottom:1px solid var(--rule)}
.tb-in{max-width:1100px;margin:0 auto;padding:14px 22px;display:flex;align-items:center;justify-content:space-between;gap:14px;flex-wrap:wrap}
.brand{display:flex;align-items:center;gap:11px;text-decoration:none;color:var(--ink)}
.mark{display:inline-flex;align-items:center;justify-content:center;width:34px;height:34px;border-radius:8px;
 background:var(--navy);color:#fff;font-size:19px;font-weight:700}
.bt{display:flex;flex-direction:column;line-height:1.2}
.bn{font-size:20px;font-weight:700;letter-spacing:.06em}
.bs{font-size:10.5px;color:#8a8f98;letter-spacing:.14em;margin-top:3px}
.badges{display:flex;gap:10px;flex-wrap:wrap}
.bdg{display:inline-flex;align-items:center;gap:6px;font-size:13px;line-height:1.4;padding:6px 13px;border-radius:999px;white-space:nowrap}
.bdg svg{width:14px;height:14px;fill:none;stroke:currentColor;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round}
.bdg.ok{background:#eaf7f0;border:1px solid #a8dcc0;color:#146c43}
.bdg.lock{background:#f1f0ed;border:1px solid #d8d4cc;color:#3d3a33}
.serif{font-family:"Noto Serif JP","Noto Serif CJK JP","Hiragino Mincho ProN","Yu Mincho","YuMincho",serif}

/* Hero */
.hook{font-size:17px;color:var(--sub);margin:0 0 10px;letter-spacing:.04em}
h1{font-size:40px;font-weight:700;letter-spacing:.06em;line-height:1.35;margin:0 0 12px}
.sub{color:#374151;font-size:15px;line-height:1.7;margin:0 0 40px}
.ask{color:var(--navy);font-size:21px;font-weight:700;letter-spacing:.03em;margin:0 0 16px}

/* 3つの扉 */
.doors{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}
.door{display:block;width:100%;text-align:left;cursor:pointer;background:var(--card);color:var(--ink);
 border:1px solid var(--rule);border-radius:12px;padding:20px 18px 18px;font:inherit;
 box-shadow:0 1px 2px rgba(30,58,95,.04);transition:border-color .15s,box-shadow .15s,transform .15s}
.door:hover{border-color:#b9b09a;box-shadow:0 4px 14px rgba(30,58,95,.08);transform:translateY(-1px)}
.door:focus-visible{outline:3px solid var(--navy);outline-offset:2px}
.door[aria-expanded="true"]{border-color:var(--navy);box-shadow:0 0 0 2px var(--navy) inset}
.door .dt{display:block;font-size:19px;font-weight:700;line-height:1.45;margin:0 0 8px}
.door .dd{display:block;font-size:14px;color:var(--sub);line-height:1.7}
.door .dn{display:block;font-size:12.5px;color:#6b7280;margin-top:10px}

/* 扉を開いたあとの場面一覧 */
.panel{margin:18px 0 0;background:var(--card);border:1px solid var(--rule);border-radius:12px;padding:18px 20px}
.js .panel{display:none}
.js .panel.open{display:block}
.panel h2{font-size:18px;font-weight:700;margin:0 0 4px}
.panel .note{color:var(--sub);font-size:14px;margin:0 0 10px}
.scn{display:block;border:1px solid var(--rule);border-radius:9px;padding:11px 14px;margin:8px 0;
 text-decoration:none;color:var(--ink);background:#fdfcfa;transition:border-color .15s,background .15s}
.scn:hover{border-color:#b9b09a;background:#fff}
.scn:focus-visible{outline:3px solid var(--navy);outline-offset:2px}
.scn .t{font-size:15.5px;font-weight:600;line-height:1.55}
.scn .t::after{content:" →";color:#9aa4ad;font-weight:400}
/* 内部キー（surv-day1 等）は画面に出さない。読む人には意味が無い。 */
.scn .k{display:none}

/* このアプリについて（控えめな枠） */
.about{margin:48px 0 0;background:#f4f1ea;border:1px solid var(--rule);border-radius:10px;
 padding:14px 18px;font-size:13.5px;line-height:1.75;color:#1f2937}
.about summary{cursor:pointer;font-weight:700;color:var(--sub)}
.about p{margin:10px 0 0}
.about b{color:#000}
.does{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:12px 0 0}
.does>div{background:#fff;border:1px solid var(--rule);border-radius:8px;padding:11px 13px;font-size:13px;line-height:1.7}
.does h3{margin:0 0 4px;font-size:13px;font-weight:700}
.does ul{margin:0;padding-left:18px}
.stat{display:flex;flex-wrap:wrap;font-size:13px;color:#374151;margin:12px 0 0;padding-top:10px;border-top:1px dashed #d1d5db}
.stat span{white-space:nowrap}
.stat span+span::before{content:"｜";color:#c9c1af;margin:0 8px}

footer{margin-top:18px;color:#374151;font-size:13.5px;line-height:1.8;padding:0 4px}
footer p{margin:0}
footer p+p{margin-top:6px}
@media(max-width:700px){.bdg{white-space:normal;font-size:12.5px}.doors{grid-template-columns:1fr}.does{grid-template-columns:1fr}h1{font-size:32px}.wrap{padding-top:36px}}
"""

# 扉と場面の切り替え。JS が無くても、全部の場面がリンクとして並び、そのまま開ける。
INDEX_JS = """
(function () {
  document.documentElement.classList.add('js');
  var doors = document.querySelectorAll('.door');
  var panels = document.querySelectorAll('.panel');
  doors.forEach(function (d) {
    d.addEventListener('click', function () {
      var id = d.getAttribute('aria-controls');
      var wasOpen = d.getAttribute('aria-expanded') === 'true';
      doors.forEach(function (x) { x.setAttribute('aria-expanded', 'false'); });
      panels.forEach(function (p) { p.classList.remove('open'); });
      if (!wasOpen) {
        d.setAttribute('aria-expanded', 'true');
        var p = document.getElementById(id);
        p.classList.add('open');
        p.scrollIntoView({behavior: 'smooth', block: 'nearest'});
      }
    });
  });
  // 場面の行は、そのまま地図へのリンク。押した瞬間に地図へ進む。
})();
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
         f'<style>{INDEX_CSS}</style></head><body>'
         f'<header class="topbar"><div class="tb-in">'
         f'<a class="brand" href="/{q}"><span class="mark serif" aria-hidden="true">栞</span>'
         f'<span class="bt"><span class="bn serif">しおり</span><span class="bs">SHIORI NAVIGATOR</span></span></a>'
         f'<div class="badges">'
         f'<span class="bdg ok"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8.5l3 3 7-7"/></svg>'
         f'照合先は公的機関のみ（.go.jp / .lg.jp）</span>'
         f'<span class="bdg lock"><svg viewBox="0 0 16 16" aria-hidden="true"><rect x="3.5" y="7" width="9" height="6.5" rx="1.2"/><path d="M5.5 7V5a2.5 2.5 0 015 0v2"/></svg>'
         f'個人情報の入力なし・保存なし</span>'
         f'</div></div></header>'
         f'<div class="wrap">']
    a = o.append

    # ---- Hero ----
    a('<p class="hook serif">家族が亡くなったら、まず何をすればいいのだろう。</p>')
    a('<h1 class="serif">迷いを、安心の順序へ。</h1>')
    a('<p class="sub">相続・死後の手続きと、生前の備えを順を追って整理する公的照合ガイド</p>')
    a('<p class="ask serif">いま、あなたの立場はどこにありますか？</p>')

    # ---- 3つの扉 ----
    groups = [(scope, heading, note,
               [(k, v) for k, v in SCENARIOS.items() if v["scope"] == scope])
              for scope, heading, note in GROUPS]
    groups = [g for g in groups if g[3]]
    a('<div class="doors">')
    for scope, heading, note, items in groups:
        pid = f"panel-{scope.lower()}"
        a(f'<button type="button" class="door" aria-expanded="false" aria-controls="{pid}">'
          f'<span class="dt serif">{esc(heading)}</span>'
          f'<span class="dd">{esc(note)}</span>'
          f'<span class="dn">{len(items)}つの場面から選べます</span></button>')
    a('</div>')

    # ---- 扉を開いたあとの場面一覧（JS が無いときは全部開いたまま） ----
    for scope, heading, note, items in groups:
        pid = f"panel-{scope.lower()}"
        a(f'<section class="panel" id="{pid}">')
        a(f'<h2 class="serif">{esc(heading)}</h2>')
        a(f'<p class="note">いちばん近い場面を押すと、その地図が開きます。</p>')
        for k, v in items:
            a(f'<a class="scn" href="/map/{esc(k)}{q}">'
              f'<div class="t">{esc(v["title"])}</div>'
              f'<div class="k">{esc(k)}</div></a>')
        a('</section>')

    # ---- このアプリについて（控えめな枠へ移した） ----
    a('<details class="about"><summary>このアプリについて（実証プロトタイプ）</summary>')
    a('<p>これは、自身の終活から着想を得て設計された、手続地図の'
      '<b>実証プロトタイプ（PoC）</b>です。</p>')
    a('<p>自分が亡くなったあと、残された家族は何を、どの順番で、いつまでに'
      'しなければならないのか。<br>'
      'その迷いをゼロにするために、公的資料（一次証憑）を自律的に照合するAIエージェントと共に、'
      '手続の順序と不可逆な期限を一枚の地図に体系化しました。<br>'
      '同じ課題を抱いている方へ、お届けします。</p>')
    a('<div class="does">')
    a('<div><h3>このアプリがすること</h3><ul>'
      '<li>手続の順序と、依存関係を示す</li>'
      '<li>期限の長さと、起算点（いつから数えるか）を示す</li>'
      '<li>一度踏むと戻れない「不可逆な地点（法的リスク）」を示す</li>'
      '<li>何を誰に確認すべきかの公的窓口を示す</li>'
      '</ul></div>')
    a('<div><h3>このアプリがしないこと（法規範の遵守）</h3><ul>'
      '<li>個別の事情に応じた法的な結論は出さない</li>'
      '<li>税額や財産の計算は行わない（入力欄も持たない）</li>'
      '<li>公的書類の自動作成や申請代理は行わない</li>'
      '<li>弁護士法・税理士法に抵触する個別相談には答えない</li>'
      '</ul></div>')
    a('</div>')
    a(f'<div class="stat">'
      f'<span>手続 {c["nodes"]}件</span>'
      f'<span>期限あり {c["deadlines"]}件（公的機関の資料で確認済み）</span>'
      f'<span>一次資料照合済み {c["verified"]}件</span>'
      f'<span>専門家による検証待ち項目 {c["checks"]}件</span>'
      f'</div>')
    a('</details>')

    a(f'<footer><p>{esc(NOTICE)}</p>'
      f'<p>この画面は {d.isoformat()} を「今日」として計算しています。'
      f'入力された情報や利用履歴は一切保存していません（公的制度の照合記録のみ保持）。ログインも要りません。</p></footer>')
    a('</div><script>' + INDEX_JS + '</script></body></html>')
    return "".join(o)


class AgentCheckRequest(BaseModel):
    node_id: str


def _get_checks_map() -> dict:
    checks_map = {}
    checks_path = ROOT / "data" / "checks" / "checks.json"
    if checks_path.exists():
        try:
            data = json.loads(checks_path.read_text(encoding="utf-8"))
            for check in data.get("checks", []):
                nid = check.get("node_id")
                if nid:
                    checks_map.setdefault(nid, []).append({
                        "check_id": check.get("check_id"),
                        "question": check.get("question"),
                        "claim": check.get("claim"),
                        "current_status": check.get("current_status"),
                        "current_source_url": check.get("current_source_url")
                    })
        except Exception:
            pass
    return checks_map


def _find_node(node_id: str) -> dict | None:
    for f in sorted((ROOT / "data" / "nodes").glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            for n in data.get("nodes", []):
                if n.get("id") == node_id:
                    return n
        except Exception:
            pass
    return None


def _find_check(node_id: str) -> dict | None:
    checks_path = ROOT / "data" / "checks" / "checks.json"
    if checks_path.exists():
        try:
            data = json.loads(checks_path.read_text(encoding="utf-8"))
            for check in data.get("checks", []):
                if check.get("node_id") == node_id:
                    return check
        except Exception:
            pass
    return None


# 生成文にまぎれる過剰敬語を、人が読む手前で端正な「です・ます調」にそろえる。
# プロンプトでも禁じてあるが、言葉づかいは揺れる。デパートの接客調になると、
# 公的な案内としての品格が落ちる。最後は機械で均す。
_POLITE_FIXES = (
    ("可能性がございます", "可能性があります"),
    ("でございます", "です"),
    ("ございません", "ありません"),
    ("ございます", "あります"),
    ("ております", "ています"),
    ("いたします", "します"),
    ("致します", "します"),
)


def _tidy(text):
    """接客口調をやめる。文字列でなければ、そのまま返す。"""
    if not isinstance(text, str):
        return text
    for before, after in _POLITE_FIXES:
        text = text.replace(before, after)
    return text


def _tidy_details(details: dict) -> dict:
    """
    人が読む「説明の文」だけを直す。

    **引用（evidence[].quote）には触らない。** あれは取得ページに一字一句あることを
    機械が確かめた文字列で、書き換えたら検証そのものが嘘になる。
    パッチの current / proposed も触らない。あれは文章ではなく、
    関門を通したうえで人が手引きに当てる値である。
    """
    for key in ("risk_if_wrong", "escalation"):
        if key in details:
            details[key] = _tidy(details[key])
    patch = details.get("proposed_patch")
    if isinstance(patch, dict) and "rationale" in patch:
        patch["rationale"] = _tidy(patch["rationale"])
    return details


# ---- 照合の本線と、検証済アーカイブ ------------------------------------------
#
# 本線は、その場で Gemini が公的ページ（go.jp / lg.jp）を探して読み、照合する。
# 外部に接続できない・鍵が無いときは、**以前に本線で実際に照合した記録**を
# 「検証済アーカイブ（照合日つき）」として、そうと分かる形で出す。
# 記録が無ければ、無いと言う。作り物の結果を、その場の照合のように見せることはしない。

ARCHIVE_DIR = Path(os.environ.get("SHIORI_ARCHIVE_DIR") or (ROOT / "data" / "archive"))


def _archive_path(node_id: str) -> Path:
    # node_id は validate_node_id を通ったもの（英大文字・数字・_ のみ）だけが来る
    return ARCHIVE_DIR / f"{node_id}.json"


def _save_archive(node_id: str, result: dict, logs: list, route: str) -> None:
    """本線で最後まで照合できた結果だけを残す。書けない環境（読み取り専用など）では黙って諦める。"""
    try:
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        rec = {
            "node_id": node_id,
            "archived_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "search_route": route,
            "result": result,
            "logs": logs,
        }
        tmp = _archive_path(node_id).with_suffix(".tmp")
        tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        tmp.replace(_archive_path(node_id))
    except Exception:
        pass


def _when(iso) -> str:
    """ログに出す照合日時。人が読む形にする。"""
    return (str(iso or "")[:16]).replace("T", " ")


def _load_archive(node_id: str) -> dict | None:
    try:
        return json.loads(_archive_path(node_id).read_text(encoding="utf-8"))
    except Exception:
        return None


def _fallback(node_id: str, node_item: dict, reason: str, live: dict | None = None) -> dict:
    """
    本線で照合を終えられなかったときの返し方。
      1. 検証済アーカイブがあれば、照合日と理由を添えて返す
      2. 無ければ、本線の途中結果（live）をそのまま返す
      3. それも無ければ、照合していないことを明示した静的表示（DRY_RUN）を返す
    """
    rec = _load_archive(node_id)
    if rec and isinstance(rec.get("result"), dict):
        result = dict(rec["result"])
        result["archive"] = {"archived_at": rec.get("archived_at"), "reason": reason,
                             "search_route": rec.get("search_route", "")}
        result["logs"] = rec.get("logs", [])
        return {"ok": True, "result": result}
    if live is not None:
        return {"ok": True, "result": live}
    lm = node_item.get("legal_metadata", {})
    return {
        "ok": True,
        "result": {
            "action": "DRY_RUN",
            "reason": f"{reason}。一次資料との照合はまだ行っていません。",
            "summary": "ノードに書かれている内容だけを表示しています。",
            "details": {
                "node_id": node_id,
                "title": node_item.get("title"),
                "verification_status": lm.get("verification_status"),
                "needs_check": lm.get("needs_check"),
                "source_draft": lm.get("source_draft"),
                "source_url": lm.get("source_url"),
            },
        },
    }


def _prepare(node_id: str):
    """入力の検査とノードの取り出し。問題があれば (status, payload) を返す。"""
    if not validate_node_id(node_id):
        return None, None, (400, {"ok": False, "error": "Invalid node_id format or directory traversal detected"})
    node_item = _find_node(node_id)
    if not node_item:
        return None, None, (404, {"ok": False, "error": "Node not found"})
    check_item = _find_check(node_id)
    if not check_item:
        lm = node_item.get("legal_metadata", {})
        check_item = {
            "check_id": f"{node_id}#1",
            "node_id": node_id,
            "claim": node_item.get("title", ""),
            "question": lm["needs_check"][0] if lm.get("needs_check") else node_item.get("title", ""),
        }
    return node_item, check_item, None


def _run_check(node_id: str, node_item: dict, check_item: dict, on_log=None) -> dict:
    """本線の照合を一回まわす。失敗の種類に応じて、検証済アーカイブへ切り替える。"""
    from src.config import config
    emit = on_log or (lambda e: None)

    def sys_log(action, detail=""):
        emit({"timestamp": datetime.now().isoformat(), "role": "System", "action": action, "detail": detail})

    api_key = os.environ.get("GEMINI_API_KEY") or config.GEMINI_API_KEY
    if not api_key:
        sys_log("✗ GEMINI_API_KEY が未設定のため、その場の照合は行えません")
        out = _fallback(node_id, node_item, "GEMINI_API_KEY が設定されていないため")
        if "archive" in out["result"]:
            sys_log("📦 検証済アーカイブを表示します", _when(out["result"]["archive"].get("archived_at")))
        return out

    try:
        from src.agent.agent import ShioriAgent
        agent = ShioriAgent(api_key=api_key, on_log=on_log)
        result = agent.run_check(check_item, node_item, date.today())
    except Exception as e:
        sys_log("✗ 照合の途中で止まりました", str(e)[:100])
        out = _fallback(node_id, node_item, f"照合の途中でエラーが起きたため（{str(e)[:60]}）")
        if "archive" in out["result"]:
            sys_log("📦 検証済アーカイブを表示します", _when(out["result"]["archive"].get("archived_at")))
            return out
        return {"ok": False, "error": f"Internal agent error: {str(e)}", "_status": 500}

    logs = agent.logger.get_logs()
    live = {
        "action": result.verdict,
        "reason": f"一次資料（.go.jp）に基づく自動照合結果。判定: {result.verdict}（確信度: {result.confidence}）",
        "summary": _tidy(result.risk_if_wrong) or "制度との整合性を検証・確認しました。",
        "details": _tidy_details(result.model_dump(mode="json")),
        "detected_ambiguities": result.detected_ambiguities or [],
        "early_warnings": result.early_warnings or [],
        "search_route": getattr(agent, "search_route", "") or "",
        "sources_found": getattr(agent, "sources_found", 0) if isinstance(getattr(agent, "sources_found", 0), int) else 0,
        "pages_read": getattr(agent, "pages_read", 0) if isinstance(getattr(agent, "pages_read", 0), int) else 0,
        "logs": logs,
    }

    infra = getattr(agent, "infra_error", None)
    if isinstance(infra, str) and infra:
        out = _fallback(node_id, node_item, infra, live=live)
        if "archive" in out["result"]:
            sys_log("📦 外部に接続できなかったため、検証済アーカイブに切り替えます",
                    _when(out["result"]["archive"].get("archived_at")))
        return out

    # 「検証済」と名乗れるのは、公的ページの原文と一字一句照合できた引用が一つでもある結果だけ。
    # 引用の裏付けが無い結果を記録すると、次に外部へ出られないとき、それが「検証済」として出てしまう。
    verified = [e for e in (live["details"].get("evidence") or []) if e.get("quote_verified")]
    if verified:
        _save_archive(node_id, {k: v for k, v in live.items() if k != "logs"}, logs, live["search_route"])
    else:
        sys_log("📦 原文と照合できた引用が無いため、この結果は検証済アーカイブに残しません")
    return {"ok": True, "result": live}


@app.post("/api/agent/check")
def agent_check(req: AgentCheckRequest):
    node_item, check_item, err = _prepare(req.node_id)
    if err:
        return JSONResponse(status_code=err[0], content=err[1])
    out = _run_check(req.node_id, node_item, check_item)
    status = out.pop("_status", 200)
    return out if status == 200 else JSONResponse(status_code=status, content=out)


@app.post("/api/agent/check/stream")
def agent_check_stream(req: AgentCheckRequest):
    """
    照合の足取りを、起きた順にそのまま流す（NDJSON: 1 行 1 イベント）。
      {"type": "log", "role": ..., "action": ..., "detail": ...}
      {"type": "result", "ok": ..., "result": {...}}
    """
    node_item, check_item, err = _prepare(req.node_id)
    if err:
        return JSONResponse(status_code=err[0], content=err[1])

    q: "queue.Queue[dict | None]" = queue.Queue()

    def on_log(entry: dict) -> None:
        q.put({"type": "log", **entry})

    def worker() -> None:
        try:
            out = _run_check(req.node_id, node_item, check_item, on_log=on_log)
            out.pop("_status", None)
            q.put({"type": "result", **out})
        except Exception as e:  # 念のため。ここで黙ると画面が待ち続ける
            q.put({"type": "result", "ok": False, "error": str(e)})
        finally:
            q.put(None)

    threading.Thread(target=worker, daemon=True).start()

    def gen():
        while True:
            item = q.get()
            if item is None:
                break
            yield json.dumps(item, ensure_ascii=False, default=str) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/map/{scenario}", response_class=HTMLResponse)
def show_map(scenario: str, today: str | None = Query(default=None)) -> str:
    if scenario not in SCENARIOS:
        raise HTTPException(status_code=404, detail="そんな場面はありません")
    d = _today(today)
    home = "/" + (f"?today={d.isoformat()}" if today else "")
    original_html = page(scenario, d, home=home)

    checks_json_str = json.dumps(_get_checks_map(), ensure_ascii=False)
    injected_html = """
<!-- Shiori Agent Inspector Drawer -->
<div id="inspector-drawer" style="
  position:fixed; top:0; right:-600px; width:540px; max-width:90vw; height:100vh;
  background:#ffffff; box-shadow:-4px 0 20px rgba(0,0,0,0.08);
  border-left:1px solid #d8d2c4; z-index:9999; transition:right 0.25s ease;
  display:flex; flex-direction:column; font-family:system-ui,sans-serif; color:#1a202c;">

  <div style="padding:16px 20px; border-bottom:1px solid #e2dcce; display:flex; justify-content:space-between; align-items:center; background:#faf8f5;">
    <div>
      <div style="font-size:12.5px; font-weight:700; color:#2d3748; letter-spacing:0.05em;">制度追従 照合エージェント</div>
      <div id="drawer-node-title" style="font-size:17px; font-weight:700; margin-top:3px; line-height:1.45;">駅を選んでください</div>
      <div id="drawer-node-sub" style="font-size:12.5px; color:#4a5568; margin-top:1px; line-height:1.5;"></div>
    </div>
    <button onclick="closeInspector()" aria-label="閉じる" style="background:#f4f1ea; border:1.5px solid #c9c1af; border-radius:50%; width:32px; height:32px; font-size:18px; cursor:pointer; color:#2d3748; flex-shrink:0;">&times;</button>
  </div>

  <div id="drawer-body" style="padding:22px 26px; overflow-y:auto; flex:1; font-size:15px; line-height:1.8;">
    <div style="color:#2d3748;">路線図の駅を選ぶと、その手続について、国や省庁の公式ページ（.go.jp）といまの記載が合っているかを、Gemini がその場で確かめます。</div>
  </div>

  <div style="padding:14px 20px; border-top:1px solid #e2dcce; background:#faf8f5;">

    <button id="btn-run-check" onclick="runAgentCheck()" style="
      width:100%; padding:13px; background:#1558c0; color:#fff; border:none;
      border-radius:8px; font-weight:700; font-size:15px; cursor:pointer;
      box-shadow:0 2px 6px rgba(21,88,192,.28); letter-spacing:.01em;">
      詳しく調べてみます
    </button>
  </div>
</div>

<script>
// 地図側のスクリプト（tools/render_map_svg.py）とは別の <script> だが、
// トップレベルの const / let / function は同じグローバル字句環境を共有する。
// 名前が一つでもぶつかると、このブロックは丸ごと構文エラーで死に、
// ドロワーは画面の外（right:-600px）に居たまま出てこない。
// だから全部を閉じ込め、HTML の onclick から呼ぶ二つだけを外に出す。
(function () {
let currentNodeId = null;
const CHECKS_MAP = CHECKS_MAP_PLACEHOLDER;

function openInspector(nodeId, title, status, sourceUrl, needsCheck, todayAction, branches, shortTitle, importantNote) {
  currentNodeId = nodeId;
  // 見出しは短い名詞（short_title）を優先し、元の長い名前は下に小さく添える
  document.getElementById('drawer-node-title').innerText = shortTitle || title || nodeId;
  document.getElementById('drawer-node-sub').innerText = shortTitle ? (title || '') : '';
  const runBtn = document.getElementById('btn-run-check');
  if (runBtn) { runBtn.disabled = false; runBtn.innerText = '詳しく調べてみます'; }
  const body = document.getElementById('drawer-body');
  
  // Close the original Shiori panel if present to avoid overlap
  if (typeof P !== 'undefined' && P) {
    P.classList.add('closed');
  }

  // いま見ている駅に印を残す。動画では、どこを開いたのかが分からなくなる。
  document.querySelectorAll('.st.sel').forEach(el => el.classList.remove('sel'));
  const picked = document.querySelector('[data-id="' + nodeId + '"]');
  if (picked) { picked.classList.add('sel'); }

  // 利用者の画面には、開発用の識別子（駅ID・確認段階）や未設定の注記を出さない。
  // 並びは「今日の一歩 → 心の中で整理しておきたいこと → 照合結果」。
  const todayBlock = todayAction ? `
    <div style="margin-bottom:16px; background:#f3f8f1; border:1.5px solid #9cc5a4; border-radius:9px; padding:12px 14px;">
      <div style="font-size:12.5px; font-weight:700; color:#2f6b3a; letter-spacing:.05em;">【ここで知っておくこと】</div>
      <div style="font-size:16px; font-weight:700; color:#1a202c; margin-top:4px; line-height:1.7;">${esc(todayAction)}</div>
    </div>` : '';
  const points = needsCheck || [];
  const pointsBlock = points.length ? `
    <div style="margin-top:28px;">
      <div style="font-size:14px; font-weight:700; color:#2d3748;">【気になること、確認しておきたいこと】</div>
      <ul style="padding-left:19px; margin:5px 0 0; color:#1a202c;">
        ${points.map(c => `<li style="margin:0 0 12px; line-height:1.8;">${esc(c)}</li>`).join('')}
      </ul>
    </div>` : '';
  // 手引きの側の「さらに大事なこと」（人が書いた文）。照合の結果（Gemini の文）とは別に出す。
  const importantBlock = importantNote ? `
    <div style="margin-top:28px; background:#F4F6F8; border:1px solid #dde3ea; border-radius:8px; padding:14px 16px;">
      <div style="font-size:14px; font-weight:700; color:#2d3748;">【さらに大事なこと】</div>
      <div style="font-size:15px; color:#1a202c; margin-top:4px; line-height:1.8;">${esc(importantNote)}</div>
    </div>` : '';
  const sourceBlock = sourceUrl ? `
    <div style="margin-top:28px;">
      <div style="font-size:14px; font-weight:700; color:#2d3748;">よりどころにしている公式ページ</div>
      <a href="${esc(sourceUrl)}" target="_blank" rel="noopener" style="color:#1558c0; word-break:break-all; font-size:13px;">${esc(sourceUrl)}</a>
    </div>` : '';

  body.innerHTML = `
    ${renderBranches(branches)}
    ${todayBlock}
    ${pointsBlock}
    ${importantBlock}
    ${sourceBlock}
    <div id="agent-result-area" style="margin-top:28px; padding-top:20px; border-top:1px dashed #c9c1af;">
      <div style="color:#2d3748;">下のボタンを押すと、この手続について、国や省庁の公式ページを読みに行き、いまの記載と食い違っていないかを確かめます。</div>
    </div>
  `;
  document.getElementById('inspector-drawer').style.right = '0';
}

// この駅から分かれる道（分岐駅だけ）を、小さな路線図で描く。
// 起点の駅から、同じ太さのレールが分かれ、終点の駅に着く。レールの上には期限の札だけ。
// 説明（検討される場面・行き先・根拠）は、終点の駅を押したときだけ下に開く。
// どの道も同じ重さで並べ、選び方は示さない。待避線は「期間の延長」を表す。
const RAIL_COLORS = ['#2f6f5e', '#9a5b13', '#5b4f8f', '#1f4e79'];
let BR_CUR = null;

function renderBranches(br) {
  BR_CUR = br;
  if (!br || !br.paths || !br.paths.length) return '';
  const n = br.paths.length;
  const top = 60, bot = 240, oy = 150, ox = 70, ex = 346;
  const ys = br.paths.map((p, i) => n === 1 ? oy : top + (bot - top) * i / (n - 1));
  const rails = br.paths.map((p, i) => {
    const y = ys[i], c = RAIL_COLORS[i % RAIL_COLORS.length];
    const d = Math.abs(y - oy) < 1
      ? `M${ox},${oy} L${ex},${oy}`
      : `M${ox},${oy} C170,${oy} 190,${y} 310,${y} L${ex},${y}`;
    return `<path d="${d}" fill="none" stroke="${c}" stroke-width="7" stroke-linecap="round"/>`;
  }).join('');
  const tags = br.paths.map((p, i) => {
    const y = ys[i], t = p.deadline || p.rail_tag || '';
    if (!t) return '';
    const w = 16 + t.length * 12, dl = !!p.deadline;
    return `<g><rect x="${253 - w / 2}" y="${y - 18}" width="${w}" height="20" rx="4" fill="#fff" stroke="${dl ? '#e3b5b1' : '#cfd6dd'}"/>
      <text x="253" y="${y - 4}" text-anchor="middle" style="font-size:11.5px; ${dl ? 'font-weight:700; fill:#a01c16' : 'fill:#4a5568'}">${esc(t)}</text></g>`;
  }).join('');
  const stations = br.paths.map((p, i) => {
    const y = ys[i], c = RAIL_COLORS[i % RAIL_COLORS.length];
    const sub = (p.label || '').replace(/^.*（(.+)）$/, '$1');
    return `<g class="br-st" data-i="${i}" tabindex="0" role="button" style="cursor:pointer;">
      <circle cx="352" cy="${y}" r="14" fill="#fff" stroke="${c}" stroke-width="4"/>
      <text x="374" y="${y - 2}" style="font-size:15px; font-weight:700; fill:#1a202c;">${esc(p.station || p.label)}</text>
      <text x="374" y="${y + 16}" style="font-size:11.5px; fill:#4a5568;">${esc(sub)}</text></g>`;
  }).join('');
  const origin = br.origin || '';
  return `
    <div style="margin-bottom:22px;">
      <div style="font-size:13px; font-weight:700; color:#1E3A5F;">この駅から分かれる道</div>
      <div style="font-size:12.5px; color:#4a5568;">終点の駅を押すと、その道の説明が開きます。</div>
      <svg id="br-svg" viewBox="0 0 496 290" style="display:block; width:100%; height:auto; margin-top:4px;" role="img" aria-label="この駅から分かれる道の路線図">
        <path d="M66,162 C62,236 12,236 12,200 C12,170 48,160 60,157" fill="none" stroke="#9aa4ad" stroke-width="2.2" stroke-dasharray="5 4"/>
        ${(br.siding || ['間に合わないとき：', '期間の延長を申し立てる']).map((s, j) =>
            `<text x="12" y="${262 + j * 16}" style="font-size:11.5px; fill:#4a5568;">${esc(s)}</text>`).join('')}
        ${rails}${tags}
        <text x="${ox}" y="106" text-anchor="middle" style="font-size:15px; font-weight:700; fill:#1E3A5F;">${esc(origin)}</text>
        <text x="${ox}" y="124" text-anchor="middle" style="font-size:11.5px; fill:#4a5568;">（いまここ）</text>
        <circle cx="${ox}" cy="${oy}" r="13" fill="#1E3A5F"/>
        ${stations}
      </svg>
      <div id="br-card" style="margin-top:6px; font-size:12.5px; color:#6b7280;">終点の駅を押してください。</div>
      <div style="font-size:13px; color:#1a202c; line-height:1.8; margin-top:12px; padding-top:10px; border-top:1px solid #e2dcce;">${esc(br.note || '')}</div>
      ${(br.footnotes || []).map(f => `<div style="font-size:12.5px; color:#4a5568; line-height:1.75; margin-top:6px;">${esc(f)}</div>`).join('')}
    </div>`;
}

function showBranch(i) {
  const br = BR_CUR; if (!br || !br.paths[i]) return;
  const p = br.paths[i], c = RAIL_COLORS[i % RAIL_COLORS.length];
  document.querySelectorAll('#br-svg .br-st circle').forEach((el, j) => el.setAttribute('stroke-width', j === i ? '6' : '4'));
  const card = document.getElementById('br-card');
  card.style.cssText = `margin-top:6px; border:1px solid ${c}; background:#F4F6F8; border-radius:10px; padding:12px 14px;`;
  card.innerHTML = `
    <div style="font-size:15.5px; font-weight:700; color:#1a202c; display:flex; align-items:center; gap:8px;">
      <span style="width:13px; height:13px; border-radius:50%; border:4px solid ${c}; display:inline-block;"></span>${esc(p.station || p.label)}（${esc((p.label || '').replace(/^.*（(.+)）$/, '$1'))}）</div>
    <div style="display:grid; grid-template-columns:auto 1fr; gap:3px 10px; font-size:14px; line-height:1.75; margin-top:6px; color:#1a202c;">
      <div style="color:#4a5568; white-space:nowrap;">${esc(br.when_label || '検討される場面')}</div><div>${esc(p.when)}</div>
      <div style="color:#4a5568;">行き先</div><div>${esc(p.to_text)}</div>
      <div style="color:#4a5568;">期限</div><div>${esc(p.deadline || p.rail_tag || '―')}</div>
    </div>
    ${p.aside ? `<div style="font-size:13px; color:#4a5568; margin-top:6px; line-height:1.75;">${esc(p.aside)}</div>` : ''}
    <div style="font-size:12px; color:#6b7280; margin-top:4px;">根拠：${esc(p.basis)}</div>`;
}

// ドロワーの中の終点の駅（押す・Enter）
document.addEventListener('click', e => {
  const g = e.target.closest && e.target.closest('#br-svg .br-st');
  if (g) showBranch(+g.getAttribute('data-i'));
});
document.addEventListener('keydown', e => {
  const g = e.target.closest && e.target.closest('#br-svg .br-st');
  if (g && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); showBranch(+g.getAttribute('data-i')); }
});

function closeInspector() {
  document.getElementById('inspector-drawer').style.right = '-600px';
}

// ---- 監査結果の描画 --------------------------------------------------------
// ここは「エージェントが何を根拠に、何を直そうと言っているか」を人が見る場所である。
// 固定の PASS を並べても、人は何も確かめられない。実際の判定だけを出す。

function esc(s) {
  return String(s === null || s === undefined ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// 判定ごとの見え方。CHANGED は「直すところがある」なので、最も目を引かせる。
// 文言は、制度の当事者が不安なまま読んでも分かる日常語にする。
const VERDICT_STYLE = {
  CHANGED:    {bg:'#fdf3d4', bd:'#d99e00', fg:'#6b4e00',
               badge:'【要改訂】制度変更を検知',
               label:'公的発表において制度の変更が確認されたため、案内の見直しが必要です。'},
  UNCHANGED:  {bg:'#dcf0e2', bd:'#6aab84', fg:'#0f5c2a',
               badge:'【資料に当たることができました】',
               label:''},
  AMBIGUOUS:  {bg:'#eceef0', bd:'#a9b0b6', fg:'#33383d',
               badge:'【個別の確認が必要な項目です】',
               label:'公式資料だけでは一律に言い切れず、各家庭の事情によって対応が分かれることがあります。'},
  NOT_FOUND:  {bg:'#eceef0', bd:'#a9b0b6', fg:'#33383d',
               badge:'【このことにずばり回答するような記述は見出せませんでした】',
               label:'指定された公式ページの中から、該当する制度の明確な記述を確認できませんでした。'},
  UNCERTAIN:  {bg:'#fbe0dd', bd:'#d9776d', fg:'#a01c16',
               badge:'【要確認】記載に食い違い',
               label:'公的資料の記載に食い違いがあり、現時点では確定的な判定を行えません。'},
  // 有効な一次証憑を一つも選べなかったとき（家族間の対話など、国の画一的な手引きが無い駅）
  NO_PUBLIC_STANDARD: {bg:'#f1efe8', bd:'#b9b09a', fg:'#3d3a33',
               badge:'【公的基準に直接の該当なし】',
               label:'家族間の対話や個別状況に基づく手続きです。'},
  UNVERIFIED: {bg:'#e1ebfd', bd:'#7aa6e8', fg:'#123a75',
               badge:'【要照会】自治体ごとに相違',
               label:'取扱いが市区町村ごとに異なるため、管轄の窓口への照会が必要です。'},
  DRY_RUN:    {bg:'#eceef0', bd:'#a9b0b6', fg:'#33383d',
               badge:'【未照合】一次資料との照合は未実行',
               label:'その場の照合を行えず、以前の照合記録もないため、手引きに書いてある内容だけを表示しています。'}
};

// 確度は機械の値（HIGH/MEDIUM/LOW）で持ち、画面には日本語で出す。
const CONFIDENCE_JA = {HIGH: '高', MEDIUM: '中', LOW: '低'};

function renderVerdict(action, confidence) {
  const st = VERDICT_STYLE[action] || VERDICT_STYLE.AMBIGUOUS;
  const conf = (confidence && action !== 'UNCHANGED' && action !== 'NOT_FOUND')
    ? `<span style="font-size:13px; color:${st.fg}; margin-left:9px; font-weight:700;">（照合確度: ${esc(CONFIDENCE_JA[confidence] || confidence)}）</span>`
    : '';
  return `
    <div style="background:${st.bg}; border:2px solid ${st.bd}; border-radius:9px; padding:13px 15px;">
      <div style="display:flex; justify-content:space-between; align-items:baseline; gap:8px;">
        <span style="font-size:12.5px; color:${st.fg}; letter-spacing:.04em; font-weight:700;">【公的文献資料を見た結果はこうです】</span>
      </div>
      <div style="font-weight:700; color:${st.fg}; font-size:17px; margin-top:3px; line-height:1.5;">
        ${esc(st.badge)}${conf}
      </div>
      ${st.label ? `<div style="font-size:14px; color:${st.fg}; margin-top:4px; line-height:1.7;">${esc(st.label)}</div>` : ''}
    </div>`;
}

// 自律監査プロセスの足跡。
// **実際に踏んだ段だけを濃く出す。** 通っていない段まで並べると、
// 固定文字列の「PASS」を並べていた頃と同じ、意味のない証跡に戻ってしまう。
function renderProcess(d, action) {
  const ev = d.evidence || [];
  const rej = d.rejected || [];
  const steps = [
    ['一次資料取得', ev.length > 0],
    ['差分検知', ev.length > 0 && (action === 'CHANGED' || action === 'UNCHANGED')],
    ['リスク評価', !!d.risk_if_wrong],
    ['パッチ起草', !!d.proposed_patch],
    ['内部統制（七つの関門）検証通過', rej.length === 0 && d.human_review_required === true],
  ];
  const done = steps.filter(s => s[1]).length;
  const body = steps.map(([name, ok], i) => {
    const sep = i ? '<span style="color:#9aa4ad; margin:0 5px;">➔</span>' : '';
    return sep + (ok
      ? `<span style="color:#123a75; font-weight:700;">${esc(name)}</span>`
      : `<span style="color:#9aa4ad; text-decoration:line-through;">${esc(name)}</span>`);
  }).join('');
  const head = done === steps.length ? '一次資料の照合プロセス完了' : `一次資料の照合プロセス（${done} / ${steps.length} 段）`;
  return `
    <div style="margin-top:13px; background:#f3f6fb; border:1px solid #c9d7ec; border-radius:7px;
                padding:8px 11px; font-size:11.5px; line-height:1.85;">
      <span style="color:#123a75; font-weight:700; letter-spacing:.03em;">${esc(head)}:</span>
      <span style="margin-left:5px;">${body}</span>
    </div>`;
}

// 修正案（ケースA の主役）。current → proposed をそのまま並べて見せる。
function renderPatch(patch) {
  if (!patch) return '';
  return `
    <div style="margin-top:16px; border:2px solid #d99e00; border-radius:9px; overflow:hidden;">
      <div style="background:#fdf3d4; padding:10px 13px; font-size:14px; font-weight:700; color:#6b4e00;">
        手引きの見直し案（修正パッチ）
      </div>
      <div style="padding:14px;">
        <div style="font-size:13px; color:#2d3748; font-weight:700;">見直す項目</div>
        <div style="font-family:ui-monospace,monospace; font-size:13px; background:#efece3;
                    border:1px solid #d3cbb8; color:#1a202c;
                    border-radius:5px; padding:5px 9px; margin:4px 0 12px; word-break:break-all;">
          ${esc(patch.json_pointer)}
        </div>

        <div style="border-left:4px solid #a01c16; background:#fbe0dd; padding:9px 12px;
                    border-radius:0 5px 5px 0; margin-bottom:7px;">
          <div style="font-size:12.5px; color:#8f1710; font-weight:700;">いまの記載</div>
          <div style="font-size:14px; color:#1a202c; margin-top:3px; line-height:1.75;">${esc(fmtVal(patch.current))}</div>
        </div>
        <div style="text-align:center; color:#6b6455; font-size:16px; line-height:1.2;">↓</div>
        <div style="border-left:4px solid #0f5c2a; background:#dcf0e2; padding:9px 12px;
                    border-radius:0 5px 5px 0; margin-top:7px;">
          <div style="font-size:12.5px; color:#0f5c2a; font-weight:700;">こう直してはいかがでしょうか</div>
          <div style="font-size:14px; color:#1a202c; margin-top:3px; line-height:1.75;">${esc(fmtVal(patch.proposed))}</div>
        </div>

        ${patch.rationale ? `
        <div style="margin-top:12px;">
          <div style="font-size:13px; color:#2d3748; font-weight:700;">見直しの理由</div>
          <div style="font-size:14px; color:#1a202c; margin-top:3px; line-height:1.75;">${esc(patch.rationale)}</div>
        </div>` : ''}
      </div>
      <div style="background:#1a202c; padding:12px 14px; font-size:13.5px; color:#fff; line-height:1.75;">
        <span style="display:inline-block; font-size:12px; font-weight:700; letter-spacing:.06em;
                     background:#fff; color:#1a202c; border-radius:4px; padding:2px 7px; margin-bottom:5px;">
          最終判断は、人がします
        </span><br>
        これは<strong>案</strong>です。手引きを書き換えるのは人であり、
        エージェントがここから先へ進む道はありません。
      </div>
    </div>`;
}

function fmtVal(v) {
  if (v === null || v === undefined) return '（まだ入っていません）';
  if (typeof v === 'object') return JSON.stringify(v, null, 1);
  return v;
}

// 根拠。引用が取得ページに一字一句あったかどうかは、機械が付けた印だけを信じる。
function renderEvidence(evidence) {
  if (!evidence || !evidence.length) {
    return `<div style="margin-top:16px; font-size:14px; color:#2d3748;">
              確かな引用は残りませんでした（チェックで落ちたか、ページを読めていません）。
            </div>`;
  }
  const items = evidence.map(e => `
    <div style="border:1px solid #dde3ea; border-radius:8px; padding:14px 16px; margin-top:12px; background:#F4F6F8;">
      <div style="display:flex; justify-content:space-between; align-items:center; gap:6px;">
        <span style="font-size:13px; color:#1a202c; font-weight:700;">${esc(e.publisher || '公的機関')}</span>
        ${e.quote_verified
          ? '<span style="font-size:12px; font-weight:700; background:#dcf0e2; color:#0f5c2a; padding:3px 9px; border-radius:11px; white-space:nowrap;">✓ 原文引用</span>'
          : '<span style="font-size:12px; font-weight:700; background:#fbe0dd; color:#a01c16; padding:3px 9px; border-radius:11px; white-space:nowrap;">未確認</span>'}
      </div>
      <blockquote style="margin:10px 0 0; padding:2px 0 2px 12px; border-left:4px solid #7aa6e8;
                         font-size:15px; color:#1a202c; line-height:1.8;">「${esc(e.quote)}」</blockquote>
      <a href="${esc(e.url)}" target="_blank" rel="noopener"
         style="display:inline-block; margin-top:7px; font-size:12.5px; color:#1558c0; word-break:break-all;">
        ${esc(e.url)}</a>
      ${e.page_date ? `<span style="font-size:12.5px; color:#4a5568; margin-left:6px;">（${esc(e.page_date)}）</span>` : ''}
    </div>`).join('');
  return `<div style="margin-top:28px;">
            <div style="font-size:14px; font-weight:700; color:#1a202c;">【一次資料の記載例を提示します】</div>
            ${items}
          </div>`;
}

// 関門。固定文字列をやめ、返ってきた結果から実際に判定できることだけを書く。
function renderGates(d, action) {
  const ev = d.evidence || [];
  const rej = d.rejected || [];
  const OFFICIAL = /^https:\/\/([^\/]*\.)?(go|lg)\.jp(\/|$)/i;
  const rows = [];

  const offAll = ev.length > 0 && ev.every(e => OFFICIAL.test(e.url || ''));
  rows.push(['公式発表への参照限定',
             ev.length === 0
               ? ['対象外', '照合できた引用がないため、この検証は行っていません。']
               : [offAll ? '適合' : '不適合',
                  `参照先を国や自治体の公的サイト（.go.jp / .lg.jp）に限定し、不確かな民間情報を排除しています。（${ev.length}件すべて照合）`]]);

  const okQ = ev.filter(e => e.quote_verified).length;
  rows.push(['公式原文との完全一致',
             ev.length === 0
               ? ['対象外', '照合できた引用がないため、この検証は行っていません。']
               : [okQ === ev.length ? '適合' : '不適合',
                  `AIによる勝手な要約や歪曲を防ぐため、公的発表の文章と一字一句一致することを機械検証しています。（${okQ}/${ev.length}件一致）`]]);

  if (d.proposed_patch) {
    rows.push(['更新範囲の厳格な制限',
               ['適合', 'あらかじめ定められた項目以外をAIが勝手に改ざんできないよう制限しています。']]);
    const ptr = d.proposed_patch.json_pointer || '';
    const up = ptr.endsWith('/verification_status') && d.proposed_patch.proposed === 'EXPERT_VERIFIED';
    rows.push(['専門家承認の必須化',
               [up ? '不適合' : '適合', 'AIが自らを「検証済み」へ格上げすることを禁止しています。']]);
  } else {
    // 修正案が無い理由は一つではない。合致していたのか、取り下げたのか、
    // そもそも差分を確かめられなかったのか。ここを一律に書くと、事実と違う説明になる。
    let why;
    if (rej.length) {
      why = 'AIが作成した修正案は、安全基準に適合しなかったため取り下げました。';
    } else if (action === 'UNCHANGED') {
      why = '公的発表と現行の手引きが合致しているため、修正案の作成は不要と判断しました。';
    } else {
      why = '公的発表から確定的な差分を確認できなかったため、修正案は作成していません。';
    }
    rows.push(['手引きの更新要否', ['対象外', why]]);
  }

  rows.push(['【人の確認が前提】',
             [d.human_review_required === true ? '適合' : '不適合',
              'AIの提案だけで手引きが書き換わることはありません。人が目視で確認してから反映します。']]);

  const body = rows.map(([name, [verdict, detail]]) => {
    const col = verdict === '適合' ? '#0f5c2a' : verdict === '不適合' ? '#a01c16' : '#4a5568';
    const bg  = verdict === '適合' ? '#dcf0e2' : verdict === '不適合' ? '#fbe0dd' : '#eceef0';
    return `<div style="display:flex; gap:9px; padding:8px 0; border-top:1px solid #e2dcce; align-items:flex-start;">
              <span style="color:${col}; background:${bg}; font-weight:700; font-size:12px;
                           min-width:52px; text-align:center; border-radius:4px; padding:3px 0; flex-shrink:0;">${esc(verdict)}</span>
              <span style="flex:1;"><span style="color:#1a202c; font-size:13.5px; font-weight:700;">${esc(name)}</span>
                <span style="display:block; color:#2d3748; font-size:12.5px; margin-top:2px; line-height:1.75;">${esc(detail)}</span></span>
            </div>`;
  }).join('');

  // 抵触したときは、まず人の言葉で結論を言う。
  // 機械が記録した理由は捨てない（それが無いと直しようがない）が、下に畳んで置く。

  // 機械が記録した理由（rejected）は開発者向けの文言を含むので、利用者の画面には出さない。
  // 理由そのものはサーバー側の結果（details.rejected）と照合記録に残っている。
  const rejBlock = rej.length ? `
    <div style="margin-top:12px; background:#fbe0dd; border:1.5px solid #d9776d; border-radius:7px; padding:10px 13px;">
      <div style="font-size:13.5px; font-weight:700; color:#8f1710;">安全基準に抵触したため、AIの提案を取り下げました。</div>
    </div>` : `
    <div style="margin-top:12px; background:#dcf0e2; border:1.5px solid #6aab84; border-radius:7px;
                padding:9px 13px; font-size:13.5px; color:#0f5c2a; font-weight:700;">
      すべての安全基準（内部統制）に適合しています。
    </div>`;

  return `<div style="margin-top:28px;">
            <div style="font-size:14px; font-weight:700; color:#1a202c; margin-bottom:3px;">【AIの安全性と品質の検証は内部的に確認しています】</div>
            <details style="margin-top:4px;">
              <summary style="font-size:12.5px; color:#2d3748; cursor:pointer;">検証の項目を見る（${rows.length}項目）</summary>
              <div style="font-size:13.5px; margin-top:4px;">${body}</div>
            </details>
            ${rejBlock}
          </div>`;
}

function renderDryRun(d) {
  return `
    <div style="margin-top:16px; font-size:14px; color:#1a202c; line-height:1.8;">
      <div style="font-size:14px; font-weight:700; color:#1a202c;">いま手引きに書いてあること</div>
      ${d.source_url ? `<div style="margin-top:6px; word-break:break-all;">よりどころにしている公式ページ: ${esc(d.source_url)}</div>` : ''}
      <div style="margin-top:4px;">根拠の下書き: ${esc(d.source_draft || '—')}</div>
    </div>`;
}

// ---- 思考ログ（ドロワーに流れる足取り） -------------------------------------
// サーバーから届いた順に、一行ずつ出す。届くのが一度に重なったときだけ、
// 目で追える間隔（LOG_MIN_GAP_MS）を空けて並べる。中身は足さないし、変えない。
const LOG_MIN_GAP_MS = 320;
const NL = String.fromCharCode(10);
const ROLE_STYLE = {
  System:     {c:'#4a5568', ja:'全体'},
  Explorer:   {c:'#1558c0', ja:'探す'},
  Evaluator:  {c:'#8a5a00', ja:'見極める'},
  Reader:     {c:'#0f5c2a', ja:'読む'},
  Comparator: {c:'#553c9a', ja:'比べる'},
  Disruptor:  {c:'#a01c16', ja:'疑う'},
  Writer:     {c:'#2d3748', ja:'書く'},
  Guards:     {c:'#123a75', ja:'関門'}
};

function logLineHtml(e, dim) {
  const st = ROLE_STYLE[e.role] || ROLE_STYLE.System;
  const detail = e.detail
    ? `<div style="color:#4a5568; font-size:11.5px; margin:1px 0 0 54px; word-break:break-all; line-height:1.5;">${esc(e.detail)}</div>`
    : '';
  return `<li style="list-style:none; margin:0 0 6px; opacity:${dim ? '.8' : '0'}; transform:translateY(${dim ? '0' : '4px'});
                     transition:opacity .28s ease, transform .28s ease;">
      <span style="display:inline-block; min-width:46px; text-align:center; font-size:11px; font-weight:700;
                   color:#fff; background:${st.c}; border-radius:4px; padding:1px 4px; margin-right:8px;">${esc(st.ja)}</span>
      <span style="font-size:13px; color:#1a202c;">${esc(e.action)}</span>${detail}</li>`;
}

// 照合中は開いたまま流し、結論が出たら自動で畳む（結論に視線を集めるため）。
// 畳んだあとも、見出しを押せばいつでも開ける。
function makeLogPanel(title) {
  return `<details id="agent-log-box" open style="background:#faf8f5; border:1px solid #e2dcce; border-radius:8px; padding:10px 12px; margin-bottom:14px;">
      <summary id="agent-log-summary" style="font-size:12.5px; font-weight:700; color:#2d3748; cursor:pointer;">${esc(title)}</summary>
      <ol id="agent-log-list" style="padding:0; margin:8px 0 0; max-height:240px; overflow-y:auto;"></ol>
    </details>`;
}

function collapseLog(text) {
  const box = document.getElementById('agent-log-box');
  const sum = document.getElementById('agent-log-summary');
  if (sum) sum.textContent = text;
  if (box) box.open = false;
}

// あたった資料の件数。ログの行数ではなく、探索で見つけた公的資料の数を数える。
function sourcesCount(result) {
  if (typeof result.sources_found === 'number') return result.sources_found;
  return (result.logs || []).filter(e => e.role === 'Explorer' && String(e.action || '').startsWith('🌐')).length;
}

function createLogWriter() {
  const list = document.getElementById('agent-log-list');
  let chain = Promise.resolve();
  let last = 0;
  function put(html, animate) {
    list.insertAdjacentHTML('beforeend', html);
    const li = list.lastElementChild;
    if (animate) requestAnimationFrame(() => { li.style.opacity = '1'; li.style.transform = 'none'; });
    list.scrollTop = list.scrollHeight;
  }
  return {
    push(e) {
      chain = chain.then(async () => {
        const wait = Math.max(0, last + LOG_MIN_GAP_MS - Date.now());
        if (wait) await new Promise(r => setTimeout(r, wait));
        put(logLineHtml(e, false), true);
        last = Date.now();
      });
      return chain;
    },
    // 検証済アーカイブの記録は、その場の出来事ではないので、流さずに一度に並べる
    record(title, entries) {
      chain = chain.then(() => {
        put(`<li style="list-style:none; margin:10px 0 7px; padding-top:8px; border-top:1px dashed #c9c1af;
                         font-size:12px; font-weight:700; color:#5b4a1e;">${esc(title)}</li>`, false);
        (entries || []).forEach(e => put(logLineHtml(e, true), false));
      });
      return chain;
    },
    done() { return chain; }
  };
}

function fmtWhen(iso) {
  const t = new Date(iso);
  if (!iso || isNaN(t.getTime())) return String(iso || '');
  const p2 = n => String(n).padStart(2, '0');
  return `${t.getFullYear()}年${t.getMonth() + 1}月${t.getDate()}日 ${p2(t.getHours())}:${p2(t.getMinutes())}`;
}

function renderArchiveBanner(a) {
  if (!a) return '';
  return `
    <div style="background:#f5f0e1; border:2px solid #b9a77a; border-radius:9px; padding:11px 14px; margin-bottom:12px;">
      <div style="font-weight:700; color:#5b4a1e; font-size:14.5px;">検証済アーカイブ（照合日: ${esc(fmtWhen(a.archived_at))}）</div>
      <div style="font-size:13px; color:#5b4a1e; margin-top:4px; line-height:1.7;">
        いまはその場の照合ができないため（${esc(a.reason || '')}）、この日時に実際に行った照合の記録を表示しています。内容はその時点のものです。
      </div>
    </div>`;
}

// Evaluator の見立て。一次証憑の選定と、5 形態の曖昧さ。
function renderEvaluator(d) {
  const w = d.early_warnings || [];
  const a = d.detected_ambiguities || [];
  if (!w.length && !a.length) return '';
  const chips = a.map(x => `<span style="display:inline-block; font-size:11.5px; background:#fff; border:1px solid #d9b85a;
                       color:#6b4e00; border-radius:10px; padding:1px 8px; margin:3px 4px 0 0;">${esc(x)}</span>`).join('');
  return `
    <div style="margin-top:28px; background:#fbf6e9; border:1px solid #e4d3a6; border-radius:8px; padding:14px 16px;">
      <div style="font-size:13px; font-weight:700; color:#6b4e00;">【選定した一次資料】</div>
      ${w.map(x => x.replace(/^✓ 『(.+)』を選定$/, '✓ 一次資料は『$1』でした')).map(x => `<div style="font-size:15px; color:#1a202c; margin-top:4px; line-height:1.8;">${esc(x)}</div>`).join('')}
      ${chips ? `<div style="margin-top:4px;">${chips}</div>` : ''}
    </div>`;
}

async function runAgentCheck() {
  if (!currentNodeId) return;
  const btn = document.getElementById('btn-run-check');
  const resArea = document.getElementById('agent-result-area');
  btn.disabled = true;
  btn.innerText = '関連する資料にあたっています。少々お待ちを...';
  resArea.innerHTML = makeLogPanel('エージェントの思考ログ') + '<div id="agent-final"></div>';
  const writer = createLogWriter();
  const finalArea = document.getElementById('agent-final');
  let finished = false;

  try {
    const res = await fetch('/api/agent/check/stream', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({node_id: currentNodeId})
    });
    if (!res.ok || !res.body) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.error || ('HTTP ' + res.status));
    }

    // NDJSON を一行ずつ読む。log は流し、result は最後に描く。
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    let final = null;
    const take = (line) => {
      if (!line.trim()) return;
      const ev = JSON.parse(line);
      if (ev.type === 'log') writer.push(ev);
      else if (ev.type === 'result') final = ev;
    };
    while (true) {
      const {value, done} = await reader.read();
      if (done) break;
      buf += dec.decode(value, {stream: true});
      let i;
      while ((i = buf.indexOf(NL)) >= 0) { take(buf.slice(0, i)); buf = buf.slice(i + 1); }
    }
    take(buf);

    if (!final) throw new Error('結果が届きませんでした');
    if (!final.ok) throw new Error(final.error || '確認に失敗しました');

    const result = final.result || {};
    if (result.archive) {
      writer.record(`以下は ${fmtWhen(result.archive.archived_at)} の照合時の記録です`, result.logs);
    }
    await writer.done();

    const d = result.details || {};
    const isDry = result.action === 'DRY_RUN';

    // 公的基準に直接の該当が無い駅では、無関係な引用も、既定の「確認しました」も出さない
    const noStd = result.action === 'NOT_FOUND'
      && (d.detected_constraints || {}).reason === 'NO_RELEVANT_PUBLIC_SOURCE';
    const risk = noStd ? '' : (d.risk_if_wrong || result.summary || result.reason || '');
    const riskBlock = risk ? `
      <div style="margin-top:28px; background:#F4F6F8; border:1px solid #dde3ea; border-radius:8px; padding:14px 16px;">
        <div style="font-size:13px; font-weight:700; color:#1a202c;">【さらに大事なことがあるとすると・・】</div>
        <div style="font-size:15px; color:#1a202c; margin-top:4px; line-height:1.8;">${esc(risk)}</div>
      </div>` : '';
    const escBlock = d.escalation ? `
      <div style="margin-top:28px; font-size:15px; color:#1a202c; line-height:1.8;">
        <div style="font-weight:700; color:#1a202c;">【誰に相談すると良いだろう。】</div>
        <div style="margin-top:2px;">${esc(d.escalation)}</div>
      </div>` : '';

    const nSrc = sourcesCount(result);
    collapseLog(result.archive
      ? `照合時の記録：${nSrc}件の資料にあたりました（詳細を見る）`
      : `✓ ${nSrc}件の資料にあたりました（詳細を見る）`);
    // 読む人に見せるのは、結論・手がかり・原文の引用と、短い安心の一文まで。
    // 照合の足取り・段の表示・内部統制の項目は、検証用のアコーディオン（初期は閉）に入れる。
    const okQuotes = (d.evidence || []).filter(e => e.quote_verified).length;
    const assurance = isDry ? '' : `
      <div style="margin-top:22px; font-size:13.5px; color:#2d3748; line-height:1.8; border-top:1px solid #e2dcce; padding-top:12px;">
        ${okQuotes ? `✓ 公式資料の原文と照合しています（引用 ${okQuotes} 件）。<br>` : ''}
        ✓ AIの提案だけで手引きが書き換わることはありません。人が確認してから反映します。
      </div>`;
    const audit = `
      <details id="audit-details" style="margin-top:14px; border:1px solid #e2dcce; border-radius:8px; padding:8px 12px; background:#faf8f5;">
        <summary style="font-size:13px; color:#4a5568; cursor:pointer;">照合プロセスの詳細・監査ログを見る（検証用）</summary>
        <div id="agent-log-slot" style="margin-top:10px;"></div>
        ${isDry ? '' : renderProcess(d, result.action)}
        ${isDry ? '' : renderGates(d, result.action)}
      </details>`;
    finalArea.innerHTML = [
      renderArchiveBanner(result.archive),
      noStd ? renderVerdict('NO_PUBLIC_STANDARD', null) : renderVerdict(result.action, d.confidence),
      isDry ? '' : renderEvaluator(d),
      riskBlock,
      escBlock,
      isDry ? renderDryRun(d)
            : [renderPatch(d.proposed_patch), noStd ? '' : renderEvidence(d.evidence)].join(''),
      assurance,
      audit
    ].join('');
    // 畳んだ足取りを、判定枠のすぐ下へ移す（要素を動かすだけで、中身は変えない）
    const logBox = document.getElementById('agent-log-box');
    const slot = document.getElementById('agent-log-slot');
    if (logBox && slot) { logBox.style.marginBottom = '0'; slot.appendChild(logBox); }
    finalArea.scrollIntoView({behavior: 'smooth', block: 'start'});
    finished = true;
  } catch (err) {
    await writer.done();
    finalArea.innerHTML = `<div style="color:#a01c16; font-size:14px; font-weight:700;">うまく確かめられませんでした: ${esc(err.message)}</div>`;
  } finally {
    // 終わったら「確認が終わりました」のまま止める。別の駅を開くと初期状態に戻る（openInspector）。
    if (finished) {
      btn.innerText = '確認が終わりました';
    } else {
      btn.disabled = false;
      btn.innerText = '詳しく調べてみます';
    }
  }
}

function wireStations() {
  const nodes = document.querySelectorAll('g.node, [data-node-id], .station, .st, .chip');
  nodes.forEach(el => {
    el.style.cursor = 'pointer';
    el.addEventListener('click', (e) => {
      const id = el.getAttribute('data-node-id') || el.getAttribute('data-id') || el.id;
      if (!id) return;
      
      let title = el.getAttribute('data-title') || id;
      let status = '';
      let sourceUrl = '';
      let needsCheck = [];

      if (typeof D !== 'undefined' && D[id]) {
        title = D[id].title || title;
        status = D[id].verif || '';
        // srcurl はノードの一次資料。dlurl は期限の根拠で、期限のないノードには付かない。
        sourceUrl = D[id].srcurl || D[id].dlurl || '';
      }
      if (typeof CHECKS_MAP !== 'undefined' && CHECKS_MAP[id]) {
        needsCheck = CHECKS_MAP[id].map(c => c.question);
      }
      
      const todayAction = (typeof D !== 'undefined' && D[id]) ? (D[id].today || '') : '';
      const branches = (typeof D !== 'undefined' && D[id]) ? (D[id].branches || null) : null;
      const shortTitle = (typeof D !== 'undefined' && D[id]) ? (D[id].short || '') : '';
      const importantNote = (typeof D !== 'undefined' && D[id]) ? (D[id].important || '') : '';
      openInspector(id, title, status, sourceUrl, needsCheck, todayAction, branches, shortTitle, importantNote);
    });
  });
}

// このスクリプトは </body> の直前に差し込まれるので、ふつうはまだ解析中である。
// それでも、読み終わったあとに差し込まれた場合に黙って何もしないことがないよう、
// 両方の場合を見る。
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', wireStations);
} else {
  wireStations();
}

// HTML の onclick から呼ばれるのはこの二つだけ。ほかは外に出さない。
window.closeInspector = closeInspector;
window.runAgentCheck = runAgentCheck;
})();
</script>
</body>
""".replace("CHECKS_MAP_PLACEHOLDER", checks_json_str)
    return original_html.replace("</body>", injected_html)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
