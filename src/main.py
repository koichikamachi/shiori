"""
しおり — 画面を作る前に、ターミナルで動かすためのもの

    python -m src.main --list                     用意した状況の一覧
    python -m src.main -s surv-day1               死の直後
    python -m src.main -s surv-day10-memo         本人のメモが見つかった
    python -m src.main -s self-owner              会社を持つ本人

ここで出す言葉が、そのまま画面の言葉になる。docs/S02 の約束と食い違っていないかを、
目で確かめるためのもの。表示の順序は docs/S03 の決めごとに従う。
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

from src.engine.engine import ShioriEngine
from src.engine.state import NodeStatus, UserCondition

ROOT = Path(__file__).resolve().parents[1]

# 提出時の「動作確認の方法」にも、そのまま使う想定の状況一覧
SCENARIOS = {
    "surv-day1": {
        "title": "遺族・亡くなった直後（1日目）。本人のメモなど何も残されていない場合",
        "scope": "SURVIVOR",
        # 1日目は、ほとんど何も分かっていない。「いいえ」ではなく「まだ分からない」である。
        "cond": dict(days_since_death=1,
                     unanswered=["has_insurance", "estate_over_basic_deduction",
                                 "has_real_estate_in_estate", "had_income_to_file",
                                 "has_foreign_assets"]),
        "done": set(),
    },
    "surv-day10-nomemo": {
        "title": "遺族・10日目。本人のメモは見つからなかった（標準の経路）",
        "scope": "SURVIVOR",
        "cond": dict(days_since_death=10, entry_found=False, has_insurance=True),
        "done": {"SURV_DEATH_NOTICE", "SURV_FUNERAL_ARRANGEMENT",
                 "SURV_KNOW_SAFE_ACTIONS", "SURV_FIND_ENTRY"},
    },
    "surv-day10-memo": {
        "title": "遺族・10日目。本人のメモが見つかった（短縮の経路）",
        "scope": "SURVIVOR",
        "cond": dict(days_since_death=10, entry_found=True, entry_locked=False, has_insurance=True),
        "done": {"SURV_DEATH_NOTICE", "SURV_FUNERAL_ARRANGEMENT",
                 "SURV_KNOW_SAFE_ACTIONS", "SURV_FIND_ENTRY"},
    },
    "surv-day80": {
        "title": "遺族・80日目。戸籍待ちのまま、放棄の期限が近づいている",
        "scope": "SURVIVOR",
        "cond": dict(days_since_death=80, entry_found=False),
        "done": {"SURV_DEATH_NOTICE", "SURV_FUNERAL_ARRANGEMENT",
                 "SURV_KNOW_SAFE_ACTIONS", "SURV_FIND_ENTRY", "SURV_WARN_DEPOSIT"},
        "statuses": {"SURV_COLLECT_KOSEKI": NodeStatus.WAITING,
                     "SURV_DISCOVER_ACCOUNTS": NodeStatus.WAITING},
    },
    "surv-new-will": {
        "title": "遺族・話し合いが済んだあとに、新しい遺言が出てきた",
        "scope": "SURVIVOR",
        "cond": dict(days_since_death=120, entry_found=False, events=["NEW_WILL_FOUND"]),
        "done": {"SURV_DEATH_NOTICE", "SURV_FUNERAL_ARRANGEMENT", "SURV_KNOW_SAFE_ACTIONS",
                 "SURV_FIND_ENTRY", "SURV_WARN_DEPOSIT", "SURV_COLLECT_KOSEKI",
                 "SURV_DETERMINE_HEIRS", "SURV_DISCOVER_ACCOUNTS", "SURV_DISCOVER_DEBT",
                 "SURV_LIST_ASSETS", "SURV_DECIDE_ACCEPT_OR_RENOUNCE", "SURV_ESTATE_DIVISION"},
    },
    "surv-rank-shift": {
        # 先生の実話。子が土壇場で放棄し、兄弟姉妹が相続人になった。
        # 死亡から100日経っているが、この人の3か月は10日前から始まっている。
        "title": "遺族・先順位が放棄して、自分が相続人になった（死後100日、知って10日）",
        "scope": "SURVIVOR",
        "cond": dict(days_since_death=100, days_since_known=10,
                     events=["HEIR_RENOUNCED"]),
        # 葬儀も届出も、先順位の人たちが100日前に済ませている。
        # この人が引き受けるのは、そこから先だけである。
        "done": {"SURV_DEATH_NOTICE", "SURV_FUNERAL_ARRANGEMENT",
                 "SURV_KNOW_SAFE_ACTIONS", "SURV_WARN_DEPOSIT", "SURV_FIND_ENTRY"},
    },
    "surv-not-heir": {
        "title": "遺族・籍を入れていない同居者（相続人ではない）",
        "scope": "SURVIVOR",
        "cond": dict(days_since_death=5, viewer_is_legal_heir=False),
        "done": set(),
    },
    "self-pensioner": {
        "title": "本人・勤めを終えて年金で暮らしている。持ち家あり",
        "scope": "SELF",
        "cond": dict(has_own_home=True, has_spouse=True, children_count=2,
                     estate_over_basic_deduction=None,
                     unanswered=["estate_over_basic_deduction"]),
        "done": set(),
    },
    "self-landowner": {
        "title": "本人・代々の土地を持っている",
        "scope": "SELF",
        "cond": dict(has_own_home=True, has_other_real_estate=True, has_leased_land=True,
                     has_spouse=True, children_count=2, estate_over_basic_deduction=True),
        "done": set(),
    },
    "self-owner": {
        "title": "本人・会社を持っている",
        "scope": "SELF",
        "cond": dict(is_business_owner=True, has_own_home=True, has_debt_or_guarantee=True,
                     has_spouse=True, children_count=1, estate_over_basic_deduction=True),
        "done": set(),
    },
    "self-foreign": {
        "title": "本人・海外に口座や不動産がある",
        "scope": "SELF",
        "cond": dict(has_foreign_assets=True, has_own_home=True, has_spouse=True,
                     estate_over_basic_deduction=True),
        "done": set(),
    },
    "self-no-child": {
        "title": "本人・子がいない。相続人は親か兄弟姉妹になる",
        "scope": "SELF",
        "cond": dict(has_spouse=True, children_count=0, has_own_home=True),
        "done": set(),
    },
    "self-no-family": {
        # 家族がいない人。亡くなった後のことを、年上の知人に頼みたい。
        "title": "本人・頼れる家族がいない。亡くなった後のことを知人に頼みたい",
        "scope": "SELF",
        "cond": dict(no_family_to_rely=True, has_spouse=False, children_count=0),
        "done": set(),
    },
    "self-surplus": {
        # 元気な70歳。年金で暮らしは足りているが、預金を子に多く残したい。
        # 守りではなく攻め。動機がほかの五人と違う。
        "title": "本人・年金で暮らしは足りている。預金を子に多く残したい",
        "scope": "SELF",
        "cond": dict(has_own_home=True, has_spouse=True, children_count=2,
                     has_surplus_savings=True,
                     estate_over_basic_deduction=None,
                     unanswered=["estate_over_basic_deduction"]),
        "done": set(),
    },
    "self-debt": {
        "title": "本人・借入金や連帯保証などがある",
        "scope": "SELF",
        "cond": dict(has_debt_or_guarantee=True, has_spouse=True, children_count=2),
        "done": set(),
    },
    "self-after-one": {
        "title": "【体験シミュレーション】銀行名を1行書き残してみた（備えによる変化を見る）",
        "scope": "SELF",
        "cond": dict(has_own_home=True, has_spouse=True, children_count=2),
        "done": {"SELF_BANK_LIST"},
    },
    "family": {
        "title": "家族・親のことが気になっているが、切り出せない",
        "scope": "FAMILY_CHILD",
        "cond": dict(),
        "done": set(),
    },
}

W = 72

# 地図の面（S03 ルール14）。流れの名前を、そのまま出せる言葉にする。
STREAM_LABEL = {
    "ESTATE_SUCCESSION": "相続と承継",
    "POST_MORTEM_AFFAIRS": "亡くなったあとの手続",
    "BUSINESS_SUCCESSION": "事業の承継",
    "CROSS_BORDER": "国境をまたぐこと",
    "FAMILY_BRIDGING": "家族に切り出す",
    "HANDOVER": "引き継ぎ",
}

MARK = {
    "DONE": "済 ",
    "READY": "● ",
    "IN_PROGRESS": "▶ ",
    "WAITING": "待 ",
    "NOT_READY": "◇ ",
}


def line(ch="─"):
    print(ch * W)


def head(text):
    print()
    line("━")
    print(text)
    line("━")


def wrap(text, indent="    ", width=W - 4):
    """日本語は単語で折り返せないので、文字数で折る。"""
    out, cur = [], ""
    for c in text:
        cur += c
        if len(cur) >= width:
            out.append(cur)
            cur = ""
    if cur:
        out.append(cur)
    return "\n".join(indent + s for s in out)


def show_map(result, scenario_key, scenario):
    """
    地図の面（S03 ルール14）。

    ここには動詞（today_action）を出さない。出すと「今日やること」が34件あるように
    見えて、今日の面の意味が消える。出すのは、名前・状態・止まっている理由・期限・
    この先に何が開くか、である。
    """
    head(f"【しおり／地図】 {scenario['title']}")
    print(f"（状況: {scenario_key}）")

    p = result["progress"]
    print()
    line()
    print("■ 全体は、こうなっています")
    line()
    print(f"  ぜんぶで {p['total']} 件。"
          f"いまのあなたに関係するのは {p['present']} 件。"
          f"通らなくてよい道が {p['absent']} 件"
          + (f"（うち、まだ分からないもの {p['fog']} 件）" if p["fog"] else "") + "。")
    print(f"  済んだもの {p['done']} 件／手をつけられるもの {p['ready']} 件／"
          f"待っているもの {p['waiting']} 件／まだ開かないもの {p['not_ready']} 件")

    fog_by_node = {a["node_id"]: a for a in result["absent"] if a["fog"]}
    fog_count = {g["key"]: g for g in result["fog_groups"]}

    # 通る流れを先に、通らない流れを後ろに束ねる。地図でも、道と景色は同じ濃さでは描かない。
    streams, order = {}, []
    for m in result["map"]:
        if m["stream"] not in streams:
            streams[m["stream"]] = []
            order.append(m["stream"])
        streams[m["stream"]].append(m)
    order.sort(key=lambda s: (0 if any(x["present"] for x in streams[s]) else 1, s))

    for stream in order:
        rows = streams[stream]
        label = STREAM_LABEL.get(stream, stream)
        st = p["by_stream"].get(stream)
        print()

        # まるごと通らない流れは、一行にまとめる（S03 ルール14）
        if not st:
            names = "／".join(r["title"] for r in rows[:2])
            more = f" ほか{len(rows) - 2}件" if len(rows) > 2 else ""
            print(f"  ── {label} ── この流れは通りません（{len(rows)}件）")
            print(f"       {names}{more}")
            continue

        print(f"  ── {label}（あなたに関係するのは {st['total']} 件・"
              f"済 {st['done']} 件）" + "─" * 16)
        for m in rows:
            _map_row(m, fog_by_node, fog_count)


    print()
    line()
    print(wrap(result["verification_notice"], indent="  "))
    print(f"\n  今日の面を見るには … python -m src.main -s {scenario_key} --brief")
    print()


def _map_row(m, fog_by_node, fog_count):
    if m["present"]:
        print(f"    {MARK.get(m['status'], '  ')}{m['title']}")
        if m["deadline_label"]:
            print(f"         期限 … {m['deadline_label']}")
        if m["status"] == "NOT_READY" and m["blocked_by"]:
            print(f"         「{'」「'.join(m['blocked_by'])}」が終わると開きます")
        if m["unlocks"]:
            print(f"         ここが済むと開きます … {'、'.join(m['unlocks'][:2])}"
                  + (f" ほか{len(m['unlocks']) - 2}件" if len(m["unlocks"]) > 2 else ""))
    elif m["node_id"] in fog_by_node:
        a = fog_by_node[m["node_id"]]
        g = fog_count.get(a["fog_keys"][0])
        n = g["count"] if g else 1
        print(f"    ▒ {m['title']}")
        print(f"         「{a['fog_labels'][0]}」が分かると、この先に {n} 件つながります")
    else:
        note = m["absent_reason"] or ""
        if len(note) > 52:
            note = note[:52] + "…"
        print(f"    －  {m['title']}")
        if note:
            print(f"         {note}")


def show(result, scenario_key, scenario):
    head(f"【しおり】 {scenario['title']}")
    print(f"（状況: {scenario_key}）")

    # 1. 赤い帯 — 通り過ぎる前に知っておくこと
    if result["irreversible_warnings"]:
        print()
        line()
        print("■ 触る前に、知っておいてください")
        line()
        for w in result["irreversible_warnings"][:1]:
            print(f"\n  ● {w['title']}")
            print(f"\n  【事実】")
            print(wrap(w["warning_fact"], indent="   "))
            print(f"\n  【ただし】")
            print(wrap(w["warning_exception"], indent="   "))
            print(f"\n  失われるもの: {w['lost_option']}")
        rest = len(result["irreversible_warnings"]) - 1
        if rest > 0:
            print(f"\n  （ほかに {rest} 件あります。順に出します）")

    # 2. 期限が近いもの — 前提が終わっていなくても出す
    if result["deadline_alerts"]:
        print()
        line()
        print("■ 期限が近づいています")
        line()
        for a in result["deadline_alerts"][:3]:
            d = a["days_left"]
            when = f"あと {d} 日" if d is not None and d >= 0 else f"{abs(d)} 日過ぎています"
            print(f"\n  ● {a['title']}（{when}）")
            if a["blocked_by"]:
                print(f"    いま止まっている理由: {'、'.join(a['blocked_by'])} が終わっていません")
                print(f"    それでも、期限は待ってくれません。")

    # 3. 今日やること
    print()
    line()
    n = len(result["today_actions"])
    if result["actor_scope"] == "SURVIVOR":
        print(f"■ 今日、あなたが決めること（{n}件）")
    else:
        print(f"■ 今日できること（{n}件）")
    line()
    if not result["today_actions"]:
        print("\n  いま手をつけられることはありません。")
    for i, node in enumerate(result["today_actions"], 1):
        print(f"\n  {i}. {node['title']}")
        print(wrap(node["today_action"], indent="     "))
        if node.get("why_only_you"):
            print(f"\n     ＜あなたにしかできない理由＞")
            print(wrap(node["why_only_you"], indent="     "))
        if node.get("caution"):
            print()
            print(wrap("※ " + node["caution"]["note"], indent="     "))
        sb = node.get("specialist_bridge")
        if sb:
            print(f"\n     ＜相談するなら＞ {sb['type']}")
            print(wrap(sb["concrete_query"], indent="     "))
            if sb.get("estimated_cost_range"):
                print(f"     費用の目安: {sb['estimated_cost_range']}")

    # 3.5 消えた経路
    if result["shortcuts"]:
        print()
        line()
        print("■ この道は、通らなくて済みます")
        line()
        for sc in result["shortcuts"]:
            print()
            print(wrap("○ " + sc["note"], indent="  "))

    # 4. ほかの人がやってくれること
    if result["handled_by_others"]:
        print()
        line()
        print("■ あなたが決めなくてよいこと（葬儀社などが進めます）")
        line()
        for h in result["handled_by_others"]:
            print(f"  ・{h['title']}")

    # 5. 待っていること
    if result["waiting"]:
        print()
        line()
        print("■ いま待っていること（あなたの落ち度ではありません）")
        line()
        for w in result["waiting"]:
            print(f"\n  ● {w['title']}")
            print(wrap(w["reason"], indent="    "))

    # 6. 出来事のあと、生き残った範囲
    if result["reset_by_events"]:
        print()
        line()
        print("■ やり直しになるのは、ここだけです")
        line()
        for nid in result["reset_by_events"]:
            print(f"  × {nid}")
        print("\n  そして、これは無駄になっていません：")
        for s in result["surviving_after_event"]:
            print(f"  ○ {s['title']}")

    # 7. 本人の画面だけ — 一つやると何が消えたか
    if result["mitigation_preview"]:
        print()
        line()
        print("■ あなたが済ませたことで、家族の作業がこう変わりました")
        line()
        for m in result["mitigation_preview"]:
            mark = {"SKIPPED": "消えました", "SHORTENED": "短くなりました",
                    "RISK_REDUCED": "危険が減りました"}[m["effect"]]
            print(f"\n  ● {mark}")
            print(wrap(m["explanation"], indent="    "))

    # 8. 締め
    print()
    line()
    later = result["deferred_count"] + result["not_ready_count"]
    print(f"  今日はやらなくてよいこと … {later} 件")
    print(f"    （うち、まだ着手できないもの {result['not_ready_count']} 件）")
    line()
    print(wrap(result["verification_notice"], indent="  "))
    # 地図は、今日の3件を終えた褒美ではない。最初から開ける（S03 ルール14）。
    p = result["progress"]
    print(f"\n  全体は {p['total']} 件です。地図を見るには … "
          f"python -m src.main -s {scenario_key} --map")
    print()


def show_questions():
    """
    1日目に聞くこと（S03 ルール4）。

    遺族の1日目に質問を増やすのは、それだけで負担である。だから増やしてよい
    問いは、答えが**地図ぜんぶの日数を変えるもの**に限る。いまはこの二つだけ。
    答えないまま進んでもよい。そのときは、画面に「およそ」が付く。
    """
    schema = json.loads((ROOT / "data" / "schema" / "conditions.json")
                        .read_text(encoding="utf-8"))
    order = schema.get("ask_order", {}).get("DAY_ONE", [])
    conds = schema["conditions"]

    head("1日目に聞くこと")
    print()
    for i, key in enumerate(order, 1):
        c = conds[key]
        print(f"  {i}　{c['question']}")
        if c.get("question_note"):
            for part in c["question_note"].split("／"):
                if part.strip():
                    print(wrap(part.strip(), indent="       ", width=W - 11))
        print()
    print(wrap("答えなくても先へ進めます。そのときは、亡くなった日から数えた"
               "おおよその日数を出します。画面には「およそ」と付きます。",
               indent="  "))
    print()
    line()
    print()


def main(argv=None):
    p = argparse.ArgumentParser(description="しおり — ターミナルで動かす")
    p.add_argument("-s", "--scenario", help="状況の名前")
    p.add_argument("-l", "--list", action="store_true", help="用意した状況の一覧")
    p.add_argument("--today", help="今日の日付（YYYY-MM-DD）。省略すると本日")
    p.add_argument("--all", action="store_true", help="すべての状況を続けて出す")
    p.add_argument("--map", action="store_true", help="地図の面を出す（全体を見る）")
    p.add_argument("--brief", action="store_true", help="今日の面を出す（今日の3件）")
    p.add_argument("--questions", action="store_true", help="1日目に聞くことを出す")
    args = p.parse_args(argv)

    if args.questions:
        show_questions()
        return 0

    if args.list or (not args.scenario and not args.all):
        print("\n用意してある状況：\n")
        for k, v in SCENARIOS.items():
            print(f"  {k:<20} {v['title']}")
        print(f"\n  例: python -m src.main -s surv-day1\n")
        return 0

    today = date.fromisoformat(args.today) if args.today else date.today()
    engine = ShioriEngine(data_dir=ROOT / "data" / "nodes")

    keys = list(SCENARIOS) if args.all else [args.scenario]
    for key in keys:
        if key not in SCENARIOS:
            print(f"そんな状況はありません: {key}", file=sys.stderr)
            print(f"一覧は python -m src.main --list", file=sys.stderr)
            return 1
        sc = SCENARIOS[key]
        result = engine.evaluate(
            actor_scope=sc["scope"],
            condition=UserCondition(**sc["cond"]),
            completed_node_ids=sc["done"],
            statuses=sc.get("statuses"),
            today=today,
        )
        # 既定の面は、立場で変える（S03 ルール14）。
        # 遺族はパニックのなかにいるので今日の面から。本人は元気なうちなので地図から。
        if args.map:
            as_map = True
        elif args.brief:
            as_map = False
        else:
            as_map = sc["scope"] != "SURVIVOR"
        (show_map if as_map else show)(result, key, sc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
