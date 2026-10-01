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
# 線と印の色。**動画に落としたときに残るか**で決める。淡い灰は圧縮で消える。
# 座標には触らない（矢印の通り道は現状のまま）。濃さだけを上げる。
C = {
    "bg": "#fbfaf7", "ink": "#1a202c", "sub": "#2d3748", "line": "#9a9384",
    "done": "#3a6647", "today": "#a83c08", "ready": "#1f4e79",
    "notready": "#ffffff", "absent": "#c4bfb3", "fog": "#6f6790",
    "reset": "#a01c16", "lane": "#f4f1ea", "closed": "#7d7263",
}

# 出来事は、画面では日本語で言う。生の識別子は出さない。
EVENT_LABEL = {
    "NEW_WILL_FOUND": "あとから遺言が出てきたら",
    "UNKNOWN_DEBT_FOUND": "知らない借金が出てきたら",
    "NEW_HEIR_FOUND": "知らない相続人がいたら",
    "HEIR_RENOUNCED": "誰かが放棄したら",
    "FOREIGN_ASSET_FOUND": "海外の資産が出てきたら",
}

# 「状況に応じて必要になる手続き」の中だけで使う見出し。
# 流れの名前（STREAM_LABEL）は路線と札で使うので短くしてある。
# 霧の箱では、なぜここに入っているのかが分かる言い方に替える。
FOG_GROUP_LABEL = {
    "ESTATE_SUCCESSION": "■ 相続税の申告・財産承継に関する確認",
}
FOG_GROUP_NOTE = {
    "ESTATE_SUCCESSION": "以下の項目は、遺産の総額や特例の条件によって対応が必要になります。",
}

# 深さの見出し。左が先、右があと。時刻ではなく順序である。
# 時期（○日以内）ではなく順序を言う。列は前後関係の深さであり、
# 同じ列に3か月の期限と10か月の期限が並ぶことがあるため、時期を書くと誤認を招く。
DEPTH_LABEL = ["まず", "それが整ったら", "その次に", "さらにその先へ",
               "完了へ", "その先"]

DX, DY, PAD, R = 236, 88, 40, 16
HEAD_H = 44          # 流れの見出しに使う高さ
RESET_H = 56         # 戻りの矢印ひとつ分の高さ
LINE_CH = 13         # 駅名の一行の文字数
TOP = 58             # 深さの見出しに使う高さ


LINE_MAX = 17        # 区切りのよい所で折るときに許す、一行の最大文字数

# ここで折ると読みやすい所。閉じ括弧・読点のあと、開き括弧の前。
_BREAK_AFTER = "、。』」）)"
_BREAK_BEFORE = "（(『「"
_PARTICLES = "をにのでがはともへ"


def wrap2(s, ch=LINE_CH):
    """
    駅名を二行に折る。日本語は単語で折れないので、
    まず括弧や読点の切れ目で、なるべく左右の長さがそろう所を探す（一行 LINE_MAX 字まで）。
    見つからなければ、これまでどおり文字数で折る。
    """
    s = str(s or "")
    if len(s) <= ch:
        return [s]
    def hira(c):
        return "\u3041" <= c <= "\u309f"

    best = None
    for i in range(1, len(s)):
        if s[i - 1] in _BREAK_AFTER or s[i] in _BREAK_BEFORE:
            rank = 0                      # 括弧・読点の切れ目をいちばんに選ぶ
        elif s[i - 1] in _PARTICLES and not hira(s[i]):
            rank = 1                      # 次に、助詞のあと（次の字が漢字など）で折る
        else:
            continue
        a, b = i, len(s) - i
        if a <= LINE_MAX and b <= LINE_MAX:
            key = (rank, max(a, b))
            if best is None or key < best[0]:
                best = (key, i)
    if best:
        return [s[:best[1]], s[best[1]:]]
    if len(s) <= ch * 2:
        return [s[:ch], s[ch:]]
    return [s[:ch], s[ch:ch * 2 - 1] + "…"]


def short_deadline(m):
    """
    駅の上に出す、期限の短い札。起算点と法定の期間だけを書く（残り日数は書かない）。
    残り日数は、起算日の置き方しだいで変わるので、駅をひらいたパネルの側で
    「およそ」の注記とともに出す。
    """
    dl = m.get("deadline_raw") or {}
    trig = dl.get("trigger") or ""
    if dl.get("absolute_date"):
        y, mo, d = str(dl["absolute_date"]).split("-")
        return f"{int(y)}年{int(mo)}月{int(d)}日まで"
    if "知っ" in trig:
        head = "知ってから"
    elif "遺産分割" in trig:
        head = "分割成立から"
    else:
        head = "起算から"
    if dl.get("months"):
        n = int(dl["months"])
        return f"{head}{n // 12}年猶予" if n % 12 == 0 else f"{head}{n}か月猶予"
    if dl.get("days"):
        return f"{head}{int(dl['days'])}日猶予"
    return "期限あり"


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
                       if lo <= pos[r["node_id"]][0] <= hi) + 84
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
    raw_dl = {k: (v.get("deadline") or {}) for k, v in
              (raw.items() if isinstance(raw, dict) else ((n["id"], n) for n in raw))}
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
            # 期限の根拠（dlurl）は期限を持つノードにしか付かない。
            # 監査ドロワーは期限の有無にかかわらず、そのノードの一次資料を指したい。
            "srcurl": (n.get("legal_metadata") or {}).get("source_url") or "",
            "blocked": m["blocked_by"], "unlocks": m["unlocks"],
            "fact": irr.get("warning_fact", ""), "exc": irr.get("warning_exception", ""),
            "lost": irr.get("lost_option", ""),
            "caution": (n.get("caution") or {}).get("note", ""),
            "sb": (sb.get("type", ""), sb.get("concrete_query", "")),
            "absent": m["absent_reason"], "fog": m["fog"],
            "verif": m["verification_status"],
            # この駅から分かれる道（ドロワー上部のミニ路線図に使う）
            "branches": (raw.get(nid) or {}).get("branches"),
            "short": (raw.get(nid) or {}).get("short_title") or "",
            "important": (raw.get(nid) or {}).get("important_note") or "",
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
    a(f'<p class="meta">{today.isoformat()} 時点</p>')
    a('<div class="counts">'
      f'対象の手続き <b>{prog["present"]}</b> 件'
      f'<span class="sep">｜</span>完了 <b>{done_n}</b> 件'
      + (f'<span class="sep">｜</span>要確認（まだ分からない） <b>{prog["fog"]}</b> 件'
         if prog["fog"] else "")
      + f'<span class="sep">｜</span>対象外 <b>{prog["absent"] - prog["fog"]}</b> 件'
      f'<span class="sep">　</span>（全 {prog["total"]} 件）</div>')
    a(f'<div class="bar"><i style="width:{pct}%"></i></div>')
    a('</div>')

    a('<div class="legend">'
      '<span><i class="k done"></i>完了</span>'
      '<span><i class="k ready"></i>いま着手できる</span>'
      '<span><i class="k today"></i>今日決める</span>'
      '<span><i class="k notready"></i>前の手続き待ち</span>'
      '<span><b class="kdl">知ってから</b>期限あり</span>'
      '<span><i class="k warnk"></i>通り過ぎると戻れない</span>'
      '<span><i class="k resetk"></i>条件による戻り線</span>'
      '<span><i class="k expk"></i>主要駅</span>'
      '<span><i class="k lock"></i>各駅</span>'
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
        # 見出しは囲って置く。駅や矢印の座標には触らない（囲みは見出しの周りだけ）。
        bw = 48 + len(lab) * 14      # 「1. 」の番号ぶんも含めて囲む
        a(f'<rect x="{x - bw // 2}" y="{TOP-34}" width="{bw}" height="24" '
          f'rx="12" class="colbox"/>')
        a(f'<text x="{x}" y="{TOP-17}" text-anchor="middle" class="col">'
          f'{k+1}. {esc(lab)}</text>')
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
    # 札を線の上に載せるとき、駅の丸・駅名・期限の札やほかの札に重ねないための箱
    rl_obst = []
    for oid, (ox, oy) in pos.items():
        on = raw.get(oid) or {}
        lines = wrap2(on.get("short_title") or nodes[oid]["title"])
        w = max(len(s_) for s_ in lines) * 13 + 6
        rl_obst.append((ox - R - 2, oy - R - 2, ox + R + 2, oy + R + 2))
        rl_obst.append((ox - w / 2, oy + R + 4, ox + w / 2, oy + R + 6 + 15 * len(lines)))
        if raw_dl.get(oid):
            hw_ = len(short_deadline({"deadline_raw": raw_dl.get(oid)})) * 6 + 8
            rl_obst.append((ox - hw_, oy - R - 24, ox + hw_, oy - R - 6))
    reset_boxes = []
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
        # 札は、戻り線の底（いちばん低く、平らな所）に、線の真上に重ねて載せる。
        l1 = f"{EVENT_LABEL.get(ev, ev)}、"
        l2 = "ここへ戻ります"
        hw = max(len(l1), len(l2)) * 6 + 4
        P = [(x1, y1 + R), (x1, yb), (x2, yb), (x2, y2 + R + 9)]
        Q = P[::-1] if P[3][0] < P[0][0] else P

        def bez(u):
            c = [(1 - u) ** 3, 3 * (1 - u) ** 2 * u, 3 * (1 - u) * u ** 2, u ** 3]
            return (sum(c[i] * Q[i][0] for i in range(4)), sum(c[i] * Q[i][1] for i in range(4)))

        pts = [bez(i / 80) for i in range(81)]
        cum = [0.0]
        for i in range(1, 81):
            cum.append(cum[-1] + ((pts[i][0] - pts[i - 1][0]) ** 2
                                  + (pts[i][1] - pts[i - 1][1]) ** 2) ** 0.5)

        def at(f):                           # 線の長さの割合 f の位置
            target = cum[-1] * f
            i = next(k for k in range(81) if cum[k] >= target)
            return pts[i]

        pick = None
        # 札を傾けないため、線のいちばん低い所（底の平らな所）とその前後だけを候補にする。
        # 両端の駅の高さが違うと、長さの真ん中は坂の途中に来てしまうので使わない
        low = max(range(81), key=lambda k: pts[k][1])
        f0 = cum[low] / cum[-1]
        for f in (f0, f0 - 0.04, f0 + 0.04, f0 - 0.08, f0 + 0.08):
            mx, my = at(f)
            bx = (mx - hw, my - 10, mx + hw, my + 22)
            if not any(not (bx[2] < o[0] or bx[0] > o[2] or bx[3] < o[1] or bx[1] > o[3])
                       for o in rl_obst + reset_boxes):
                pick = (f, bx)
                break
        if pick is None:
            mx, my = at(f0)
            pick = (f0, (mx - hw, my - 10, mx + hw, my + 22))
        f, bx = pick
        reset_boxes.append(bx)
        mx, my = at(f)
        # 一行目は線の真上に重ねる（文字の縁取りが下の点線を隠す）。二行目はそのすぐ下
        a(f'<text x="{mx:.1f}" y="{my+4:.1f}" class="resetlbl" text-anchor="middle">{esc(l1)}</text>')
        a(f'<text x="{mx:.1f}" y="{my+19:.1f}" class="resetlbl" text-anchor="middle">{esc(l2)}</text>')

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

        # 急行（主要駅）は太い枠、各駅は小さく控えめに。指定の無い駅は、これまでどおり。
        rn = raw.get(nid) or {}
        exp = rn.get("express")
        rr = R - 5 if exp is False else R
        sw = 4.6 if exp else (2 if exp is False else 2.8)
        a(f'<g class="st {cls}" data-id="{esc(nid)}" tabindex="0" role="button">')
        if nid in warn:                     # 通り過ぎると戻れない駅は、二重丸
            a(f'<circle cx="{x}" cy="{y}" r="{R+5}" fill="none" '
              f'stroke="{C["reset"]}" stroke-width="1.5" opacity=".6"/>')
        dash = ' stroke-dasharray="4 3"' if d["closed"] else ""
        a(f'<circle cx="{x}" cy="{y}" r="{rr}" fill="{fill}" stroke="{stroke}" '
          f'stroke-width="{sw}"{dash}/>')
        if m["status"] == "DONE":
            a(f'<path d="M{x-6},{y} l4.5,4.5 l8,-9" fill="none" stroke="#fff" '
              f'stroke-width="2.6" stroke-linecap="round"/>')
        elif d["istoday"]:
            a(f'<text x="{x}" y="{y+5}" text-anchor="middle" class="num">'
              f'{d["istoday"]}</text>')
        # 駅名は、短い名詞（short_title）があればそれを使う。長い説明はパネル・ドロワーの側へ。
        scls = "stt" + (" exp" if exp else (" loc" if exp is False else ""))
        for i, ln in enumerate(wrap2(rn.get("short_title") or m["title"])):
            a(f'<text x="{x}" y="{y+R+16+i*15}" text-anchor="middle" class="{scls}">'
              f'{esc(ln)}</text>')
        # 期限の札。済んだ駅には出さない（days_left が None になっている）。
        # 門が閉じた駅は、日数ではなく「過ぎている可能性」と書く。断定しない。
        if m["status"] == "DONE":
            lab = None
        elif d["closed"]:
            lab = "期間が過ぎている可能性"
        elif m["deadline_label"]:
            # 駅の上には、起算点と法定の期間だけを書く（例：知ってから3か月）。
            # 残り日数は、起算日を聞いていないと断定できないので（S03 ルール4・15）、
            # 駅をひらいたパネルの側で「およそ」の注記とともに出す。
            lab = short_deadline({"deadline_raw": raw_dl.get(nid)})
        else:
            lab = None
        if lab:
            cl = ("dl closed" if d["closed"]
                  else ("dl assumed" if m.get("clock_assumed") else "dl"))
            if rn.get("deadline_boxed") and not d["closed"]:
                # 見落としやすい期限は、札を枠で囲んで目立たせる
                bw = len(lab) * 11.5 + 14
                a(f'<rect x="{x-bw/2}" y="{y-R-24}" width="{bw}" height="20" rx="4" '
                  f'class="dlbox"/>')
            a(f'<text x="{x}" y="{y-R-9}" text-anchor="middle" class="{cl}">'
              f'{esc(lab)}</text>')
        a('</g>')

    # 乗り換え案内（点線のヒゲ）。分かれる道のうち、行き先の駅が地図に描かれていないものだけ。
    # 本線や駅名・期限の札に重ならないよう、駅の右上へ短く伸ばし、小さな札を置く。
    # ほかの駅の丸・駅名・期限の札の占める箱（ヒゲの札をその上に置かないため）
    occupied = list(reset_boxes)          # 戻り線の札の上にも、ヒゲの札を置かない
    for oid, (ox, oy) in pos.items():
        on = raw.get(oid) or {}
        lines = wrap2(on.get("short_title") or nodes[oid]["title"])
        fw = 13 if on.get("express") else (11.5 if on.get("express") is False else 12)
        w = max(len(s) for s in lines) * fw + 6
        occupied.append((ox - R - 2, oy - R - 2, ox + R + 2, oy + R + 2))
        occupied.append((ox - w / 2, oy + R + 4, ox + w / 2, oy + R + 6 + 15 * len(lines)))
        if raw_dl.get(oid):
            hw_ = len(short_deadline({"deadline_raw": raw_dl.get(oid)})) * 6 + 8
            occupied.append((ox - hw_, oy - R - 24, ox + hw_, oy - R - 6))

    def hits(b):
        return any(not (b[2] < o[0] or b[0] > o[2] or b[3] < o[1] or b[1] > o[3]) for o in occupied)

    for nid, (x, y) in pos.items():
        br = (raw.get(nid) or {}).get("branches")
        if not br:
            continue
        k = 0
        for p in br.get("paths", []):
            if not p.get("whisker") or (p.get("to_node") and p["to_node"] in pos):
                continue
            if k >= 2:                      # 1駅あたり2本まで。本線の邪魔をしない
                break
            # 案内は二行に分けて、点線の四角で囲む（「：」の後ろで改行する）
            wt = p["whisker"]
            wl = [wt[:wt.index("：") + 1], wt[wt.index("：") + 1:].strip()] if "：" in wt else [wt]
            bw = max(len(t_) for t_ in wl) * 11.5 + 14
            bh = len(wl) * 15 + 10
            # 置き場所の候補：右上 → 右下 → さらに上 → さらに下。ほかの駅名などに重ならない所を選ぶ
            ex = x + 74
            pick = None
            cys = (y - 44, y + 46, y - 92, y + 94, y - 140, y + 142)
            for cy in cys:
                box = (ex, cy - bh / 2, ex + bw, cy + bh / 2)
                if not hits(box):
                    pick = (cy, box)
                    break
            if pick is None:
                cy = cys[0]
                pick = (cy, (ex, cy - bh / 2, ex + bw, cy + bh / 2))
            cy, box = pick
            sy = y - 6 if cy < y else y + 6
            occupied.append(box)
            a(f'<g class="whg" aria-label="{esc(wt)}">')
            a(f'<path d="M{x+R+2},{sy} L{ex},{cy}" fill="none" stroke="#5b5646" '
              f'stroke-width="1.6" stroke-dasharray="4 3" class="whisker"/>')
            a(f'<rect x="{ex}" y="{cy-bh/2}" width="{bw}" height="{bh}" rx="4" class="whbox"/>')
            for i_, t_ in enumerate(wl):
                a(f'<text x="{ex+7}" y="{cy-bh/2+17+i_*15}" class="whlbl">{esc(t_)}</text>')
            a('</g>')
            k += 1
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
        a(f'<details class="side fog" open><summary>状況に応じて必要になる手続き'
          f'（未確定 <b>{n}</b>件）</summary>'
          f'<p class="why">財産の状況や意向によって、対応が必要になるかどうかが変わる手続きです。'
          f'<strong>（『不要と決まった』という意味ではありません）</strong></p>')
        for s, v in fogs:
            a(f'<div class="grp"><h3>{esc(FOG_GROUP_LABEL.get(s, STREAM_LABEL.get(s, s)))}</h3>')
            if s in FOG_GROUP_NOTE:
                a(f'<p class="grpnote">{esc(FOG_GROUP_NOTE[s])}</p>')
            a(f'{chips(v)}</div>')
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
/* 色と大きさは、画面で読めるかではなく、**動画にしたときに読めるか**で決める。
   淡い灰色は紙では上品だが、圧縮された映像では消える。 */
:root{--bg:#fbfaf7;--ink:#1a202c;--sub:#2d3748;--dim:#4a5568;--rule:#d8d2c4}
:root{color-scheme:light}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
 font-family:"Hiragino Kaku Gothic ProN","Yu Gothic",system-ui,sans-serif}
.wrap{padding:22px 24px 40px;max-width:1520px;margin:0 auto}
h1{font-size:23px;margin:0 0 5px;letter-spacing:.01em;font-weight:700}
.meta{margin:0 0 10px;color:var(--dim);font-size:13.5px}
.counts{font-size:14.5px;color:var(--sub)}
.counts b{font-size:17px;color:var(--ink)}
.counts .sep{color:#a89f8b;margin:0 9px}
.bar{height:6px;background:#eae6dc;border-radius:99px;margin:9px 0 0;max-width:420px;
 overflow:hidden}
.bar i{display:block;height:100%;background:#4a7c59;border-radius:99px}
.legend{display:flex;flex-wrap:wrap;align-items:center;gap:14px;margin:16px 0 10px;
 font-size:13.5px;color:var(--sub)}
.legend span{display:flex;align-items:center;gap:5px}
.legend .axis{color:var(--dim);margin-left:auto;font-size:12.5px}
.k{width:13px;height:13px;border-radius:50%;display:inline-block;border:2.4px solid}
.k.done{background:#4a7c59;border-color:#4a7c59}
.k.today{background:#c2410c;border-color:#c2410c}
.k.ready{background:#fff;border-color:#1f4e79}
.k.notready{background:#fff;border-color:#b9b3a7}
.k.warnk{background:#fff;border-color:#b3261e}
.k.closedk{background:#fff;border-color:#9b8f86;border-style:dashed}
.k.resetk{background:#fff;border-color:#b3261e;border-style:dashed}
.kdl{color:#a01c16;font-size:12px;font-weight:700;border:1px solid #e3b5b1;border-radius:4px;padding:0 5px;background:#fff}
.board{overflow-x:auto;background:#fff;border:1px solid var(--rule);
 border-radius:12px;padding:6px 6px 10px}
svg{display:block}
text{font-family:"Hiragino Kaku Gothic ProN","Yu Gothic",system-ui,sans-serif}
/* 順序の見出し（1 はじめに／2 これが済んでから …）。
   X軸が何を意味するかは、この一行でしか伝わらない。濃く、大きく、囲って置く。 */
.colbox{fill:#efebe1;stroke:#c9c1af;stroke-width:1}
.col{font-size:13px;fill:#1a202c;font-weight:700;letter-spacing:.04em}
.lane{font-size:15px;font-weight:700;fill:#2d3748}
.lanesub{font-size:12.5px;font-weight:400;fill:#4a5568}
.stt{font-size:12px;fill:#1a202c;stroke:#fff;stroke-width:3.4px;paint-order:stroke;stroke-linejoin:round}
.num{font-size:15px;font-weight:700;fill:#fff}
.dl.closed{fill:#6b6455;font-weight:700}
.dl.assumed{font-weight:700}
.dlbox{fill:#fff;stroke:#a01c16;stroke-width:1.6}
.dl{font-size:11.5px;fill:#a01c16;font-weight:700;stroke:#fff;stroke-width:3.4px;paint-order:stroke;stroke-linejoin:round}
.whbox{fill:#fbfaf7;stroke:#5b5646;stroke-width:1.2;stroke-dasharray:3 2}
.whlbl{font-size:11.5px;fill:#2d3748;font-weight:600;stroke:#fbfaf7;stroke-width:3px;paint-order:stroke;stroke-linejoin:round}
.resetlbl{font-size:12px;fill:#a01c16;font-weight:700;stroke:#f4f1ea;stroke-width:5px;paint-order:stroke;stroke-linejoin:round}
.st{cursor:pointer}
.st:hover circle{stroke-width:4.5}
.st:focus{outline:none}
.st:focus-visible circle{stroke-width:5;stroke:#1f4e79}
/* いま開いている駅。動画では「どこを見ているか」が分からなくなるので印を残す。
   霧のノードは駅ではなく chip で出るので、そちらにも同じ印を付ける。 */
.st.sel circle{stroke-width:5.5}
.st.sel .stt{font-weight:700}
.stt.exp{font-size:13px;font-weight:700}
.stt.loc{font-size:11.5px;fill:#4a5568}
.k.expk{width:15px;height:15px;border-width:4px;border-color:#1f4e79;background:#fff}
.k.lock{width:9px;height:9px;border-width:1.5px;border-color:#9a9384;background:#fff}
.chip.sel{background:#1f4e79;border-color:#1f4e79;color:#fff;font-weight:700}
details.fog .chip.sel{background:#1f4e79;border-color:#1f4e79;color:#fff}
details.side{margin-top:14px;background:#fff;border:1px solid var(--rule);
 border-radius:12px;padding:12px 16px}
details.side summary{cursor:pointer;font-size:14.5px;font-weight:700;color:#2d3748}
details.side summary:focus-visible{outline:2px solid #1f4e79;outline-offset:3px}
details.side.fog summary{color:#4c4470;font-size:22px;font-weight:700}
details.side.fog summary b{font-size:inherit}
details.side b{font-size:16.5px}
.hint2{font-weight:400;color:#4a5568;font-size:12.5px;margin-left:6px}
.why{font-size:13.5px;color:var(--sub);margin:9px 0 4px;line-height:1.75}
.why strong{color:#4c4470}
.grp{margin:10px 0 0}
.grp h3{font-size:13px;color:#1a1a1a;margin:0 0 4px;font-weight:700}
.grpnote{font-size:12.5px;color:#4a5568;margin:0 0 8px;line-height:1.7}
/* 押せるものは、押せると分かる形にする（枠・濃い字・触れたときの変化） */
.chip{font:inherit;font-size:13px;font-weight:500;cursor:pointer;background:#f9f8f5;color:#1a1a1a;
 border:1px solid #b8b4a8;border-radius:99px;padding:5px 13px;margin:0 5px 6px 0;
 transition:background .12s,border-color .12s,box-shadow .12s}
details.fog .chip{background:#f9f8f5;border:1px solid #b8b4a8;color:#1a1a1a}
.chip:hover{background:#efece5;border-color:#1a1a1a;box-shadow:0 1px 4px rgba(0,0,0,.08)}
.chip:focus-visible{outline:2px solid #1f4e79;outline-offset:2px}
.chip:active{background:#d9d2c0}
aside{position:fixed;top:0;right:0;width:390px;max-width:92vw;height:100vh;background:#fff;
 border-left:1px solid var(--rule);box-shadow:-14px 0 34px rgba(0,0,0,.07);
 padding:22px 24px;overflow:auto;font-size:14.5px;line-height:1.85;
 transition:transform .18s ease}
aside.closed{transform:translateX(102%)}
#x{position:absolute;top:12px;right:14px;border:1.5px solid #c9c1af;background:#f4f1ea;
 border-radius:50%;width:32px;height:32px;font-size:18px;color:#2d3748;cursor:pointer}
#x:hover{background:#e6e0d2}
#x:focus-visible{outline:2px solid #1f4e79;outline-offset:2px}
aside h2{font-size:18px;margin:0 8px 8px 0;padding-right:30px;line-height:1.5}
aside .tag{display:inline-block;font-size:12.5px;padding:3px 10px;border-radius:99px;
 background:#eee9dd;color:#2d3748;margin:0 4px 8px 0;border:1px solid #d3cbb8}
aside .band{background:#fff5f2;border-left:4px solid #a01c16;padding:10px 12px;
 border-radius:0 6px 6px 0;margin:10px 0}
aside .band b{color:#8f1710}
aside dl{margin:10px 0 0}aside dt{font-size:12.5px;color:var(--dim);margin-top:10px;font-weight:700}
/* 手順を番号で分けて書いたノードがある。改行を潰すと一行に繋がって読めなくなる。 */
aside dd{margin:2px 0 0;white-space:pre-line}
aside .src{font-size:12.5px;color:#4a5568;margin-top:3px}
aside .src a{color:#1f4e79}
aside .assumed{background:#f7f5ef;border-left:4px solid #8c8472;padding:10px 12px;border-radius:0 6px 6px 0;margin:10px 0;font-size:13.5px}
aside .assumed b{color:#4a4335}
aside .shut{background:#f4f1ea;border-left:4px solid #6b6455;padding:10px 12px;border-radius:0 6px 6px 0;margin:10px 0}
aside .shut b{color:#4a4335}
aside .note{background:#f7f5ef;padding:10px 12px;border-radius:6px;margin:10px 0}
footer{color:#333333;font-size:14px;margin-top:22px;
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
    +(d.lost?'<br><br><span style="font-size:13px;color:#5e1410;font-weight:700">失われるもの … '+esc(d.lost)+'</span>':'')+'</div>';
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


NOTICE = ("これは、手続の順序と期限の全体像を示すための参考情報（地図）です。"
          "個別の法律相談や税務相談を提供するものではありません。"
          "法令・制度は変更される場合があり、個別のご事情によって具体的な手続きや判断は異なります。"
          "実際のお手続きにあたっては、必ず記載の公的窓口や弁護士・税理士等の専門家にご確認ください。"
          "本情報の利用により生じたいかなる結果についても、運営者は責任を負いかねます。")


def page(key, today, home=None):
    """
    一枚のHTMLにして返す。ファイルに書かずに使えるようにしてある
    （Cloud Run のうえで、その場で組んで返すため）。
    """
    body, sc = render(key, today)
    back = (f'<header class="sh-top"><div class="sh-in">'
            f'<a class="sh-brand" href="{home}"><span class="sh-mark" aria-hidden="true">栞</span>'
            f'<span class="sh-bt"><span class="sh-bn">しおり</span><span class="sh-bs">SHIORI NAVIGATOR</span></span></a>'
            f'<a class="back" href="{home}">← 表紙に戻る</a>'
            f'</div></header>') if home else ""
    return (f'<!doctype html><html lang="ja"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>しおり／地図 — {html.escape(sc["title"])}</title>'
            f'<style>{CSS}'
            f'.back{{font-size:14px;font-weight:700;color:#1f4e79;text-decoration:none;'
            f'display:inline-block;border:1.5px solid #c9c1af;border-radius:99px;'
            f'padding:5px 14px;background:#fff}}'
            f'.back:hover{{background:#f7fafd;border-color:#1f4e79}}'
            f'.back:focus-visible{{outline:2px solid #1f4e79;outline-offset:2px}}'
            f'.sh-top{{background:#fff;border-bottom:1px solid #e2dcce}}'
            f'.sh-in{{max-width:1100px;margin:0 auto;padding:12px 24px;display:flex;align-items:center;gap:18px;flex-wrap:wrap}}'
            f'.sh-brand{{display:flex;align-items:center;gap:10px;text-decoration:none;color:#1a202c}}'
            f'.sh-mark{{display:inline-flex;align-items:center;justify-content:center;width:32px;height:32px;'
            f'border-radius:8px;background:#1E3A5F;color:#fff;font-size:18px;font-weight:700;'
            f'font-family:"Noto Serif JP","Noto Serif CJK JP","Hiragino Mincho ProN","Yu Mincho",serif}}'
            f'.sh-bt{{display:flex;flex-direction:column;line-height:1.2}}'
            f'.sh-bn{{font-size:18px;font-weight:700;letter-spacing:.06em;'
            f'font-family:"Noto Serif JP","Noto Serif CJK JP","Hiragino Mincho ProN","Yu Mincho",serif}}'
            f'.sh-bs{{font-size:10px;color:#8a8f98;letter-spacing:.14em;margin-top:3px}}'
            f'</style></head><body>{back}{body}'
            f'<div class="wrap" style="padding-top:0"><footer>{NOTICE}</footer></div>'
            f'<script>{JS}</script></body></html>')


def write(key, today):
    out = ROOT / "docs" / "submission" / f"map_{key}.html"
    out.write_text(page(key, today), encoding="utf-8")
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
