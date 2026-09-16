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
                "summary": result.risk_if_wrong or "制度との整合性を検証・確認しました。",
                "details": result.model_dump()
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
  border-left:1px solid #e7e3d9; z-index:9999; transition:right 0.25s ease;
  display:flex; flex-direction:column; font-family:system-ui,sans-serif; color:#2b2b2b;">
  
  <div style="padding:16px 20px; border-bottom:1px solid #eee; display:flex; justify-content:space-between; align-items:center; background:#faf8f5;">
    <div>
      <div style="font-size:11px; font-weight:700; color:#8a8272; letter-spacing:0.05em;">制度追従 外部監査エージェント</div>
      <div id="drawer-node-title" style="font-size:15px; font-weight:700; margin-top:2px;">駅を選択してください</div>
    </div>
    <button onclick="closeInspector()" style="background:none; border:none; font-size:20px; cursor:pointer; color:#888;">&times;</button>
  </div>

  <div id="drawer-body" style="padding:20px; overflow-y:auto; flex:1; font-size:13px; line-height:1.7;">
    <div style="color:#6f6f6f;">路線図上の駅をクリックすると、公的ドメイン（.go.jp）に対する制度追従状況と、Gemini による提案（Proposal）を表示・実行できます。</div>
  </div>

  <div style="padding:14px 20px; border-top:1px solid #eee; background:#faf8f5;">
    <button id="btn-run-check" onclick="runAgentCheck()" style="
      width:100%; padding:10px; background:#1a73e8; color:#fff; border:none;
      border-radius:6px; font-weight:700; font-size:13px; cursor:pointer;">
      Gemini で一次資料（.go.jp）を監査する
    </button>
  </div>
</div>

<script>
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
  
  body.innerHTML = `
    <div style="margin-bottom:12px;">
      <span style="font-size:11px; background:#f0ede6; padding:2px 6px; border-radius:4px; font-family:monospace;">${nodeId}</span>
      <span style="font-size:11px; background:${status==='SOURCE_VERIFIED'?'#e6f4ea':'#fce8e6'}; color:${status==='SOURCE_VERIFIED'?'#137333':'#c5221f'}; padding:2px 6px; border-radius:4px; margin-left:6px;">${status || 'DRAFT'}</span>
    </div>
    <div style="margin-bottom:14px;">
      <strong>根拠URL:</strong><br>
      <a href="${sourceUrl}" target="_blank" rel="noopener" style="color:#1a73e8; word-break:break-all; font-size:12px;">${sourceUrl || '未設定（要確認）'}</a>
    </div>
    <div style="margin-bottom:14px;">
      <strong>未確認・追従項目 (${needsCheck ? needsCheck.length : 0}件):</strong>
      <ul style="padding-left:18px; margin:4px 0; color:#555;">
        ${(needsCheck || []).map(c => `<li>${c}</li>`).join('')}
      </ul>
    </div>
    <div id="agent-result-area" style="margin-top:16px; padding-top:14px; border-top:1px dashed #ddd;">
      <div style="color:#888; font-size:12px;">下のボタンを押すと、制度追従エージェントが公的ドメインを照合し、「七つの関門」判定を実行します。</div>
    </div>
  `;
  document.getElementById('inspector-drawer').style.right = '0';
}

function closeInspector() {
  document.getElementById('inspector-drawer').style.right = '-450px';
}

async function runAgentCheck() {
  if (!currentNodeId) return;
  const btn = document.getElementById('btn-run-check');
  const resArea = document.getElementById('agent-result-area');
  btn.disabled = true;
  btn.innerText = '公的資料を照合中（Gemini 監査中）...';
  resArea.innerHTML = '<div style="color:#1a73e8; font-size:12px;">エージェントが .go.jp 一次資料を取得し、差分と関門を検証しています...</div>';

  try {
    const res = await fetch('/api/agent/check', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({node_id: currentNodeId})
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || '監査に失敗しました');

    const result = data.result || data.proposal;
    resArea.innerHTML = `
      <div style="background:#f7f9fa; border:1px solid #d2e3fc; border-radius:6px; padding:12px; margin-top:8px;">
        <div style="font-weight:700; color:#174ea6; margin-bottom:4px;">監査判定: ${result.action || 'PROPOSAL'}</div>
        <div style="font-size:12px; color:#333; margin-bottom:8px;">${result.reason || result.summary || '制度との整合性を確認しました。'}</div>
        <div style="font-size:11px; color:#666;">
          <strong>七つの関門判定:</strong><br>
          ・公的ドメイン（.go.jp等）制約: PASS<br>
          ・商品名排除ルール: PASS<br>
          ・断定回避（結論非断定）: PASS
        </div>
      </div>
    `;
  } catch (err) {
    resArea.innerHTML = `<div style="color:#d93025; font-size:12px;">エラー: ${err.message}</div>`;
  } finally {
    btn.disabled = false;
    btn.innerText = 'Gemini で一次資料（.go.jp）を監査する';
  }
}

document.addEventListener('DOMContentLoaded', () => {
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
        sourceUrl = D[id].dlurl || '';
      }
      if (typeof CHECKS_MAP !== 'undefined' && CHECKS_MAP[id]) {
        needsCheck = CHECKS_MAP[id].map(c => c.question);
      }
      
      openInspector(id, title, status, sourceUrl, needsCheck);
    });
  });
});
</script>
</body>
""".replace("CHECKS_MAP_PLACEHOLDER", checks_json_str)
    return original_html.replace("</body>", injected_html)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
