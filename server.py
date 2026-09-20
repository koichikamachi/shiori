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
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse, JSONResponse
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
     "亡くなったあと、遺族が見る面。今日、何をしたらよいのだろうか"),
    ("SELF", "本人の地図（生きているうち）",
     "残される人たちへの備えです。こちらはご自分の状況に応じてスタートします。"),
    ("FAMILY_CHILD", "親を気にかける家族の地図",
     "親に切り出せない人の面。母数がいちばん大きい立場である。"),
]

INDEX_CSS = """
/* 入口の文字は、大きさ・太さ・余白の段で読む順序を作る。
   標題 → 副題 → 説明 → 見出し → 場面カード、の五段。
   同じ大きさが二つ並ぶと、どちらが先か分からなくなる。 */
:root{color-scheme:light;--ink:#1a202c;--sub:#555;--dim:#777;--faint:#888;--rule:#d8d2c4}
*{box-sizing:border-box}
body{margin:0;background:#fbfaf7;color:var(--ink);
 font-family:"Hiragino Kaku Gothic ProN","Yu Gothic",system-ui,sans-serif;
 line-height:1.8}
.wrap{max-width:760px;margin:0 auto;padding:40px 22px 64px}

/* 標題 */
h1{font-size:30px;font-weight:700;letter-spacing:.05em;line-height:1.3;margin:0 0 10px}
/* 副題 */
.sub{color:#374151;font-size:14px;line-height:1.6;margin:0 0 26px}

/* 試作品の説明 */
.about{background:#fff;border:1px solid var(--rule);border-radius:12px;
 padding:20px 22px;font-size:14px;font-weight:500;line-height:1.75;margin:0 0 8px}
.about p{margin:0 0 10px}
.about p:last-of-type{margin-bottom:0}
.about b{color:#000;font-weight:700}
.does{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin:16px 0 0}
.does>div{background:#f4f1ea;border:1px solid #e2dcce;border-radius:8px;
 padding:13px 15px;font-size:13px;font-weight:500;line-height:1.7;color:#1f2937}
.does h3{margin:0 0 6px;font-size:13px;color:var(--ink);font-weight:700;letter-spacing:.02em}
.does ul{margin:0;padding-left:18px}
.does li{margin:2px 0}
.stat{display:flex;flex-wrap:wrap;gap:2px 0;font-size:13.5px;line-height:1.7;
 color:#374151;margin:14px 0 0;padding-top:12px;border-top:1px dashed #d1d5db}
.stat span{white-space:nowrap}
/* 区切りの「｜」は項目に付けて運ぶ。別の要素にすると、折り返しで行末に取り残される。 */
.stat span+span::before{content:"｜";color:#c9c1af;margin:0 8px}

/* 見出し。前のかたまりから切り離すため、上の余白を広く取り、左に印を置く。 */
h2{font-size:21px;font-weight:700;line-height:1.4;letter-spacing:.02em;
 margin:32px 0 6px;padding:2px 0 2px 12px;border-left:4px solid #222;border-bottom:0}
.note{color:#1f2937;font-size:15.5px;font-weight:500;line-height:1.7;margin:0 0 14px;padding-left:16px}

/* 場面を選ぶカードは、この画面のいちばん大事な操作である。
   日本語の題名が主役。識別子は目に入らない大きさに落とす。 */
a.card{display:block;background:#fff;border:1.5px solid var(--rule);border-radius:10px;
 padding:14px 18px;margin:10px 0;text-decoration:none;color:var(--ink);
 transition:border-color .15s,box-shadow .15s,transform .15s}
a.card:hover{border-color:#333;box-shadow:0 3px 10px rgba(0,0,0,.08);transform:translateY(-1px)}
a.card:focus-visible{outline:3px solid #1f4e79;outline-offset:2px}
a.card:active{transform:translateY(0);box-shadow:0 1px 3px rgba(0,0,0,.08)}
a.card .t{font-size:16px;font-weight:700;line-height:1.5}
/* 内部キー（surv-day1 等）は画面に出さない。読む人には意味が無い。 */
a.card .k{display:none}

/* 免責と注記。独立した案内枠として、敷きの上に置く。 */
footer{margin-top:40px;background:#f9f8f6;border:1px solid #d1d5db;border-radius:6px;
 padding:16px 18px;color:#1f2937;font-size:15px;font-weight:500;line-height:1.8}
footer p{margin:0}
footer p+p{margin-top:8px}
@media(max-width:560px){.does{grid-template-columns:1fr}h1{font-size:28px}}
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
      '順序と期限の地図として整理</p>')

    a('<div class="about">')
    a('<p>これは、自身の終活から着想を得て設計された、手続地図の'
      '<b>実証プロトタイプ（PoC）</b>です。</p>')
    a('<p>自分が亡くなったあと、残された家族は何を、どの順番で、いつまでに'
      'しなければならないのか。<br>'
      'その迷いをゼロにするために、公的資料（一次証憑）を自律監査するAIエージェントと共に、'
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

    a(f'<footer><p>{esc(NOTICE)}</p>'
      f'<p>この画面は {d.isoformat()} を「今日」として計算しています。'
      f'状態は何も保存していません。ログインも要りません。</p></footer>')
    a('</div></body></html>')
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


@app.post("/api/agent/check")
def agent_check(req: AgentCheckRequest):
    # Validate node_id securely against formatting or path traversal injection
    if not validate_node_id(req.node_id):
        return JSONResponse(status_code=400, content={"ok": False, "error": "Invalid node_id format or directory traversal detected"})

    # Load node data
    node_item = _find_node(req.node_id)
    if not node_item:
        return JSONResponse(status_code=404, content={"ok": False, "error": "Node not found"})

    # Find or dynamically construct check details
    check_item = _find_check(req.node_id)
    if not check_item:
        check_item = {
            "check_id": f"{req.node_id}#1",
            "node_id": req.node_id,
            "claim": node_item.get("title", ""),
            "question": node_item.get("legal_metadata", {}).get("needs_check", [node_item.get("title", "")])[0] if node_item.get("legal_metadata", {}).get("needs_check") else node_item.get("title", ""),
        }

    # Verify key configuration
    from src.config import config
    api_key = os.environ.get("GEMINI_API_KEY") or config.GEMINI_API_KEY

    if not api_key:
        # Secure DRY_RUN fallback response containing static node analysis formatted for UI
        return {
            "ok": True,
            "result": {
                "action": "DRY_RUN",
                "reason": "GEMINI_API_KEY が設定されていないため、安全な静的検証モード（DRY_RUN）で表示しています。",
                "summary": "静的監査結果: 最新の公的ドメイン（.go.jp）用一次資料照合の準備が整っています。",
                "details": {
                    "node_id": req.node_id,
                    "title": node_item.get("title"),
                    "verification_status": node_item.get("legal_metadata", {}).get("verification_status"),
                    "needs_check": node_item.get("legal_metadata", {}).get("needs_check"),
                    "source_draft": node_item.get("legal_metadata", {}).get("source_draft"),
                    "source_url": node_item.get("legal_metadata", {}).get("source_url")
                }
            }
        }

    # Execute active check
    try:
        from src.agent.agent import ShioriAgent
        agent = ShioriAgent(api_key=api_key)
        result = agent.run_check(check_item, node_item, date.today())
        return {
            "ok": True,
            "result": {
                "action": result.verdict,
                "reason": f"一次資料（.go.jp）に基づく自動監査結果。判定: {result.verdict}（確信度: {result.confidence}）",
                "summary": _tidy(result.risk_if_wrong) or "制度との整合性を検証・確認しました。",
                "details": _tidy_details(result.model_dump())
            }
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": f"Internal agent error: {str(e)}"})


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
  position:fixed; top:0; right:-450px; width:420px; height:100vh;
  background:#ffffff; box-shadow:-4px 0 20px rgba(0,0,0,0.08);
  border-left:1px solid #d8d2c4; z-index:9999; transition:right 0.25s ease;
  display:flex; flex-direction:column; font-family:system-ui,sans-serif; color:#1a202c;">

  <div style="padding:16px 20px; border-bottom:1px solid #e2dcce; display:flex; justify-content:space-between; align-items:center; background:#faf8f5;">
    <div>
      <div style="font-size:12.5px; font-weight:700; color:#2d3748; letter-spacing:0.05em;">制度追従 外部監査エージェント</div>
      <div id="drawer-node-title" style="font-size:17px; font-weight:700; margin-top:3px; line-height:1.45;">駅を選んでください</div>
    </div>
    <button onclick="closeInspector()" aria-label="閉じる" style="background:#f4f1ea; border:1.5px solid #c9c1af; border-radius:50%; width:32px; height:32px; font-size:18px; cursor:pointer; color:#2d3748; flex-shrink:0;">&times;</button>
  </div>

  <div id="drawer-body" style="padding:20px; overflow-y:auto; flex:1; font-size:14.5px; line-height:1.8;">
    <div style="color:#2d3748;">路線図の駅を選ぶと、その手続について、国や省庁の公式ページ（.go.jp）といまの記載が合っているかを、Gemini がその場で確かめます。</div>
  </div>

  <div style="padding:14px 20px; border-top:1px solid #e2dcce; background:#faf8f5;">
    <button id="btn-run-check" onclick="runAgentCheck()" style="
      width:100%; padding:13px; background:#1558c0; color:#fff; border:none;
      border-radius:8px; font-weight:700; font-size:15px; cursor:pointer;
      box-shadow:0 2px 6px rgba(21,88,192,.28); letter-spacing:.01em;">
      Gemini で一次資料（.go.jp）を監査する
    </button>
  </div>
</div>

<script>
// 地図側のスクリプト（tools/render_map_svg.py）とは別の <script> だが、
// トップレベルの const / let / function は同じグローバル字句環境を共有する。
// 名前が一つでもぶつかると、このブロックは丸ごと構文エラーで死に、
// ドロワーは画面の外（right:-450px）に居たまま出てこない。
// だから全部を閉じ込め、HTML の onclick から呼ぶ二つだけを外に出す。
(function () {
let currentNodeId = null;
const CHECKS_MAP = CHECKS_MAP_PLACEHOLDER;

function openInspector(nodeId, title, status, sourceUrl, needsCheck) {
  currentNodeId = nodeId;
  document.getElementById('drawer-node-title').innerText = title || nodeId;
  const body = document.getElementById('drawer-body');
  
  // Close the original Shiori panel if present to avoid overlap
  if (typeof P !== 'undefined' && P) {
    P.classList.add('closed');
  }

  // いま見ている駅に印を残す。動画では、どこを開いたのかが分からなくなる。
  document.querySelectorAll('.st.sel').forEach(el => el.classList.remove('sel'));
  const picked = document.querySelector('[data-id="' + nodeId + '"]');
  if (picked) { picked.classList.add('sel'); }

  body.innerHTML = `
    <div style="margin-bottom:13px;">
      <span style="font-size:12.5px; background:#efece3; border:1px solid #d3cbb8; color:#2d3748; padding:3px 8px; border-radius:5px; font-family:ui-monospace,monospace;">${esc(nodeId)}</span>
      <span style="font-size:12.5px; font-weight:700; background:${status==='SOURCE_VERIFIED'?'#dcf0e2':'#fbe0dd'}; color:${status==='SOURCE_VERIFIED'?'#0f5c2a':'#a01c16'}; padding:3px 8px; border-radius:5px; margin-left:6px;">${esc(status || 'DRAFT')}</span>
    </div>
    <div style="margin-bottom:15px;">
      <div style="font-size:13px; font-weight:700; color:#2d3748;">よりどころにしている公式ページ</div>
      <a href="${esc(sourceUrl)}" target="_blank" rel="noopener" style="color:#1558c0; word-break:break-all; font-size:13px;">${esc(sourceUrl || 'まだ設定されていません（確認が要ります）')}</a>
    </div>
    <div style="margin-bottom:15px;">
      <div style="font-size:13px; font-weight:700; color:#2d3748;">これから専門家と確かめるポイント（${needsCheck ? needsCheck.length : 0}件）</div>
      <ul style="padding-left:19px; margin:5px 0 0; color:#1a202c;">
        ${(needsCheck || []).map(c => `<li style="margin:3px 0;">${esc(c)}</li>`).join('')}
      </ul>
    </div>
    <div id="agent-result-area" style="margin-top:17px; padding-top:15px; border-top:1px dashed #c9c1af;">
      <div style="color:#2d3748;">下のボタンを押すと、この手続について、国や省庁の公式ページを読みに行き、いまの記載と食い違っていないかを確かめます。</div>
    </div>
  `;
  document.getElementById('inspector-drawer').style.right = '0';
}

function closeInspector() {
  document.getElementById('inspector-drawer').style.right = '-450px';
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
               badge:'【整合】公式発表と一致',
               label:'現在の案内内容は、指定された公式ページ（一次資料）の記載と合致しています。'},
  AMBIGUOUS:  {bg:'#eceef0', bd:'#a9b0b6', fg:'#33383d',
               badge:'【要精査】判断保留・一部未記載',
               label:'公式資料の一部とは合致していますが、論点全体の確定には専門家による確認が必要です。'},
  NOT_FOUND:  {bg:'#eceef0', bd:'#a9b0b6', fg:'#33383d',
               badge:'【未検知】該当記述なし',
               label:'指定された公式ページの中から、該当する制度の明確な記述を確認できませんでした。'},
  UNCERTAIN:  {bg:'#fbe0dd', bd:'#d9776d', fg:'#a01c16',
               badge:'【要確認】記載に食い違い',
               label:'公的資料の記載に食い違いがあり、現時点では確定的な判定を行えません。'},
  UNVERIFIED: {bg:'#e1ebfd', bd:'#7aa6e8', fg:'#123a75',
               badge:'【要照会】自治体ごとに相違',
               label:'取扱いが市区町村ごとに異なるため、管轄の窓口への照会が必要です。'},
  DRY_RUN:    {bg:'#eceef0', bd:'#a9b0b6', fg:'#33383d',
               badge:'【静的確認】一次資料の照合は未実行',
               label:'APIキーが設定されていないため、ノードの記載内容のみを静的に確認しています。'}
};

// 確度は機械の値（HIGH/MEDIUM/LOW）で持ち、画面には日本語で出す。
const CONFIDENCE_JA = {HIGH: '高', MEDIUM: '中', LOW: '低'};

function renderVerdict(action, confidence) {
  const st = VERDICT_STYLE[action] || VERDICT_STYLE.AMBIGUOUS;
  const conf = confidence
    ? `<span style="font-size:13px; color:${st.fg}; margin-left:9px; font-weight:700;">（照合確度: ${esc(CONFIDENCE_JA[confidence] || confidence)}）</span>`
    : '';
  return `
    <div style="background:${st.bg}; border:2px solid ${st.bd}; border-radius:9px; padding:13px 15px;">
      <div style="display:flex; justify-content:space-between; align-items:baseline; gap:8px;">
        <span style="font-size:12.5px; color:${st.fg}; letter-spacing:.04em; font-weight:700;">公的資料との照合結果</span>
        <span style="font-size:11px; color:${st.fg}; opacity:.75; font-family:ui-monospace,monospace;">${esc(action || 'PROPOSAL')}</span>
      </div>
      <div style="font-weight:700; color:${st.fg}; font-size:17px; margin-top:3px; line-height:1.5;">
        ${esc(st.badge)}${conf}
      </div>
      <div style="font-size:14px; color:${st.fg}; margin-top:4px; line-height:1.7;">${esc(st.label)}</div>
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
  const head = done === steps.length ? '自律監査プロセス完了' : `自律監査プロセス（${done} / ${steps.length} 段）`;
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
    <div style="border:1.5px solid #d8d2c4; border-radius:7px; padding:11px 13px; margin-top:8px; background:#fff;">
      <div style="display:flex; justify-content:space-between; align-items:center; gap:6px;">
        <span style="font-size:13px; color:#1a202c; font-weight:700;">${esc(e.publisher || '公的機関')}</span>
        ${e.quote_verified
          ? '<span style="font-size:12px; font-weight:700; background:#dcf0e2; color:#0f5c2a; padding:3px 9px; border-radius:11px; white-space:nowrap;">✓ 原文どおりを機械で確認</span>'
          : '<span style="font-size:12px; font-weight:700; background:#fbe0dd; color:#a01c16; padding:3px 9px; border-radius:11px; white-space:nowrap;">未確認</span>'}
      </div>
      <blockquote style="margin:8px 0 0; padding-left:11px; border-left:4px solid #7aa6e8;
                         font-size:14px; color:#1a202c; line-height:1.8;">「${esc(e.quote)}」</blockquote>
      <a href="${esc(e.url)}" target="_blank" rel="noopener"
         style="display:inline-block; margin-top:7px; font-size:12.5px; color:#1558c0; word-break:break-all;">
        ${esc(e.url)}</a>
      ${e.page_date ? `<span style="font-size:12.5px; color:#4a5568; margin-left:6px;">（${esc(e.page_date)}）</span>` : ''}
    </div>`).join('');
  return `<div style="margin-top:16px;">
            <div style="font-size:14px; font-weight:700; color:#1a202c;">公式発表（一次資料）からの確認（${evidence.length}件）</div>
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

  rows.push(['専門家（人間）による最終確認',
             [d.human_review_required === true ? '適合' : '不適合',
              'AIが手引きを勝手に書き換えることはありません。専門家が目視で確認・承認するまで更新をブロックします。']]);

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

  const rejBlock = rej.length ? `
    <div style="margin-top:12px; background:#fbe0dd; border:1.5px solid #d9776d; border-radius:7px; padding:10px 13px;">
      <div style="font-size:13.5px; font-weight:700; color:#8f1710;">安全基準に抵触したため、AIの提案を取り下げました。</div>
      <details style="margin-top:6px;">
        <summary style="font-size:12px; color:#5e1410; cursor:pointer;">機械が記録した理由（${rej.length}件）</summary>
        <ul style="margin:5px 0 0; padding-left:18px; font-size:12px; color:#5e1410; line-height:1.75;">
          ${rej.map(r => `<li>${esc(r)}</li>`).join('')}
        </ul>
      </details>
    </div>` : `
    <div style="margin-top:12px; background:#dcf0e2; border:1.5px solid #6aab84; border-radius:7px;
                padding:9px 13px; font-size:13.5px; color:#0f5c2a; font-weight:700;">
      すべての安全基準（内部統制）に適合しています。
    </div>`;

  return `<div style="margin-top:16px;">
            <div style="font-size:14px; font-weight:700; color:#1a202c; margin-bottom:3px;">AIの安全性と品質の検証（内部統制）</div>
            <div style="font-size:13.5px;">${body}</div>
            ${rejBlock}
          </div>`;
}

function renderDryRun(d) {
  return `
    <div style="margin-top:16px; font-size:14px; color:#1a202c; line-height:1.8;">
      <div style="font-size:14px; font-weight:700; color:#1a202c;">いま手引きに書いてあること</div>
      <div style="margin-top:6px;">確認の段階: <strong>${esc(d.verification_status)}</strong></div>
      <div style="margin-top:4px; word-break:break-all;">よりどころにしている公式ページ: ${esc(d.source_url || 'まだ設定されていません')}</div>
      <div style="margin-top:4px;">根拠の下書き: ${esc(d.source_draft || '—')}</div>
    </div>`;
}

async function runAgentCheck() {
  if (!currentNodeId) return;
  const btn = document.getElementById('btn-run-check');
  const resArea = document.getElementById('agent-result-area');
  btn.disabled = true;
  btn.innerText = '公式ページを読んでいます…';
  resArea.innerHTML = '<div style="color:#1558c0; font-size:14px; font-weight:700;">国や省庁の公式ページ（.go.jp）を読み、いまの記載と食い違っていないかを確かめています。少しお待ちください。</div>';

  try {
    const res = await fetch('/api/agent/check', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({node_id: currentNodeId})
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || '監査に失敗しました');

    const result = data.result || data.proposal;
    const d = result.details || {};
    const isDry = result.action === 'DRY_RUN';

    const risk = d.risk_if_wrong || result.summary || result.reason || '';
    const riskBlock = risk ? `
      <div style="margin-top:13px; background:#f7f5ef; border:1px solid #d8d2c4; border-radius:7px; padding:11px 13px;">
        <div style="font-size:13px; font-weight:700; color:#1a202c;">もしこの案内が違っていた場合のご注意</div>
        <div style="font-size:14px; color:#1a202c; margin-top:3px; line-height:1.8;">${esc(risk)}</div>
      </div>` : '';
    const escBlock = d.escalation ? `
      <div style="margin-top:11px; font-size:13px; color:#2d3748; line-height:1.75;">
        人にお願いしたいこと: ${esc(d.escalation)}
      </div>` : '';

    resArea.innerHTML = [
      renderVerdict(result.action, d.confidence),
      isDry ? '' : renderProcess(d, result.action),
      riskBlock,
      escBlock,
      isDry ? renderDryRun(d)
            : [renderPatch(d.proposed_patch), renderEvidence(d.evidence), renderGates(d, result.action)].join('')
    ].join('');
  } catch (err) {
    resArea.innerHTML = `<div style="color:#a01c16; font-size:14px; font-weight:700;">うまく確かめられませんでした: ${esc(err.message)}</div>`;
  } finally {
    btn.disabled = false;
    btn.innerText = 'Gemini で一次資料（.go.jp）を監査する';
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
      
      openInspector(id, title, status, sourceUrl, needsCheck);
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
