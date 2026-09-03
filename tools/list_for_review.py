#!/usr/bin/env python3
"""
目視で確かめるべきノードだけを、読める形で並べる

    python tools/list_for_review.py            画面に出す
    python tools/list_for_review.py --write    docs/S08_目視で確かめる分.md に書く

Lintは形を見るだけで、中身が法律として正しいかは見ない。テストは動きを固定する
だけで、正しさは固定しない。**最初の正しさは、人が入れるしかない。**

ただし全部を読む必要はない。間違えたときに実害が出るのは、
  ・赤い帯（事実の断定と、その逃げ道）
  ・期限
を持つノードである。それ以外は言い回しの問題にとどまる。だからここだけを出す。

読んで問題がなければ verification_status を EXPERT_VERIFIED に上げる。
上げるのは人だけである（S07 第4節）。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = ["nodes_self.json", "nodes_family.json", "nodes_survivor.json"]
OUT = ROOT / "docs" / "S08_目視で確かめる分.md"


def collect():
    rows, total = [], 0
    for f in FILES:
        doc = json.loads((ROOT / "data" / "nodes" / f).read_text(encoding="utf-8"))
        for n in doc["nodes"]:
            total += 1
            if n.get("irreversibility") or n.get("deadline"):
                rows.append((f, n))
    return rows, total


def render(rows, total):
    out = []
    w = out.append
    w("# 目視で確かめる分")
    w("")
    w(f"全 {total} 件のうち、**{len(rows)} 件**。")
    w("")
    w("赤い帯（事実の断定）か期限を持つノードだけを並べた。間違えたときに実害が出るのはここである。"
      "残りは言い回しの問題にとどまるので、ここには出さない。")
    w("")
    w("読んで問題がなければ `verification_status` を `EXPERT_VERIFIED` に上げる。"
      "**上げられるのは人だけである。** エージェントはここへ上げられない。")
    w("")
    w("*このファイルは `python tools/list_for_review.py --write` で作り直せる。手で直さない。*")
    w("")
    w("---")

    for f, n in rows:
        lm = n.get("legal_metadata", {})
        w("")
        w(f"## {n['title']}")
        w("")
        w(f"`{n['id']}`　{f}　検証状態 **{lm.get('verification_status')}**")
        w("")
        w(f"**やること**　{n['today_action']}")

        dl = n.get("deadline")
        if dl:
            if dl.get("absolute_date"):
                when = f"{dl['absolute_date']} まで（暦の期限）"
            elif dl.get("months") is not None:
                when = f"{dl['months']} か月"
            elif dl.get("days") is not None:
                when = f"{dl['days']} 日"
            else:
                when = "（期間の指定なし）"
            clock = "知った時から" if dl.get("clock") == "KNOWN" else "死亡日から"
            w("")
            w(f"**期限**　{when}　／　起算点「{dl.get('trigger')}」　／　時計 {clock}")
            if dl.get("note"):
                w("")
                w(f"> {dl['note']}")

        irr = n.get("irreversibility")
        if irr:
            w("")
            w(f"**失われるもの**　{irr['lost_option']}")
            w("")
            w(f"**【事実】** {irr['warning_fact']}")
            w("")
            w(f"**【ただし】** {irr['warning_exception']}")

        if n.get("caution"):
            w("")
            w(f"**注意書き**　{n['caution']['note']}")

        if lm.get("source_draft"):
            w("")
            w(f"**根拠（下書き）**　{lm['source_draft']}")
        if lm.get("source_url"):
            w(f"　{lm['source_url']}")

        checks = lm.get("needs_check") or []
        if checks:
            w("")
            w("**まだ確かめていないこと**")
            w("")
            for c in checks:
                w(f"- [ ] {c}")

        w("")
        w("**判定**　[ ] このままでよい　　[ ] 直す（下に書く）")
        w("")
        w("---")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    rows, total = collect()
    text = render(rows, total)
    if "--write" in sys.argv:
        OUT.write_text(text, encoding="utf-8")
        print(f"{len(rows)} 件 / 全 {total} 件 → {OUT.relative_to(ROOT)}")
    else:
        print(text)
