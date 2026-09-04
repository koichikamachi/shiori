#!/usr/bin/env python3
"""
地図の面を、絵にする（S03 ルール14）

    python tools/render_map_svg.py                    既定の状況
    python tools/render_map_svg.py -s surv-day80      状況を選ぶ
    python tools/render_map_svg.py -s all             ぜんぶ書き出す

テキストの一覧は、辺（つながり）を捨てていた。`unlocks` も `blocked_by` も
計算しているのに、平らに並べた時点で経路が消える。地図と言いながら、
地図の一番大事なところを出していなかった。

網の目の図にはしない。29件を同じ重さで線でつなぐと毛玉になる。
路線図にする。駅が手続、線が前提関係、戻りの矢印が出来事によるやり直し。

配置は「流れ × 深さ × priority」だけで決める。毎回まったく同じ場所に、
同じ駅が来る。力学レイアウトは使わない（地図は毎回おなじ形をしていること）。

■ 描き分けの決めごと（ルール14の三区分を、絵でも守る）

  通る道        … 駅として描く。線でつなぐ
  まだ分からない … 駅にしない。線の下に、別の見出しで置く（霧）
  通らない道     … 駅にしない。線の下に、さらに別の見出しで置く

  三つを同じ帯に並べると、まだ答えていないだけのものが
  「通らなくて済みます」の顔をする。それは断定である（ルール15）。
  だから絵の上でも、混ぜない。

  期限は、通る道にしか出さない。通らない道に「期限あり」と書くと、
  自分に関係のない時計が動いているように見える。
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

# 色。状態は塗りと線と形で表す（刷ったとき白黒でも区別がつくように）
C = {
    "bg": "#fbfaf7", "ink": "#2b2b2b", "sub": "#6f6f6f", "line": "#b9b3a7",
    "done": "#4a7c59", "today": "#c2410c", "ready": "#1f4e79",
    "notready": "#ffffff", "absent": "#d9d5cc", "fog": "#8d86a0",
    "reset": "#b3261e", "lane": "#f4f1ea", "closed": "#9b8f86",
}

# 出来事は、画面では日本語で言う。生の識別子は出さない。
EVENT_LABEL = {
    "NEW_WILL_FOUND": "あとから遺言が出てきたら",
    "UNKNOWN_DEBT_FOUND": "知らない借金が出てきたら",
    "NEW_HEIR_FOUND": "知らない相続人がいたら",
    "HEIR_RENOUNCED": "誰かが放棄したら",
    "FOREIGN_ASSET_FOUND": "海外の資産が出てきたら",
}

# 深さの見出し。左が先、右があと。時刻ではなく順序である。
DEPTH_LABEL = ["はじめに", "これが済んでから", "そのあと", "さらにあと",
               "その先", "いちばん先"]

DX, DY, PAD, R = 236, 88, 40, 16
HEAD_H = 44          # 流れの見出しに使う高さ
RESET_H = 30         # 戻りの矢印ひとつ分の高さ
LINE_CH = 13         # 駅名の一行の文字数
TOP = 58             # 深さの見出しに使う高さ


def wrap2(s, ch=LINE_CH):
    """駅名を二行に折る。日本語は単語で折れないので文字数で折る。"""
    s = str(s or "")
    if len(s) <= ch:
        return [s]
    if len(s) <= ch * 2:
        return [s[:ch], s[ch:]]
    return [s[:ch], s[ch:ch * 2 - 1] + "…"]


def layout(result, engine, scope):
    """流れ×深さ×priority で座標を決める。乱数も力学も使わない。"""
    nodes = {m["node_id"]: m for m in result["map"]}
    raw = engine.nodes_by_scope[scope]
    present = {k: v for k, v in nodes.items() if v["present"]}

    pre = {k: [p for p in raw[k]["prerequisites"] if p in present] for k in present}
    depth = {}

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

    # どの戻りの矢印が、どの流れに属するか（帯の高さに要るので先に数える）
    resets = []
    for ev, ids in EVENT_RESETS.items():
        for nid in ids:
            if nid not in present:
                continue
            downs = [k for k, ps in pre.items() if nid in ps]
            if not downs:
                continue
            resets.append((ev, max(downs, key=lambda k: depth[k]), nid))

    pos, lanes, dips, y = {}, [], {}, TOP + PAD
    for s in order:
        live = [r for r in streams[s] if r["present"]]
        if not live:                       # まるごと通らない流れは、絵に帯を作らない
            continue

        cells = {}
        for r in sorted(live, key=lambda r: (depth[r["node_id"]],
                                             raw[r["node_id"]].get("priority", 999),
                                             r["node_id"])):
            cells.setdefault(depth[r["node_id"]], []).append(r)
        height = max(len(v) for v in cells.values())

        top = y                            # 帯の上端
        first = top + HEAD_H + R + 14      # 一つめの駅の中心
        for k, rs in cells.items():
            for i, r in enumerate(rs):
                pos[r["node_id"]] = (PAD + 120 + k * DX, first + i * DY)

        # 戻りの矢印は、その二つの駅のすぐ下を通す。遠くまで垂らさない
        ids = {r["node_id"] for r in live}
        mine = [t for t in resets if t[1] in ids and t[2] in ids]
        bottom = first + (height - 1) * DY + R + 44
        if mine:
            # 何本あっても、駅名の下でそろえる。矢印どうしも重ねない。
            # 高さは「矢印が通る列にある駅」のいちばん下から測る。
            # 端点だけで測ると、あいだの列の駅名の上に文字が乗る。
            xs = [pos[t[1]][0] for t in mine] + [pos[t[2]][0] for t in mine]
            lo, hi = min(xs) - DX / 2, max(xs) + DX / 2
            base = max(pos[r["node_id"]][1] for r in live
                       if lo <= pos[r["node_id"]][0] <= hi) + 66
            for i, t in enumerate(mine):
                dips[t] = base + i * RESET_H
                bottom = max(bottom, dips[t] + 18)

        lanes.append({"stream": s, "y": top, "h": bottom - top, "live": len(live)})
        y = bottom + 22

    return pos, depth, pre, order, lanes, resets, dips, y + 10


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
    pos, depth, pre, order, lanes, resets, dips, H = layout(result, engine, scope)
    maxd = max(depth.values(), default=0)
    W = PAD + 120 + maxd * DX + 130
    todays = {n["id"]: i + 1 for i, n in enumerate(result["today_actions"])}
    fog = {a["node_id"] for a in result["absent"] if a["fog"]}
    warn = {w["node_id"] for w in result["irreversible_warnings"]}
    prog = result["progress"]

    out = []
    a = out.append

    # ---- 駅の説明（押すと横から出す）----
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
            "dlsrc": m.get("deadline_source") or "",
            "dlurl": m.get("deadline_source_url") or "",
            "dlat": m.get("deadline_checked_at") or "",
            "blocked": m["blocked_by"], "unlocks": m["unlocks"],
            "fact": irr.get("warning_fact", ""), "exc": irr.get("warning_exception", ""),
            "lost": irr.get("lost_option", ""),
            "caution": (n.get("caution") or {}).get("note", ""),
            "sb": (sb.get("type", ""), sb.get("concrete_query", "")),
            "absent": m["absent_reason"], "fog": m["fog"],
            "verif": m["verification_status"],
            "istoday": todays.get(nid, 0),
            "closed": m.get("gate_closed", False),
            "assumed": m.get("clock_assumed", False),
            "closednote": m.get("closed_note") or "",
            "stream": STREAM_LABEL.get(m["stream"], m["stream"]),
        }

    done_n = sum(1 for m in nodes.values() if m["present"] and m["status"] == "DONE")
    pct = round(done_n / prog["present"] * 100) if prog["present"] else 0

    a('<div class="wrap"><div class="head">')
    a(f'<h1>{esc(sc["title"])}</h1>')
    a(f'<p class="meta">{esc(key)}　／　{today.isoformat()} 時点</p>')
    a('<div class="counts">'
      f'<b>{prog["present"]}</b> 件が、あなたの道です'
      f'<span class="sep">／</span>済んだ <b>{done_n}</b> 件'
      + (f'<span class="sep">／</span>まだ分からない <b>{prog["fog"]}</b> 件'
         if prog["fog"] else "")
      + f'<span class="sep">／</span>通らない道 <b>{prog["absent"] - prog["fog"]}</b> 件'
      f'<span class="sep">／</span>ぜんぶで {prog["total"]} 件</div>')
    a(f'<div class="bar"><i style="width:{pct}%"></i></div>')
    a('</div>')

    a('<div class="legend">'
      '<span><i class="k done"></i>済んだ</span>'
      '<span><i class="k today"></i>今日決める（三件を超えません）</span>'
      '<span><i class="k ready"></i>手をつけられる</span>'
      '<span><i class="k notready"></i>まだ開かない</span>'
      '<span><i class="k warnk"></i>通り過ぎると戻れない</span>'
      '<span><i class="k closedk"></i>もう申し立てられない期間</span>'
      '<span><i class="k resetk"></i>出来事で戻る</span>'
      '<span class="axis">← 左が先、右があと。左が済むと、右が開きます</span>'
      '</div>')

    a(f'<div class="board"><svg viewBox="0 0 {W} {int(H)}" width="{W}" '
      f'role="img" aria-label="手続の路線図">')
    a('<defs><marker id="ar" markerWidth="9" markerHeight="9" refX="8" refY="3" '
      'orient="auto"><path d="M0,0 L8,3 L0,6 z" fill="' + C["line"] + '"/></marker>'
      '<marker id="arr" markerWidth="9" markerHeight="9" refX="8" refY="3" '
      'orient="auto"><path d="M0,0 L8,3 L0,6 z" fill="' + C["reset"] + '"/></marker></defs>')

    # 深さの見出し（X軸の意味を、言葉で置く）
    for k in range(maxd + 1):
        x = PAD + 120 + k * DX
        lab = DEPTH_LABEL[k] if k < len(DEPTH_LABEL) else f"{k+1}番目"
        a(f'<text x="{x}" y="{TOP-16}" text-anchor="middle" class="col">'
          f'{k+1}　{esc(lab)}</text>')
        a(f'<line x1="{x}" y1="{TOP-8}" x2="{x}" y2="{int(H)-10}" '
          f'stroke="#eae6dc" stroke-width="1"/>')

    for ln in lanes:
        label = STREAM_LABEL.get(ln["stream"], ln["stream"])
        a(f'<rect x="{PAD-14}" y="{ln["y"]}" width="{W-PAD-4}" height="{ln["h"]}" '
          f'rx="12" fill="{C["lane"]}"/>')
        a(f'<text x="{PAD}" y="{ln["y"]+24}" class="lane">{esc(label)}'
          f'　<tspan class="lanesub">{ln["live"]} 件</tspan></text>')

    # 前提のつながり
    for nid, ps in pre.items():
        x2, y2 = pos[nid]
        for p in ps:
            x1, y1 = pos[p]
            mx = (x1 + x2) / 2
            a(f'<path d="M{x1+R},{y1} C{mx},{y1} {mx},{y2} {x2-R-9},{y2}" '
              f'fill="none" stroke="{C["line"]}" stroke-width="2" marker-end="url(#ar)"/>')

    # 出来事による戻り（ルール7）。帯の下の余白に、重ならないように並べる
    for t in resets:
        if t not in dips:
            continue
        ev, src, dst = t
        yb = dips[t]
        x1, y1 = pos[src]
        x2, y2 = pos[dst]
        a(f'<path d="M{x1},{y1+R} C{x1},{yb} {x2},{yb} {x2},{y2+R+9}" '
          f'fill="none" stroke="{C["reset"]}" stroke-width="1.6" '
          f'stroke-dasharray="5 4" marker-end="url(#arr)"/>')
        a(f'<text x="{(x1+x2)/2}" y="{yb+4}" class="resetlbl" '
          f'text-anchor="middle">{esc(EVENT_LABEL.get(ev, ev))}、ここへ戻ります</text>')

    # 駅
    for nid, (x, y) in pos.items():
        m = nodes[nid]
        d = detail[nid]
        if m["status"] == "DONE":
            fill = stroke = C["done"]; cls = "done"
        elif d["closed"]:
            # 門が閉じた駅。急かさない。色を抜き、破線にする
            fill, stroke, cls = "#fff", C["closed"], "closed"
        elif d["istoday"]:
            fill, stroke, cls = C["today"], C["today"], "today"
        elif m["status"] == "NOT_READY":
            fill, stroke, cls = C["notready"], C["line"], "notready"
        else:
            fill, stroke, cls = "#fff", C["ready"], "ready"

        a(f'<g class="st {cls}" data-id="{esc(nid)}" tabindex="0" role="button">')
        if nid in warn:                     # 通り過ぎると戻れない駅は、二重丸
            a(f'<circle cx="{x}" cy="{y}" r="{R+5}" fill="none" '
              f'stroke="{C["reset"]}" stroke-width="1.5" opacity=".6"/>')
        dash = ' stroke-dasharray="4 3"' if d["closed"] else ""
        a(f'<circle cx="{x}" cy="{y}" r="{R}" fill="{fill}" stroke="{stroke}" '
          f'stroke-width="2.8"{dash}/>')
        if m["status"] == "DONE":
            a(f'<path d="M{x-6},{y} l4.5,4.5 l8,-9" fill="none" stroke="#fff" '
              f'stroke-width="2.6" stroke-linecap="round"/>')
        elif d["istoday"]:
            a(f'<text x="{x}" y="{y+5}" text-anchor="middle" class="num">'
              f'{d["istoday"]}</text>')
        for i, ln in enumerate(wrap2(m["title"])):
            a(f'<text x="{x}" y="{y+R+16+i*15}" text-anchor="middle" class="stt">'
              f'{esc(ln)}</text>')
        # 期限の札。済んだ駅には出さない（days_left が None になっている）。
        # 門が閉じた駅は、日数ではなく「過ぎている可能性」と書く。断定しない。
        if m["status"] == "DONE":
            lab = None
        elif d["closed"]:
            lab = "期間が過ぎている可能性"
        elif m["days_left"] is not None:
            # 起算日を聞いていないときは「およそ」を付ける。
            # 聞いていない日を根拠に断定しない（S03 ルール4・15）。
            head = "およそ " if m.get("clock_assumed") else ""
            lab = (f'{head}あと{m["days_left"]}日' if m["days_left"] >= 0
                   else f'{head}{abs(m["days_left"])}日超過')
        elif m["deadline_label"]:
            lab = "期限あり"
        else:
            lab = None
        if lab:
            cl = ("dl closed" if d["closed"]
                  else ("dl assumed" if m.get("clock_assumed") else "dl"))
            a(f'<text x="{x}" y="{y-R-9}" text-anchor="middle" class="{cl}">'
              f'{esc(lab)}</text>')
        a('</g>')
    a('</svg></div>')

    # ---- 駅にしないもの。三区分を混ぜない（ルール14）----
    by_stream = {}
    for m in result["map"]:
        if not m["present"]:
            by_stream.setdefault(m["stream"], {"fog": [], "gone": []})[
                "fog" if m["fog"] else "gone"].append(m)

    def chips(items):
        return "".join(
            f'<button class="chip" data-id="{esc(m["node_id"])}">'
            f'{esc(m["title"])}</button>' for m in items)

    fogs = [(s, v["fog"]) for s, v in by_stream.items() if v["fog"]]
    gones = [(s, v["gone"]) for s, v in by_stream.items() if v["gone"]]

    if fogs:
        n = sum(len(v) for _, v in fogs)
        a(f'<details class="side fog" open><summary>まだ分からない　'
          f'<b>{n}</b> 件</summary>'
          f'<p class="why">答えていただいていない項目があるので、'
          f'通るかどうかが決まっていません。'
          f'<strong>「通らなくて済む」という意味ではありません。</strong></p>')
        for s, v in fogs:
            a(f'<div class="grp"><h3>{esc(STREAM_LABEL.get(s, s))}</h3>{chips(v)}</div>')
        a('</details>')

    if gones:
        n = sum(len(v) for _, v in gones)
        a(f'<details class="side gone"><summary>通らない道　<b>{n}</b> 件'
          f'<span class="hint2">（押すと開きます）</span></summary>'
          f'<p class="why">いまうかがった範囲では、あなたはこの道を通らなくて済みます。'
          f'事情が変われば、また出てきます。</p>')
        for s, v in gones:
            a(f'<div class="grp"><h3>{esc(STREAM_LABEL.get(s, s))}</h3>{chips(v)}</div>')
        a('</details>')

    a('</div>')
    a('<aside id="panel" class="closed"><button id="x" aria-label="閉じる">×</button>'
      '<div id="body"></div></aside>')
    a('<script>const D=' + json.dumps(detail, ensure_ascii=False) + ';</script>')
    return "".join(out), sc


CSS = """
:root{--bg:#fbfaf7;--ink:#2b2b2b;--sub:#6f6f6f;--rule:#e7e3d9}
:root{color-scheme:light}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
 font-family:"Hiragino Kaku Gothic ProN","Yu Gothic",system-ui,sans-serif}
.wrap{padding:22px 24px 40px;max-width:1520px;margin:0 auto}
h1{font-size:20px;margin:0 0 4px;letter-spacing:.01em}
.meta{margin:0 0 10px;color:var(--sub);font-size:12.5px}
.counts{font-size:13px;color:#4a4a4a}
.counts b{font-size:15px;color:var(--ink)}
.counts .sep{color:#cfc9bc;margin:0 9px}
.bar{height:6px;background:#eae6dc;border-radius:99px;margin:9px 0 0;max-width:420px;
 overflow:hidden}
.bar i{display:block;height:100%;background:#4a7c59;border-radius:99px}
.legend{display:flex;flex-wrap:wrap;align-items:center;gap:14px;margin:16px 0 10px;
 font-size:12px;color:var(--sub)}
.legend span{display:flex;align-items:center;gap:5px}
.legend .axis{color:#8c857a;margin-left:auto;font-size:11.5px}
.k{width:13px;height:13px;border-radius:50%;display:inline-block;border:2.4px solid}
.k.done{background:#4a7c59;border-color:#4a7c59}
.k.today{background:#c2410c;border-color:#c2410c}
.k.ready{background:#fff;border-color:#1f4e79}
.k.notready{background:#fff;border-color:#b9b3a7}
.k.warnk{background:#fff;border-color:#b3261e}
.k.closedk{background:#fff;border-color:#9b8f86;border-style:dashed}
.k.resetk{background:#fff;border-color:#b3261e;border-style:dashed}
.board{overflow-x:auto;background:#fff;border:1px solid var(--rule);
 border-radius:12px;padding:6px 6px 10px}
svg{display:block}
text{font-family:"Hiragino Kaku Gothic ProN","Yu Gothic",system-ui,sans-serif}
.col{font-size:11.5px;fill:#a49b88;letter-spacing:.06em}
.lane{font-size:13.5px;font-weight:700;fill:#77705f}
.lanesub{font-size:11.5px;font-weight:400;fill:#a89f8b}
.stt{font-size:11.5px;fill:#2b2b2b;stroke:#fff;stroke-width:3.2px;paint-order:stroke;stroke-linejoin:round}
.num{font-size:14px;font-weight:700;fill:#fff}
.dl.closed{fill:#9b8f86;font-weight:400}
.dl.assumed{font-weight:400}
.dl{font-size:11px;fill:#b3261e;font-weight:700;stroke:#fff;stroke-width:3.2px;paint-order:stroke;stroke-linejoin:round}
.resetlbl{font-size:10.5px;fill:#b3261e;stroke:#f4f1ea;stroke-width:3.4px;paint-order:stroke;stroke-linejoin:round}
.st{cursor:pointer}
.st:hover circle{stroke-width:4}
.st:focus{outline:none}
.st:focus circle{stroke-width:4}
details.side{margin-top:14px;background:#fff;border:1px solid var(--rule);
 border-radius:12px;padding:12px 16px}
details.side summary{cursor:pointer;font-size:13px;font-weight:700;color:#6b6455}
details.side.fog summary{color:#6b6280}
details.side b{font-size:15px}
.hint2{font-weight:400;color:#a89f8b;font-size:11.5px;margin-left:6px}
.why{font-size:12px;color:var(--sub);margin:9px 0 4px;line-height:1.7}
.why strong{color:#6b6280}
.grp{margin:10px 0 0}
.grp h3{font-size:11.5px;color:#a89f8b;margin:0 0 5px;font-weight:700}
.chip{font:inherit;font-size:11.5px;cursor:pointer;background:#f4f1ea;color:#8a8272;
 border:1px solid #e7e3d9;border-radius:99px;padding:4px 11px;margin:0 5px 5px 0}
details.fog .chip{background:#fff;border-style:dashed;border-color:#b9b2c6;color:#6b6280}
.chip:hover{background:#ece7db}
aside{position:fixed;top:0;right:0;width:390px;max-width:92vw;height:100vh;background:#fff;
 border-left:1px solid var(--rule);box-shadow:-14px 0 34px rgba(0,0,0,.07);
 padding:22px 24px;overflow:auto;font-size:13px;line-height:1.8;
 transition:transform .18s ease}
aside.closed{transform:translateX(102%)}
#x{position:absolute;top:12px;right:14px;border:0;background:#f4f1ea;border-radius:50%;
 width:28px;height:28px;font-size:16px;color:#8a8272;cursor:pointer}
aside h2{font-size:16px;margin:0 8px 8px 0;padding-right:26px}
aside .tag{display:inline-block;font-size:11px;padding:2px 8px;border-radius:99px;
 background:#f2efe8;color:#7a7364;margin:0 4px 8px 0}
aside .band{background:#fff5f2;border-left:3px solid #b3261e;padding:9px 11px;
 border-radius:0 6px 6px 0;margin:10px 0}
aside .band b{color:#b3261e}
aside dl{margin:10px 0 0}aside dt{font-size:11.5px;color:var(--sub);margin-top:10px}
aside dd{margin:2px 0 0}
aside .src{font-size:11.5px;color:#8a8272;margin-top:3px}
aside .src a{color:#6b6455}
aside .assumed{background:#f7f5ef;border-left:3px solid #b9b3a7;padding:9px 11px;border-radius:0 6px 6px 0;margin:10px 0;font-size:12.5px}
aside .assumed b{color:#6b6455}
aside .shut{background:#f4f1ea;border-left:3px solid #9b8f86;padding:9px 11px;border-radius:0 6px 6px 0;margin:10px 0}
aside .shut b{color:#6b6455}
aside .note{background:#f7f5ef;padding:9px 11px;border-radius:6px;margin:10px 0}
footer{color:var(--sub);font-size:11.5px;margin-top:22px;
 border-top:1px solid var(--rule);padding-top:12px;line-height:1.8}
@media(max-width:760px){.wrap{padding:16px 14px 30px}.legend .axis{margin-left:0}}
"""

JS = """
const P=document.getElementById('panel'),B=document.getElementById('body');
const esc=s=>String(s??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const SB={LAWYER:'弁護士',JUDICIAL_SCRIVENER:'司法書士',TAX_ACCOUNTANT:'税理士',
 ADMINISTRATIVE_SCRIVENER:'行政書士',LAND_SURVEYOR:'土地家屋調査士',
 INTERNATIONAL_LAWYER:'海外の弁護士',INTERNATIONAL_TAX_LAWYER:'国際税務の専門家'};
const ST={DONE:'済んだ',READY:'手をつけられる',IN_PROGRESS:'着手している',
 WAITING:'相手の返事を待っている',NOT_READY:'まだ開かない'};
const VF={SELF_REVIEWED:'まだ専門家の確認を受けていません',
 SOURCE_VERIFIED:'一次資料で確かめました',EXPERT_VERIFIED:'専門家の確認を受けました',
 DRAFT:'下書き'};
function show(id){
  const d=D[id];if(!d)return;
  let h='<h2>'+esc(d.title)+'</h2>';
  h+='<span class="tag">'+esc(d.stream)+'</span>';
  if(!d.closed)h+='<span class="tag">'+esc(d.present?(ST[d.status]||d.status):(d.fog?'まだ分からない':'通らない道'))+'</span>';
  if(d.istoday)h+='<span class="tag" style="background:#fdece4;color:#c2410c">今日決めること '+d.istoday+'</span>';
  if(d.closed)h+='<span class="tag">期間が過ぎている可能性</span>';
  if(d.verif)h+='<span class="tag">'+esc(VF[d.verif]||d.verif)+'</span>';
  if(!d.present){
    h+='<div class="note">'+esc(d.absent||'')+'</div>';
    if(d.fog)h+='<p class="why">これは「通らなくて済む」という意味ではありません。'
      +'まだうかがっていないので、決まっていないだけです。</p>';
    B.innerHTML=h;P.classList.remove('closed');return;}
  if(d.closed)h+='<div class="shut"><b>この期間は、もう動かせないかもしれません。</b><br>'
    +esc(d.closednote)+'</div>';
  if(d.fact)h+='<div class="band"><b>【事実】</b> '+esc(d.fact)+'<br><br><b>【ただし】</b> '+esc(d.exc)
    +(d.lost?'<br><br><span style="font-size:11.5px;color:#7a7364">失われるもの … '+esc(d.lost)+'</span>':'')+'</div>';
  h+='<dl>';
  h+='<dt>やること</dt><dd>'+esc(d.today)+'</dd>';
  if(d.assumed)h+='<div class="assumed"><b>この日数は、亡くなった日から数えたものです。</b><br>'
    +'この3か月は、ほんとうは「自分が相続人になったと知った日」から始まります。'
    +'相続人ごとに、別々に進みます。先の順位の人が放棄したためにあなたが相続人に'
    +'なったのなら、あなたの3か月は、それを知った日から始まります。'
    +'知った日が違えば、ここの日数も変わります。</div>';
  if(d.deadline)h+='<dt>期限</dt><dd>'+esc(d.deadline)
    +(d.days!=null&&d.days>=0?'（'+(d.assumed?'およそ ':'')+'あと '+d.days+' 日）':'')
    +(d.days!=null&&d.days<0&&!d.closed?'（'+(-d.days)+' 日過ぎています）':'')
    +(d.dlsrc?'<div class="src">確かめた先 … '+(d.dlurl
        ?'<a href="'+esc(d.dlurl)+'" target="_blank" rel="noopener">'+esc(d.dlsrc)+'</a>'
        :esc(d.dlsrc))+(d.dlat?'（'+esc(d.dlat)+' 確認）':'')+'</div>':'')
    +'</dd>';
  if(d.blocked&&d.blocked.length)h+='<dt>いま止まっている理由</dt><dd>「'+d.blocked.map(esc).join('」「')+'」が終わっていません</dd>';
  if(d.unlocks&&d.unlocks.length)h+='<dt>ここが済むと開きます</dt><dd>'+d.unlocks.map(esc).join('、')+'</dd>';
  if(d.why)h+='<dt>あなたにしかできない理由</dt><dd>'+esc(d.why)+'</dd>';
  if(d.sb[0])h+='<dt>相談するなら</dt><dd>'+esc(SB[d.sb[0]]||d.sb[0])+'<br>'+esc(d.sb[1])+'</dd>';
  h+='</dl>';
  if(d.caution)h+='<div class="note">※ '+esc(d.caution)+'</div>';
  B.innerHTML=h;P.classList.remove('closed');
}
document.querySelectorAll('.st,.chip').forEach(g=>{
  g.addEventListener('click',()=>show(g.dataset.id));
  g.addEventListener('keydown',e=>{if(e.key==='Enter')show(g.dataset.id);});
});
document.getElementById('x').addEventListener('click',()=>P.classList.add('closed'));
document.addEventListener('keydown',e=>{if(e.key==='Escape')P.classList.add('closed');});
"""


def write(key, today):
    body, sc = render(key, today)
    notice = ("これは、手続の順序と期限を示す地図です。個別の法律相談や税務相談ではありません。"
              "あなたの場合にどうなるかは、記載の相談先で確かめてください。"
              "制度は変わります。多くの項目は、まだ専門家の確認を受けていません。")
    page = (f'<!doctype html><html lang="ja"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>しおり／地図 — {html.escape(sc["title"])}</title>'
            f'<style>{CSS}</style></head><body>{body}'
            f'<div class="wrap" style="padding-top:0"><footer>{notice}</footer></div>'
            f'<script>{JS}</script></body></html>')
    out = ROOT / "docs" / "submission" / f"map_{key}.html"
    out.write_text(page, encoding="utf-8")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("-s", "--scenario", default="surv-day10-nomemo")
    ap.add_argument("--today", default=None)
    ap.add_argument("-o", "--out", default=None)
    args = ap.parse_args()
    today = date.fromisoformat(args.today) if args.today else date.today()

    if args.scenario == "all":
        for k in SCENARIOS:
            print(f"{write(k, today).relative_to(ROOT)}　（{k}）")
        sys.exit(0)

    if args.scenario not in SCENARIOS:
        print(f"そんな状況はありません: {args.scenario}", file=sys.stderr)
        sys.exit(1)

    if args.out:
        body, sc = render(args.scenario, today)
        Path(args.out).write_text(body, encoding="utf-8")
        print(args.out)
    else:
        print(f"{write(args.scenario, today).relative_to(ROOT)}　（{args.scenario}）")
