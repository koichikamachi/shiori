#!/usr/bin/env python3
"""
期限の表を作る（S03 ルール4）

    python tools/deadline_table.py

このアプリで、人がいちばん先に確かめるべきものは、期限である。
期限がなければ、このアプリは意味を持たない。期限が間違っていれば、実害が出る。
そして期限は、ノード全体（75件）のうち10件にしか付いていない。

    いちばん大事で、いちばん小さい。

だから、ここだけを一枚に抜き出す。専門家に見てもらう束の筆頭がこれである。
75件を渡せば読まれない。10行なら読まれる。

出すもの
    docs/S09_期限の表.md      … 外部の専門家に渡す一枚

書かないこと
    ノードID、lint の言葉、内部の用語。読む人には要らない。
    確かめる人が要るのは「何を、いつから、どれだけ」と「その根拠」だけである。
"""
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CLOCK = {
    "DEATH": "亡くなった日",
    "KNOWN": "自己のために相続の開始があったことを知った時",
    "DIVISION": "遺産分割が成立した日",
    None: "亡くなった日",
}

GATE = {
    "STAYS_OPEN": "過ぎても、まだしなければならない",
    "CLOSES": "過ぎたら、その手続そのものができなくなる",
    None: "（未定）",
}

VERIF = {
    "DRAFT": "下書き",
    "SELF_REVIEWED": "まだ一次資料に当たっていない",
    "SOURCE_VERIFIED": "一次資料で確かめた",
    "EXPERT_VERIFIED": "専門家の点検を経た",
}

SPECIALIST = {
    "LAWYER": "弁護士", "JUDICIAL_SCRIVENER": "司法書士",
    "TAX_ACCOUNTANT": "税理士", "ADMINISTRATIVE_SCRIVENER": "行政書士",
    "LAND_SURVEYOR": "土地家屋調査士", "INTERNATIONAL_LAWYER": "海外の弁護士",
    "INTERNATIONAL_TAX_LAWYER": "国際税務の専門家",
}

SCOPE = {"nodes_survivor.json": "遺された人",
         "nodes_self.json": "本人（生きているうち）",
         "nodes_family.json": "親を気にかける家族"}


def length_of(dl):
    if dl.get("absolute_date"):
        return f'{dl["absolute_date"]} まで（暦の期限）'
    if dl.get("months") is not None:
        m = dl["months"]
        return f'{m} か月' if m < 12 else f'{m} か月（{m // 12} 年）'
    if dl.get("days") is not None:
        return f'{dl["days"]} 日'
    return "（不明）"


def collect():
    rows = []
    for f in sorted((ROOT / "data" / "nodes").glob("*.json")):
        for n in json.loads(f.read_text(encoding="utf-8"))["nodes"]:
            if not n.get("deadline"):
                continue
            rows.append((f.name, n))
    # 読む順は、遺族の急ぐものから。期間の短い順にする
    def key(t):
        dl = t[1]["deadline"]
        if dl.get("days") is not None:
            return (0, dl["days"])
        if dl.get("months") is not None:
            return (1, dl["months"])
        return (2, 0)
    return sorted(rows, key=key)


def build(today):
    rows = collect()
    n_ok = sum(1 for _, n in rows
               if (n["legal_metadata"] or {}).get("verification_status") == "SOURCE_VERIFIED")
    n_exp = sum(1 for _, n in rows
                if (n["legal_metadata"] or {}).get("verification_status") == "EXPERT_VERIFIED")

    o = []
    a = o.append
    a("# しおり — 期限の表")
    a("")
    a("**S09_期限の表**")
    a(f"{today.isoformat()} 現在 ／ BKW研究所")
    a("")
    a("---")
    a("")
    a("## これを見ていただきたい理由")
    a("")
    a("相続と、亡くなったあとの手続を、**順序と期限の地図**として示すアプリを作っています。"
      "研究途上の試作品です。")
    a("")
    a(f"手続は全部で {sum(len(json.loads(f.read_text(encoding='utf-8'))['nodes']) for f in sorted((ROOT / 'data' / 'nodes').glob('*.json')))} 件ありますが、"
      f"**期限が付いているのは、そのうち {len(rows)} 件だけ**です。")
    a("")
    a("**このアプリは、期限がなければ意味を持ちません。そして期限が間違っていれば、実害が出ます。**"
      "いちばん大事で、いちばん小さいのが、この表です。ここだけを見ていただきたいのです。")
    a("")
    a("お願いしたいのは、**制度の説明として誤りがないか**という点だけです。"
      "個別の事案についての助言をお願いするものではありません。"
      "お名前を出すかどうかはお選びいただけます（出さない場合は「弁護士の点検を経ています」等、"
      "資格の種別と点検日のみを表示します）。")
    a("")
    a("### いまの確かめ具合")
    a("")
    a("| | 件数 |")
    a("|---|---|")
    a(f"| 期限を持つ手続 | {len(rows)} |")
    a(f"| 公的機関の資料に当たって確かめたもの | {n_ok} |")
    a(f"| 資格のある専門家の点検を経たもの | **{n_exp}** |")
    a("")
    a("---")
    a("")
    a("## 一覧")
    a("")
    a("| # | 手続 | 期間 | いつから数えるか | 過ぎたら |")
    a("|---|---|---|---|---|")
    for i, (f, n) in enumerate(rows, 1):
        dl = n["deadline"]
        a(f'| {i} | {n["title"]} | {length_of(dl)} | '
          f'{CLOCK.get(dl.get("clock"))} | '
          f'{"できなくなる" if dl.get("gate") == "CLOSES" else "まだできる"} |')
    a("")
    a("---")
    a("")

    for i, (f, n) in enumerate(rows, 1):
        dl = n["deadline"]
        lm = n["legal_metadata"] or {}
        sb = n.get("specialist_bridge") or {}
        irr = n.get("irreversibility") or {}

        a(f'## {i}　{n["title"]}')
        a("")
        a(f'　　*{SCOPE.get(f, f)}の画面に出ます*')
        a("")
        a("| | |")
        a("|---|---|")
        a(f'| **期間の長さ** | {length_of(dl)} |')
        a(f'| **いつから数えるか** | {CLOCK.get(dl.get("clock"))} |')
        a(f'| **画面での書き方** | 「{dl.get("trigger")}」 |')
        a(f'| **過ぎたときの扱い** | {GATE.get(dl.get("gate"))} |')
        a(f'| **いまの確かめ具合** | {VERIF.get(lm.get("verification_status"), "—")}'
          + (f'（{lm.get("verified_at")}）' if lm.get("verified_at") else "") + ' |')
        a(f'| **典拠** | {lm.get("verified_by") or "—"} |')
        if lm.get("source_url"):
            a(f'| **出典** | {lm["source_url"]} |')
        if sb.get("type"):
            a(f'| **相談先として案内している職種** | {SPECIALIST.get(sb["type"], sb["type"])} |')
        a("")

        if dl.get("note"):
            a(f'**数え方について書いていること**　{dl["note"]}')
            a("")
        a("**根拠として持っている記述**")
        a("")
        a("> " + (lm.get("source_draft") or "（まだ書いていません）").replace("\n", "  \n> "))
        a("")

        if irr.get("warning_fact"):
            a("**画面に赤い帯で出る文（そのまま）**")
            a("")
            a(f'> 【事実】{irr["warning_fact"]}')
            a(">")
            a(f'> 【ただし】{irr.get("warning_exception", "")}')
            a("")
        if dl.get("closed_note"):
            a("**期限を過ぎているときに出る文（そのまま）**")
            a("")
            a("> " + dl["closed_note"].replace("／", "  \n> "))
            a("")
        if lm.get("needs_check"):
            a("**こちらで、まだ確かめられていないこと**")
            a("")
            for c in lm["needs_check"]:
                a(f"- {c}")
            a("")

        a("**ご返事**（あてはまるものに○、コメントは自由に）")
        a("")
        a("- [ ] このままでよい")
        a("- [ ] 直すべき　→　")
        a("- [ ] これは書くべきでない　→　")
        a("")
        a("```")
        a("")
        a("")
        a("```")
        a("")
        a("---")
        a("")

    a("## 最後に、二つだけ")
    a("")
    a("**（あ）この表にない期限で、載せるべきものはありますか。**")
    a("")
    a("```")
    a("")
    a("")
    a("```")
    a("")
    a("**（い）期限そのものより、**「いつから数えるか」**を間違えるほうが怖いと考えています。"
      "この見方は妥当でしょうか。**")
    a("")
    a("```")
    a("")
    a("")
    a("```")
    a("")
    a("---")
    a("")
    a("お忙しいところ、ありがとうございます。"
      "このアプリは無償の試作品で、個別の法律相談・税務相談への回答はしません。"
      "順序と期限を示し、**何を誰に聞けばよいか**までを示すところで止まります。")
    return "\n".join(o) + "\n"


if __name__ == "__main__":
    today = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()
    out = ROOT / "docs" / "S09_期限の表.md"
    out.write_text(build(today), encoding="utf-8")
    rows = collect()
    print(f"{out.relative_to(ROOT)}　（期限 {len(rows)} 件）")
