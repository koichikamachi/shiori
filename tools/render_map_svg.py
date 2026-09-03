#!/usr/bin/env python3
"""
地図の面を、絵にする（S03 ルール14）

    python tools/render_map_svg.py                    既定の状況
    python tools/render_map_svg.py -s surv-day80      状況を選ぶ

テキストの一覧は、辺（つながり）を捨てていた。`unlocks` も `blocked_by` も
計算しているのに、平らに並べた時点で経路が消える。地図と言いながら、
地図の一番大事なところを出していなかった。

網の目の図にはしない。29件を同じ重さで線でつなぐと毛玉になる。
路線図にする。駅が手続、線が前提関係、戻りの矢印が出来事によるやり直し。

配置は「流れ × 深さ × priority」だけで決める。毎回まったく同じ場所に、
同じ駅が来る。力学レイアウトは使わない（地図は毎回おなじ形をしていること）。
"""
import argparse
import html
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.engine.engine import EVENT_RESETS, ShioriEngine  # noqa: E402
from src.engine.state import UserCondition                # noqa: E402
from src.main import SCENARIOS, STREAM_LABEL              # noqa: E402

# 色。状態は塗りと線で表す（S03 ルール14の記号に対応）
C = {
    "bg": "#fbfaf7", "ink": "#2b2b2b", "sub": "#6f6f6f", "line": "#b9b3a7",
    "done": "#4a7c59", "today": "#c2410c", "ready": "#1f4e79",
    "notready": "#ffffff", "absent": "#d9d5cc", "fog": "#9a93a8",
    "reset": "#b3261e", "lane": "#f2efe8",
}

DX, DY, PAD, R = 250, 74, 40, 15


def layout(result, engine, scope):
    """流れ×深さ×priority で座標を決める。乱数も力学も使わない。"""
    nodes = {m["node_id"]: m for m in result["map"]}
    raw = engine.nodes_by_scope[scope]
    present = {k: v for k, v in nodes.items() if v["present"]}

    pre = {k: [p for p in raw[k]["prerequisites"] if p in present] for k in present}
    depth, seen = {}, set()

    def d(nid, path=()):
        if nid in depth:
            return depth[nid]
        if nid in path:
            return 0
        v = 0 if not pre[nid] else 1 + max(d(p, path + (nid,)) for p in pre[nid])
        depth[nid] = v
        return v

    for nid in present:
        d(nid)

    # 流れの順序は、関係するものを先に（テキストの面と同じ）
    streams, order = {}, []
    for m in result["map"]:
        streams.setdefault(m["stream"], []).append(m)
        if m["stream"] not in order:
            order.append(m["stream"])
    order.sort(key=lambda s: (0 if any(x["present"] for x in streams[s]) else 1, s))

    pos, lanes, y = {}, [], PAD + 60
    for s in order:
        rows = streams[s]
        live = [r for r in rows if r["present"]]
        gone = [r for r in rows if not r["present"]]
        if not live and not gone:
            continue

        # 同じ深さに複数あるときは、priority 順に縦に積む
        cells = {}
        for r in sorted(live, key=lambda r: (depth[r["node_id"]],
                                             raw[r["node_id"]].get("priority", 999),
                                             r["node_id"])):
            k = depth[r["node_id"]]
            cells.setdefault(k, []).append(r)
        height = max([len(v) for v in cells.values()] or [1])

        for k, rs in cells.items():
            for i, r in enumerate(rs):
                pos[r["node_id"]] = (PAD + 120 + k * DX, y + i * DY)

        gy = y + height * DY - 20
        for i, r in enumerate(gone):                     # 通らない道は下に薄く
            pos[r["node_id"]] = (PAD + 120 + (i % 5) * 200, gy + 34 + (i // 5) * 30)

        band = height * DY + (((len(gone) - 1) // 5 + 1) * 30 + 34 if gone else 0)
        lanes.append({"stream": s, "y": y - 44, "h": band + 20,
                      "live": len(live), "gone": len(gone)})
        y += band + 46

    return pos, depth, pre, order, lanes, y + PAD


def esc(s):
    return html.escape(str(s or ""))


def render(key, today):
    sc = SCENARIOS[key]
    engine = ShioriEngine(data_dir=ROOT / "data" / "nodes")
    result = engine.evaluate(actor_scope=sc["scope"],
                             condition=UserCondition(**sc["cond"]),
                             completed_node_ids=sc["done"],
                             statuses=sc.get("statuses"), today=today)
    scope = sc["scope"]
    raw = engine.nodes_by_scope[scope]
    nodes = {m["node_id"]: m for m in result["map"]}
    pos, depth, pre, order, lanes, H = layout(result, engine, scope)
    W = PAD + 140 + (max(depth.values(), default=0) + 1) * DX
    todays = {n["id"] for n in result["today_actions"]}
    fog = {a["node_id"] for a in result["absent"] if a["fog"]}
    warn = {w["node_id"] for w in result["irreversible_warnings"]}

    out = []
    a = out.append

    # ---- 駅の説明（クリックで右に出す）----
    detail = {}
    for nid, m in nodes.items():
        n = raw[nid]
        irr = n.get("irreversibility") or {}
        sb = n.get("specialist_bridge") or {}
        detail[nid] = {
            "title": m["title"], "status": m["status"], "present": m["present"],
            "today": n.get("today_action", ""),
            "why": n.get("why_only_you", ""),
            "deadline": m["deadline_label"], "days": m["days_left"],
            "blocked": m["blocked_by"], "unlocks": m["unlocks"],
            "fact": irr.get("warning_fact", ""), "exc": irr.get("warning_exception", ""),
            "lost": irr.get("lost_option", ""),
            "caution": (n.get("caution") or {}).get("note", ""),
            "sb": (sb.get("type", ""), sb.get("concrete_query", "")),
            "absent": m["absent_reason"], "fog": m["fog"],
            "verif": m["verification_status"],
            "istoday": nid in todays,
        }

    a(f'<div class="wrap"><div class="head">'
      f'<h1>{esc(sc["title"])}</h1>'
      f'<p class="meta">{esc(key)}　／　{today.isoformat()} 時点　／　'
      f'ぜんぶで {result["progress"]["total"]} 件、'
      f'いま関係するのは {result["progress"]["present"]} 件、'
      f'通らない道 {result["progress"]["absent"]} 件'
      + (f'（うち、まだ分からないもの {result["progress"]["fog"]} 件）'
         if result["progress"]["fog"] else "") + '</p></div>')

    a('<div class="legend">'
      '<span><i class="k done"></i>済んだ</span>'
      '<span><i class="k today"></i>今日決める</span>'
      '<span><i class="k ready"></i>手をつけられる</span>'
      '<span><i class="k notready"></i>まだ開かない</span>'
      '<span><i class="k fogk"></i>まだ分からない</span>'
      '<span><i class="k gone"></i>通らない道</span>'
      '<span><i class="k resetk"></i>出来事で戻る</span>'
      '</div>')

    a(f'<div class="board"><svg viewBox="0 0 {W} {int(H)}" width="{W}">')
    a('<defs><marker id="ar" markerWidth="9" markerHeight="9" refX="8" refY="3" '
      'orient="auto"><path d="M0,0 L8,3 L0,6 z" fill="' + C["line"] + '"/></marker>'
      '<marker id="arr" markerWidth="9" markerHeight="9" refX="8" refY="3" '
      'orient="auto"><path d="M0,0 L8,3 L0,6 z" fill="' + C["reset"] + '"/></marker></defs>')

    for ln in lanes:
        label = STREAM_LABEL.get(ln["stream"], ln["stream"])
        tail = (f'関係する {ln["live"]} 件' if ln["live"] else "この流れは通りません")
        a(f'<rect x="{PAD-14}" y="{ln["y"]}" width="{W-PAD-12}" height="{ln["h"]}" '
          f'rx="10" fill="{C["lane"]}"/>')
        a(f'<text x="{PAD}" y="{ln["y"]+22}" class="lane">{esc(label)}'
          f'　<tspan class="lanesub">{esc(tail)}</tspan></text>')

    # 前提のつながり
    for nid, ps in pre.items():
        x2, y2 = pos[nid]
        for p in ps:
            x1, y1 = pos[p]
            mx = (x1 + x2) / 2
            a(f'<path d="M{x1+R},{y1} C{mx},{y1} {mx},{y2} {x2-R-8},{y2}" '
              f'fill="none" stroke="{C["line"]}" stroke-width="2" marker-end="url(#ar)"/>')

    # 出来事による戻り（ルール7）。先生の絵の「×5に戻る」
    for ev, ids in EVENT_RESETS.items():
        for nid in ids:
            if nid not in pos:
                continue
            downs = [k for k, ps in pre.items() if nid in ps]
            if not downs:
                continue
            src = max(downs, key=lambda k: depth[k])
            x1, y1 = pos[src]
            x2, y2 = pos[nid]
            a(f'<path d="M{x1},{y1+R} C{x1},{y1+58} {x2},{y2+58} {x2},{y2+R+8}" '
              f'fill="none" stroke="{C["reset"]}" stroke-width="1.6" '
              f'stroke-dasharray="5 4" marker-end="url(#arr)"/>')
            a(f'<text x="{(x1+x2)/2}" y="{max(y1,y2)+72}" class="resetlbl" '
              f'text-anchor="middle">{esc(ev)} で戻る</text>')

    # 駅
    for nid, (x, y) in pos.items():
        m = nodes[nid]
        d = detail[nid]
        if not m["present"]:
            fill, stroke, cls = C["absent"], C["absent"], "gone"
            if nid in fog:
                fill, stroke, cls = "#fff", C["fog"], "fogst"
        elif m["status"] == "DONE":
            fill = stroke = C["done"]; cls = "done"
        elif d["istoday"]:
            fill, stroke, cls = C["today"], C["today"], "today"
        elif m["status"] == "NOT_READY":
            fill, stroke, cls = C["notready"], C["line"], "notready"
        else:
            fill, stroke, cls = "#fff", C["ready"], "ready"

        dash = ' stroke-dasharray="4 3"' if nid in fog else ""
        a(f'<g class="st {cls}" data-id="{esc(nid)}" tabindex="0">')
        if nid in warn:
            a(f'<circle cx="{x}" cy="{y}" r="{R+5}" fill="none" '
              f'stroke="{C["reset"]}" stroke-width="1.4" opacity=".55"/>')
        a(f'<circle cx="{x}" cy="{y}" r="{R}" fill="{fill}" stroke="{stroke}" '
          f'stroke-width="2.6"{dash}/>')
        if m["status"] == "DONE":
            a(f'<path d="M{x-6},{y} l4,4 l8,-8" fill="none" stroke="#fff" stroke-width="2.4"/>')
        t = m["title"]
        t = t if len(t) <= 13 else t[:12] + "…"
        a(f'<text x="{x}" y="{y+R+16}" text-anchor="middle" class="stt">{esc(t)}</text>')
        if m["deadline_label"]:
            lab = (f'あと{m["days_left"]}日' if m["days_left"] is not None and m["days_left"] >= 0
                   else (f'{abs(m["days_left"])}日超過' if m["days_left"] is not None else "期限あり"))
            a(f'<text x="{x}" y="{y-R-8}" text-anchor="middle" class="dl">{esc(lab)}</text>')
        a('</g>')
    a('</svg></div>')

    a('<aside id="panel"><p class="hint">駅をクリックすると、その手続の中身が出ます。</p></aside>')
    a('</div>')
    a('<script>const D=' + json.dumps(detail, ensure_ascii=False) + ';</script>')
    return "".join(out), sc


CSS = """
:root{--bg:#fbfaf7;--ink:#2b2b2b;--sub:#6f6f6f}
:root{color-scheme:light}
body{margin:0;background:var(--bg);color:var(--ink);
 font-family:"Hiragino Kaku Gothic ProN","Yu Gothic",system-ui,sans-serif}
.wrap{display:grid;grid-template-columns:1fr 340px;grid-template-areas:
 "head head" "legend legend" "board panel";gap:0 20px;padding:22px 24px;max-width:1600px}
.head{grid-area:head}
h1{font-size:19px;margin:0 0 4px}
.meta{margin:0;color:var(--sub);font-size:12.5px}
.legend{grid-area:legend;display:flex;flex-wrap:wrap;gap:14px;margin:14px 0 10px;
 font-size:12px;color:var(--sub)}
.legend span{display:flex;align-items:center;gap:5px}
.k{width:13px;height:13px;border-radius:50%;display:inline-block;border:2.4px solid}
.k.done{background:#4a7c59;border-color:#4a7c59}
.k.today{background:#c2410c;border-color:#c2410c}
.k.ready{background:#fff;border-color:#1f4e79}
.k.notready{background:#fff;border-color:#b9b3a7}
.k.fogk{background:#fff;border-color:#9a93a8;border-style:dashed}
.k.gone{background:#d9d5cc;border-color:#d9d5cc}
.k.resetk{background:#fff;border-color:#b3261e;border-style:dashed}
.board{grid-area:board;overflow-x:auto;background:#fff;border:1px solid #e7e3d9;
 border-radius:12px;padding:8px}
svg{display:block}
text{font-family:"Hiragino Kaku Gothic ProN","Yu Gothic",system-ui,sans-serif}
.lane{font-size:13px;font-weight:700;fill:#7a7364}
.lanesub{font-size:11.5px;font-weight:400;fill:#a09884}
.stt{font-size:11.5px;fill:#2b2b2b}
.gone .stt,.fogst .stt{fill:#a9a396}
.dl{font-size:11px;fill:#b3261e}
.resetlbl{font-size:10px;fill:#b3261e}
.st{cursor:pointer}
.st:hover circle{stroke-width:3.6}
.st:focus{outline:none}
aside{grid-area:panel;position:sticky;top:22px;align-self:start;background:#fff;
 border:1px solid #e7e3d9;border-radius:12px;padding:16px;font-size:13px;line-height:1.75;
 max-height:82vh;overflow:auto}
.hint{color:var(--sub);margin:0}
aside h2{font-size:15px;margin:0 0 8px}
aside .tag{display:inline-block;font-size:11px;padding:2px 8px;border-radius:99px;
 background:#f2efe8;color:#7a7364;margin:0 4px 8px 0}
aside .band{background:#fff5f2;border-left:3px solid #b3261e;padding:9px 11px;
 border-radius:0 6px 6px 0;margin:10px 0}
aside .band b{color:#b3261e}
aside dl{margin:10px 0 0}aside dt{font-size:11.5px;color:var(--sub);margin-top:9px}
aside dd{margin:2px 0 0}
aside .note{background:#f7f5ef;padding:9px 11px;border-radius:6px;margin:10px 0}
footer{grid-column:1/-1;color:var(--sub);font-size:11.5px;margin-top:18px;
 border-top:1px solid #e7e3d9;padding-top:12px}
"""

JS = """
const P=document.getElementById('panel');
const esc=s=>String(s??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const SB={LAWYER:'弁護士',JUDICIAL_SCRIVENER:'司法書士',TAX_ACCOUNTANT:'税理士',
 ADMINISTRATIVE_SCRIVENER:'行政書士',LAND_SURVEYOR:'土地家屋調査士',
 INTERNATIONAL_LAWYER:'海外の弁護士',INTERNATIONAL_TAX_LAWYER:'国際税務の専門家'};
const ST={DONE:'済んだ',READY:'手をつけられる',IN_PROGRESS:'着手している',
 WAITING:'相手の返事を待っている',NOT_READY:'まだ開かない'};
function show(id){
  const d=D[id];if(!d)return;let h='<h2>'+esc(d.title)+'</h2>';
  h+='<span class="tag">'+esc(d.present?(ST[d.status]||d.status):(d.fog?'まだ分からない':'通らない道'))+'</span>';
  if(d.istoday)h+='<span class="tag" style="background:#fdece4;color:#c2410c">今日決めること</span>';
  if(d.verif)h+='<span class="tag">'+esc(d.verif)+'</span>';
  if(!d.present){h+='<div class="note">'+esc(d.absent||'')+'</div>';P.innerHTML=h;return;}
  if(d.fact)h+='<div class="band"><b>【事実】</b> '+esc(d.fact)+'<br><br><b>【ただし】</b> '+esc(d.exc)
    +(d.lost?'<br><br><span style="font-size:11.5px;color:#7a7364">失われるもの … '+esc(d.lost)+'</span>':'')+'</div>';
  h+='<dl>';
  h+='<dt>やること</dt><dd>'+esc(d.today)+'</dd>';
  if(d.deadline)h+='<dt>期限</dt><dd>'+esc(d.deadline)+(d.days!=null?'（あと '+d.days+' 日）':'')+'</dd>';
  if(d.blocked&&d.blocked.length)h+='<dt>いま止まっている理由</dt><dd>「'+d.blocked.map(esc).join('」「')+'」が終わっていません</dd>';
  if(d.unlocks&&d.unlocks.length)h+='<dt>ここが済むと開きます</dt><dd>'+d.unlocks.map(esc).join('、')+'</dd>';
  if(d.why)h+='<dt>あなたにしかできない理由</dt><dd>'+esc(d.why)+'</dd>';
  if(d.sb[0])h+='<dt>相談するなら</dt><dd>'+esc(SB[d.sb[0]]||d.sb[0])+'<br>'+esc(d.sb[1])+'</dd>';
  h+='</dl>';
  if(d.caution)h+='<div class="note">※ '+esc(d.caution)+'</div>';
  P.innerHTML=h;
}
document.querySelectorAll('.st').forEach(g=>{
  g.addEventListener('click',()=>show(g.dataset.id));
  g.addEventListener('keydown',e=>{if(e.key==='Enter')show(g.dataset.id);});
});
"""


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("-s", "--scenario", default="surv-day10-nomemo")
    ap.add_argument("--today", default=None)
    ap.add_argument("-o", "--out", default=None)
    args = ap.parse_args()
    if args.scenario not in SCENARIOS:
        print(f"そんな状況はありません: {args.scenario}", file=sys.stderr)
        sys.exit(1)
    today = date.fromisoformat(args.today) if args.today else date.today()

    body, sc = render(args.scenario, today)
    notice = ("これは、手続の順序と期限を示す地図です。個別の法律相談や税務相談ではありません。"
              "あなたの場合にどうなるかは、記載の相談先で確かめてください。"
              "制度は変わります。多くの項目は、まだ専門家の確認を受けていません。")
    page = (f'<!doctype html><html lang="ja"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>しおり／地図 — {html.escape(sc["title"])}</title>'
            f'<style>{CSS}</style></head><body>{body}'
            f'<div class="wrap" style="padding-top:0"><footer>{notice}</footer></div>'
            f'<script>{JS}</script></body></html>')

    out = Path(args.out) if args.out else ROOT / "docs" / "submission" / f"map_{args.scenario}.html"
    out.write_text(page, encoding="utf-8")
    print(f"{out.relative_to(ROOT)}　（{args.scenario}）")
