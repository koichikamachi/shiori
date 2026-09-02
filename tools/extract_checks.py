#!/usr/bin/env python3
"""
確かめていない項目（needs_check）を、機械的に取り出す

    python tools/extract_checks.py

data/nodes/*.json は一文字も書き換えない。読むだけである。
出力は data/checks/checks.json。エージェントへの入力になる。

1件ずつに、
  ・安定した番号（NODE_ID#n）
  ・そのノードが「いま何を主張しているか」
  ・いまの根拠（あれば）
を付ける。主張がないと、エージェントは何を確かめるのか分からない。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NODE_DIR = ROOT / "data" / "nodes"
OUT = ROOT / "data" / "checks" / "checks.json"

FILES = ["nodes_self.json", "nodes_family.json", "nodes_survivor.json"]


def claim_of(node):
    """このノードが、制度について何を言っているか。"""
    parts = [node["title"]]

    d = node.get("deadline") or {}
    if d.get("absolute_date"):
        parts.append(f"期限は {d['absolute_date']}")
    if d.get("months"):
        parts.append(f"起算から {d['months']} か月")
    if d.get("days"):
        parts.append(f"起算から {d['days']} 日")
    if d.get("trigger"):
        parts.append(f"起算点は「{d['trigger']}」")
    if d.get("note"):
        parts.append(d["note"])

    irr = node.get("irreversibility") or {}
    if irr.get("warning_fact"):
        parts.append(irr["warning_fact"])

    lm = node.get("legal_metadata") or {}
    if lm.get("source_draft"):
        parts.append(f"根拠（下書き）: {lm['source_draft']}")

    return "　".join(parts)


def main():
    items = []
    for fname in FILES:
        doc = json.loads((NODE_DIR / fname).read_text(encoding="utf-8"))
        for node in doc["nodes"]:
            lm = node.get("legal_metadata") or {}
            for i, note in enumerate(lm.get("needs_check") or [], 1):
                items.append({
                    "check_id": f"{node['id']}#{i}",
                    "node_id": node["id"],
                    "file": fname,
                    "actor": node["actor"],
                    "question": note,
                    "claim": claim_of(node),
                    "current_status": lm.get("verification_status"),
                    "current_source_url": lm.get("source_url"),
                })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps({"generated_from": FILES, "count": len(items), "checks": items},
                   ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")

    print(f"確かめる項目 {len(items)} 件 → {OUT.relative_to(ROOT)}")
    by_status = {}
    for it in items:
        by_status[it["current_status"]] = by_status.get(it["current_status"], 0) + 1
    for k, v in sorted(by_status.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v} 件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
