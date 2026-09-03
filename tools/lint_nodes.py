#!/usr/bin/env python3
"""
しおり ノード定義の点検（Lint）

    python tools/lint_nodes.py

JSONを一文字も書き換えないまま、次を機械的に調べる。

  1. JSONとして読めるか
  2. 必須の項目が揃っているか、余計な項目がないか
  3. id が重複していないか、命名の規則に合っているか
  4. prerequisites が存在するノードを指しているか（同じファイル内に限る）
  5. mitigates_nodes が存在するノードを指しているか（本人→遺族は可）
  6. prerequisites に循環がないか
  7. 列挙の値が定義どおりか
  8. exists_if に使われている condition が conditions.json にあるか
  9. verification_status と verified_at が食い違っていないか
 10. irreversibility に事実と例外の両方があるか
 11. deadline が days と months を同時に持っていないか
 12. 「今日の3件」が成立するか（直後に出るノードの数）

エラーが0件なら終了コード0。1件でもあれば1。
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NODE_DIR = ROOT / "data" / "nodes"
SCHEMA_DIR = ROOT / "data" / "schema"

FILES = ["nodes_self.json", "nodes_family.json", "nodes_survivor.json"]

REQUIRED = [
    "id", "stream", "actor", "title", "today_action",
    "exists_if", "prerequisites", "base_urgency", "priority",
    "wait", "durability", "legal_metadata",
]

ALLOWED = set(REQUIRED) | {
    "persona_labels", "why_only_you", "decided_by_you",
    "is_warning_only", "absent_note",
    "mitigates_nodes", "irreversibility", "caution",
    "deadline", "specialist_bridge",
}

ENUMS = {
    "stream": {"ESTATE_SUCCESSION", "POST_MORTEM_AFFAIRS", "BUSINESS_SUCCESSION",
               "CROSS_BORDER", "FAMILY_BRIDGING", "HANDOVER"},
    "actor": {"SELF", "FAMILY_CHILD", "RELATIVES", "LEGAL_HEIR"},
    "base_urgency": {"MUST_IMMEDIATE", "CAN_WAIT"},
    "durability": {"PERMANENT", "REFRESHABLE", "VOLATILE"},
}

PERSONA = {"ALL", "PENSIONER", "LANDOWNER", "BUSINESS_OWNER", "FOREIGN_ASSETS", "DEBT_CONCERN"}
EFFECT = {"SKIPPED", "SHORTENED", "RISK_REDUCED"}
WAIT_NATURE = {"NONE", "EXTERNAL_WAIT"}
VERIF = {"DRAFT", "SELF_REVIEWED", "SOURCE_VERIFIED", "EXPERT_VERIFIED"}
VERIF_NEEDS_DATE = {"SOURCE_VERIFIED", "EXPERT_VERIFIED"}
SPECIALIST = {"LAWYER", "JUDICIAL_SCRIVENER", "TAX_ACCOUNTANT", "ADMINISTRATIVE_SCRIVENER",
              "LAND_SURVEYOR", "INTERNATIONAL_LAWYER", "INTERNATIONAL_TAX_LAWYER"}

ID_RE = re.compile(r"^(SELF|FAMILY|SURV)_[A-Z0-9_]+$")
COND_RE = re.compile(r"condition\.([a-z_][a-z0-9_]*)")

errors = []
warnings = []


def err(where, msg):
    errors.append(f"[NG] {where}: {msg}")


def warn(where, msg):
    warnings.append(f"[  ] {where}: {msg}")


# ---- 読み込み -------------------------------------------------------------

docs = {}
for name in FILES:
    path = NODE_DIR / name
    if not path.exists():
        err(name, "ファイルがない")
        continue
    try:
        docs[name] = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        err(name, f"JSONとして読めない（{e.lineno}行目 {e.colno}文字目）: {e.msg}")

cond_path = SCHEMA_DIR / "conditions.json"
known_conditions = set()
if cond_path.exists():
    known_conditions = set(json.loads(cond_path.read_text(encoding="utf-8"))["conditions"].keys())
else:
    err("conditions.json", "ファイルがない")

if errors:
    print("\n".join(errors))
    sys.exit(1)

# ---- 索引 ---------------------------------------------------------------

all_ids = {}          # id -> file
by_file = {}          # file -> {id: node}

for name, doc in docs.items():
    by_file[name] = {}
    for node in doc.get("nodes", []):
        nid = node.get("id", "(id なし)")
        if nid in all_ids:
            err(nid, f"id が重複している（{all_ids[nid]} と {name}）")
        all_ids[nid] = name
        by_file[name][nid] = node

# ---- 各ノードの点検 -------------------------------------------------------

for name, nodes in by_file.items():
    for nid, node in nodes.items():
        w = f"{name}/{nid}"

        for key in REQUIRED:
            if key not in node:
                err(w, f"必須の項目がない: {key}")
        for key in node:
            if key not in ALLOWED:
                err(w, f"定義にない項目がある: {key}")

        if not ID_RE.match(nid):
            err(w, "id の付け方が規則に合わない（SELF_ / FAMILY_ / SURV_ で始まる大文字とアンダースコア）")

        for key, allowed in ENUMS.items():
            if key in node and node[key] not in allowed:
                err(w, f"{key} の値が定義にない: {node[key]}")

        for label in node.get("persona_labels", []):
            if label not in PERSONA:
                err(w, f"persona_labels の値が定義にない: {label}")

        wait = node.get("wait", {})
        if wait.get("nature") not in WAIT_NATURE:
            err(w, f"wait.nature の値が定義にない: {wait.get('nature')}")
        if wait.get("nature") == "EXTERNAL_WAIT" and not wait.get("reason"):
            err(w, "EXTERNAL_WAIT なのに reason がない（待ちの理由を出せない）")
        if wait.get("nature") == "NONE" and wait.get("reason"):
            warn(w, "wait.nature が NONE なのに reason がある")

        # prerequisites は同じファイル内のみ
        for pre in node.get("prerequisites", []):
            if pre not in nodes:
                if pre in all_ids:
                    err(w, f"prerequisites がファイルをまたいでいる: {pre}（{all_ids[pre]}）")
                else:
                    err(w, f"prerequisites が存在しないノードを指している: {pre}")

        # mitigates は他ファイルを指してよいが、存在すること
        for m in node.get("mitigates_nodes", []):
            tgt = m.get("target_node_id")
            if tgt not in all_ids:
                err(w, f"mitigates_nodes が存在しないノードを指している: {tgt}")
            if m.get("effect") not in EFFECT:
                err(w, f"mitigates_nodes.effect の値が定義にない: {m.get('effect')}")
            if node.get("actor") == "SELF" and tgt in all_ids and not tgt.startswith("SURV_"):
                warn(w, f"本人ノードが遺族以外を mitigates している: {tgt}")

        # exists_if の condition
        expr = node.get("exists_if")
        if isinstance(expr, str):
            used = set(COND_RE.findall(expr))
            if not used:
                err(w, f"exists_if に condition.〜 が見当たらない: {expr}")
            for c in used - known_conditions:
                err(w, f"exists_if に未定義の condition がある: {c}")
            if "actor" in expr and "condition." not in expr.split("actor")[0][-15:]:
                err(w, "exists_if に actor を書いている（actor フィールドで絞ること）")

        # 検証状態
        lm = node.get("legal_metadata", {})
        st = lm.get("verification_status")
        if st not in VERIF:
            err(w, f"verification_status の値が定義にない: {st}")
        if st not in VERIF_NEEDS_DATE:
            if lm.get("verified_at") is not None:
                err(w, f"{st} なのに verified_at が入っている（確かめていないものを確かめたように見せない）")
            if lm.get("verified_by") is not None:
                err(w, f"{st} なのに verified_by が入っている")
            if lm.get("source_url"):
                warn(w, f"{st} なのに source_url がある。確かめたなら status を上げる")
        else:
            if not lm.get("verified_at") or not lm.get("verified_by"):
                err(w, f"{st} なのに verified_at / verified_by が空")
            if st == "SOURCE_VERIFIED" and not lm.get("source_url"):
                err(w, "SOURCE_VERIFIED なのに source_url がない（どこで確かめたか辿れない）")
            if lm.get("verified_at") and not re.match(r"^\d{4}-\d{2}-\d{2}$", lm["verified_at"]):
                err(w, f"verified_at の書き方が YYYY-MM-DD でない: {lm['verified_at']}")

        # 内部点検の記録。対外的な保証ではないので、status は上げない（S03 ルール8）
        ihr = lm.get("in_house_reviewed_at")
        if ihr is not None and not re.match(r"^\d{4}-\d{2}-\d{2}$", ihr):
            err(w, f"in_house_reviewed_at の書き方が YYYY-MM-DD でない: {ihr}")

        # 事実を断定するノードには、必ず相談先を置く（S03 ルール15）
        # 断定したまま「あとはご自分で」で終わらせない
        if node.get("irreversibility") and not node.get("specialist_bridge"):
            err(w, "赤い帯を出すのに specialist_bridge がない（断定したまま渡す先がない）")
        if node.get("deadline") and not node.get("specialist_bridge"):
            warn(w, "期限を持つのに specialist_bridge がない（相談先を置けないか確かめる）")

        # 不可逆
        irr = node.get("irreversibility")
        if irr is not None:
            if irr.get("is_irreversible") is not True:
                err(w, "irreversibility.is_irreversible が true でない（不可逆でないなら caution を使う）")
            if not irr.get("warning_fact"):
                err(w, "warning_fact がない（因果を断定する一文）")
            if not irr.get("warning_exception"):
                err(w, "warning_exception がない（断定の直後に逃げ道を置く）")

        # 期限
        dl = node.get("deadline")
        if dl is not None:
            kinds = [k for k in ("days", "months", "absolute_date") if dl.get(k) is not None]
            if len(kinds) > 1:
                err(w, "deadline に " + " と ".join(kinds) + " が同時にある（一つだけにする）")
            if not kinds:
                err(w, "deadline に days も months も absolute_date もない")
            if not dl.get("trigger"):
                err(w, "deadline に trigger がない（いつから数えるか／何の期限か）")
            ad = dl.get("absolute_date")
            if ad and not re.match(r"^\d{4}-\d{2}-\d{2}$", ad):
                err(w, f"absolute_date の書き方が YYYY-MM-DD でない: {ad}")
            # どの時計で数えるか（S03 ルール4・14）。書くなら値は二つだけ。
            if "clock" in dl and dl["clock"] not in {"DEATH", "KNOWN", "DIVISION"}:
                err(w, f"deadline.clock の値が定義にない: {dl['clock']}")
            if dl.get("clock") == "KNOWN" and dl.get("absolute_date"):
                err(w, "暦の期限に clock は要らない（知った時から数える期限ではない）")
            if ad and lm.get("verification_status") not in VERIF_NEEDS_DATE:
                err(w, "暦の期限を持つのに一次資料で確かめていない（制度改正で動く値である）")

        # 専門家
        sb = node.get("specialist_bridge")
        if sb is not None and sb.get("type") not in SPECIALIST:
            err(w, f"specialist_bridge.type の値が定義にない: {sb.get('type')}")

        # 赤い帯だけに出すノードは、不可逆の警告を持っていること
        if node.get("is_warning_only") and not node.get("irreversibility"):
            err(w, "is_warning_only なのに irreversibility がない（赤い帯に出す中身がない）")

        # 消えたときの一文は、条件つきのノードにしか意味がない
        if node.get("absent_note") and not node.get("exists_if"):
            err(w, "absent_note があるのに exists_if がない（このノードは消えることがない）")

        # 消える道には、必ず言葉を置く（S03 ルール14）
        # 空欄だと、利用者は「自分の場合が単純なのか、道具が隠しているのか」を区別できない
        if node.get("exists_if") and not node.get("absent_note"):
            err(w, "exists_if があるのに absent_note がない（消えた道の説明が空欄になる）")

        # 本人ノードの why_only_you
        if node.get("actor") == "SELF" and not node.get("why_only_you"):
            warn(w, "why_only_you がない（本人にしかできない理由を書くと、本人の動機になる）")

# ---- 循環参照 -----------------------------------------------------------

def find_cycle(nodes):
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {nid: WHITE for nid in nodes}
    stack = []

    def visit(nid):
        color[nid] = GRAY
        stack.append(nid)
        for pre in nodes[nid].get("prerequisites", []):
            if pre not in nodes:
                continue
            if color[pre] == GRAY:
                return stack[stack.index(pre):] + [pre]
            if color[pre] == WHITE:
                got = visit(pre)
                if got:
                    return got
        stack.pop()
        color[nid] = BLACK
        return None

    for nid in nodes:
        if color[nid] == WHITE:
            got = visit(nid)
            if got:
                return got
    return None

for name, nodes in by_file.items():
    cyc = find_cycle(nodes)
    if cyc:
        err(name, "prerequisites が循環している: " + " → ".join(cyc))

# ---- 「今日の3件」が成立するか ---------------------------------------------

surv = by_file.get("nodes_survivor.json", {})
immediate = [
    n for n in surv.values()
    if n.get("base_urgency") == "MUST_IMMEDIATE"
    and not n.get("prerequisites")
    and n.get("decided_by_you") is True
    and not n.get("is_warning_only")
]
if len(immediate) > 3:
    err("nodes_survivor.json",
        "死の直後に前提なしで出る『自分で決める』ノードが3件を超えている（"
        + ", ".join(sorted(n["id"] for n in immediate)) + "）")

# 到達不能の検出（前提が exists_if で消えるノードだけ、という場合の注意）
for name, nodes in by_file.items():
    for nid, node in nodes.items():
        pres = node.get("prerequisites", [])
        if pres and all(nodes.get(p, {}).get("exists_if") for p in pres):
            warn(f"{name}/{nid}",
                 "前提がすべて条件付きノード。前提が消えたときの扱い（S03のルール2）が効いていることを確認する")

# ---- 出力 ---------------------------------------------------------------

total_nodes = sum(len(v) for v in by_file.values())
print(f"ノード数: {total_nodes}（" + ", ".join(f"{k}: {len(v)}" for k, v in by_file.items()) + "）")

need = [(f"{all_ids[n['id']]}/{n['id']}", c)
        for nodes in by_file.values() for n in nodes.values()
        for c in n.get("legal_metadata", {}).get("needs_check", [])]
print(f"要検証の項目: {len(need)} 件")

if warnings:
    print(f"\n--- 注意 {len(warnings)} 件 ---")
    print("\n".join(warnings))

if errors:
    print(f"\n--- エラー {len(errors)} 件 ---")
    print("\n".join(errors))
    sys.exit(1)

print("\nエラーなし。")
sys.exit(0)
