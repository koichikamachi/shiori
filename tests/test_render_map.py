"""
地図の絵が、ルール14とルール15を破らないことを見る。

絵は「きれいかどうか」ではなく、「嘘をつかないかどうか」を試験する。
きれいさは目で見る。嘘は機械で落とす。
"""
import re
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.engine.engine import EVENT_RESETS          # noqa: E402
from src.main import SCENARIOS                      # noqa: E402
from tools.render_map_svg import EVENT_LABEL, render, wrap2  # noqa: E402

TODAY = date(2026, 9, 5)
KEYS = list(SCENARIOS)


def body(key):
    return render(key, TODAY)[0]


def svg_part(html):
    m = re.search(r"<svg.*?</svg>", html, re.S)
    return m.group(0) if m else ""


@pytest.mark.parametrize("key", KEYS)
def test_no_raw_event_identifiers(key):
    """出来事の識別子を画面に出さない。UNKNOWN_DEBT_FOUND は人の言葉ではない。"""
    h = body(key)
    for ev in EVENT_RESETS:
        assert ev not in h, f"{key}: 生の識別子 {ev} が画面に出ている"


def test_every_event_has_japanese_words():
    """戻りの矢印を描く出来事には、必ず日本語の言い方があること。"""
    for ev in EVENT_RESETS:
        assert ev in EVENT_LABEL and EVENT_LABEL[ev].strip()


@pytest.mark.parametrize("key", KEYS)
def test_absent_and_fog_are_not_drawn_as_stations(key):
    """
    ルール14。通らない道と霧は、駅にしない。
    同じ帯に円で並べると、まだ答えていないだけのものが
    「通らなくて済みます」の顔をする。
    """
    from src.engine.engine import ShioriEngine
    from src.engine.state import UserCondition
    sc = SCENARIOS[key]
    eng = ShioriEngine(data_dir=ROOT / "data" / "nodes")
    r = eng.evaluate(actor_scope=sc["scope"], condition=UserCondition(**sc["cond"]),
                     completed_node_ids=sc["done"], statuses=sc.get("statuses"),
                     today=TODAY)
    svg = svg_part(body(key))
    for m in r["map"]:
        if not m["present"]:
            assert f'data-id="{m["node_id"]}"' not in svg, \
                f'{key}: {m["node_id"]} は通らない道なのに駅として描かれている'


@pytest.mark.parametrize("key", KEYS)
def test_no_deadline_shown_on_paths_you_do_not_walk(key):
    """
    通らない道に「期限あり」と書かない。
    自分に関係のない時計が動いているように見えるからである。
    絵から外してあるので、期限の札は present のぶんしか出ない。
    """
    from src.engine.engine import ShioriEngine
    from src.engine.state import UserCondition
    sc = SCENARIOS[key]
    eng = ShioriEngine(data_dir=ROOT / "data" / "nodes")
    r = eng.evaluate(actor_scope=sc["scope"], condition=UserCondition(**sc["cond"]),
                     completed_node_ids=sc["done"], statuses=sc.get("statuses"),
                     today=TODAY)
    # 済んだ駅にも札を出さない。いつ出したかを持っていないのだから、
    # 「3日超過」と言う根拠がない。済んだ人を責める札になる。
    expect = sum(1 for m in r["map"]
                 if m["present"] and m["deadline_label"] and m["status"] != "DONE")
    assert len(re.findall(r'class="dl', svg_part(body(key)))) == expect


@pytest.mark.parametrize("key", KEYS)
def test_done_stations_carry_no_clock(key):
    """
    済んだ駅に、残り日数を出さない。
    死亡届を出し終えた人の画面に「3日超過」と赤く出ていた。
    アプリは、その人がいつ出したかを持っていない。持っていないことは言わない。
    """
    from src.engine.engine import ShioriEngine
    from src.engine.state import UserCondition
    sc = SCENARIOS[key]
    eng = ShioriEngine(data_dir=ROOT / "data" / "nodes")
    r = eng.evaluate(actor_scope=sc["scope"], condition=UserCondition(**sc["cond"]),
                     completed_node_ids=sc["done"], statuses=sc.get("statuses"),
                     today=TODAY)
    for m in r["map"]:
        if m["present"] and m["status"] == "DONE":
            assert m["days_left"] is None, f'{key}: {m["node_id"]} 済んだのに残り日数がある'


@pytest.mark.parametrize("key", KEYS)
def test_a_shut_gate_is_never_todays_business(key):
    """
    「期間が過ぎるとこの申立てはできません」と自分で書いておきながら、
    過ぎたその日に、それを今日の一番目に置いてはいけない。
    """
    from src.engine.engine import ShioriEngine
    from src.engine.state import UserCondition
    sc = SCENARIOS[key]
    eng = ShioriEngine(data_dir=ROOT / "data" / "nodes")
    r = eng.evaluate(actor_scope=sc["scope"], condition=UserCondition(**sc["cond"]),
                     completed_node_ids=sc["done"], statuses=sc.get("statuses"),
                     today=TODAY)
    shut = {m["node_id"] for m in r["map"] if m.get("gate_closed")}
    assert not (shut & {n["id"] for n in r["today_actions"]})
    assert not (shut & {a["node_id"] for a in r["deadline_alerts"]})


def test_a_shut_gate_does_not_declare_the_case_over():
    """
    閉じた門でも、その人がもう無理だとは断定しない（ルール15）。
    制度の事実（過ぎる前でなければできない）は断定してよい。
    """
    h = body("surv-new-will")
    assert "期間が過ぎている可能性" in h
    # 閉じた門の先にも道を示す一文。言い回しは
    # 「〜してください」から「〜すべき事柄です」に改めた（紋切り型をやめたため）。
    assert "あきらめる前に弁護士に相談すべき事柄です" in h
    assert "できません。あなたはもう" not in h


def test_every_shutting_gate_carries_its_words_and_a_bridge():
    """gate=CLOSES のノードには、閉じたときの言葉と、相談先が要る。"""
    import json
    for f in (ROOT / "data" / "nodes").glob("*.json"):
        for n in json.loads(f.read_text(encoding="utf-8"))["nodes"]:
            dl = n.get("deadline") or {}
            if dl.get("gate") == "CLOSES":
                assert dl.get("closed_note"), f'{n["id"]}: closed_note がない'
                assert n.get("specialist_bridge"), f'{n["id"]}: 相談先がない'


def test_fog_section_says_it_is_not_a_road_you_skip():
    """霧の見出しは、「通らなくて済む」と読まれてはならない（ルール15）。"""
    h = body("surv-day10-nomemo")
    assert "まだ分からない" in h
    assert "「通らなくて済む」という意味ではありません" in h


def test_fog_and_absent_are_in_separate_boxes():
    """三区分を混ぜない。霧と通らない道は、別の見出しに置く。"""
    h = body("self-surplus")
    assert 'class="side fog"' in h and 'class="side gone"' in h


@pytest.mark.parametrize("key", KEYS)
def test_layout_is_deterministic(key):
    """地図は毎回おなじ形をしていること。二度描いて一致する。"""
    assert body(key) == body(key)


def test_titles_are_wrapped_not_chopped():
    """駅名は二行まで。一行で切って捨てない。"""
    assert wrap2("短い名前") == ["短い名前"]
    assert len(wrap2("あ" * 20)) == 2
    assert "".join(wrap2("あ" * 20)) == "あ" * 20
    assert wrap2("あ" * 40)[1].endswith("…")


@pytest.mark.parametrize("key", KEYS)
def test_the_map_carries_no_verb(key):
    """
    ルール14。地図の面は動詞を持たない。
    駅の絵には today_action を書かない（押して初めて出る）。
    ここでは「今日決めること」の番号が三つを超えないことだけを見る。
    """
    svg = svg_part(body(key))
    assert len(re.findall(r'class="num"', svg)) <= 3


def test_every_verification_status_has_japanese_words():
    """
    ルール15。確かめの度合いは、人の言葉で出す。
    SELF_REVIEWED という札を見せられても、読む人には意味がない。
    データに出てくる値すべてに、日本語の言い方があること。
    """
    import json
    from tools.render_map_svg import JS
    seen = set()
    for f in (ROOT / "data" / "nodes").glob("*.json"):
        for n in json.loads(f.read_text(encoding="utf-8"))["nodes"]:
            v = (n.get("legal_metadata") or {}).get("verification_status")
            if v:
                seen.add(v)
    for v in seen:
        assert f"{v}:'" in JS, f"{v} に日本語の言い方がない"


@pytest.mark.parametrize("key", KEYS)
def test_the_page_never_claims_expert_verification_it_does_not_have(key):
    """人以外が「専門家の確認を受けました」を名乗る道を、絵にも作らない。"""
    h = body(key)
    assert "専門家の確認を受けました" not in h


# ---- 熟慮期間まわり。先生の点検を受けて固定したこと -----------------------

def test_an_assumed_start_date_is_never_stated_as_fact():
    """
    ルール4・15。起算日を聞いていないのに「あと82日」と赤で断定しない。

    熟慮期間は「自己のために相続の開始があったことを知った時」から数える。
    その日を聞いていないとき、エンジンは死亡日で代用する。代用してよいが、
    代用したことを黙ってはいけない。相続人ごとに満了日が違うからである。
    """
    from src.engine.engine import ShioriEngine
    from src.engine.state import UserCondition
    eng = ShioriEngine(data_dir=ROOT / "data" / "nodes")

    # 聞いていない場合 —— 「およそ」が付く
    r = eng.evaluate(actor_scope="SURVIVOR",
                     condition=UserCondition(days_since_death=10), today=TODAY)
    m = next(x for x in r["map"] if x["node_id"] == "SURV_DECIDE_ACCEPT_OR_RENOUNCE")
    assert m["clock_assumed"] is True

    # 聞いた場合 —— 付かない
    r2 = eng.evaluate(actor_scope="SURVIVOR",
                      condition=UserCondition(days_since_death=100, days_since_known=10),
                      today=TODAY)
    m2 = next(x for x in r2["map"] if x["node_id"] == "SURV_DECIDE_ACCEPT_OR_RENOUNCE")
    assert m2["clock_assumed"] is False
    assert m2["days_left"] == m["days_left"], "知った日が同じなら、残り日数も同じはず"


def test_the_word_roughly_appears_on_an_assumed_clock():
    """代用した日数の札には「およそ」が付き、そのわけがパネルに出る。"""
    from tools.render_map_svg import JS
    assert "およそ あと" in body("surv-day10-nomemo")
    assert "この3か月は、ほんとうは「自分が相続人になったと知った日」から始まります" in JS
    assert "相続人ごとに、別々に進みます" in JS


def test_the_line_between_safe_and_unsafe_acts_is_written_on_both_sides():
    """
    民法921条1号。期限の内側でも、遺産を処分すれば道は閉じる。
    片側（安全なこと）だけ書いても、線にはならない。
    """
    import json
    d = json.loads((ROOT / "data/nodes/nodes_survivor.json").read_text(encoding="utf-8"))
    n = next(x for x in d["nodes"] if x["id"] == "SURV_KNOW_SAFE_ACTIONS")
    note = n["caution"]["note"]
    for s in ("放棄の道を閉じうる行為", "ふつうは閉じない行為",
              "遺産分割協議を成立させて財産を取得する", "残高証明書"):
        assert s in note, s


def test_renunciation_is_not_a_cause_for_representation():
    """放棄は代襲の原因ではない。伝える相手を間違えると、誰も救われない。"""
    import json
    d = json.loads((ROOT / "data/nodes/nodes_survivor.json").read_text(encoding="utf-8"))
    n = next(x for x in d["nodes"] if x["id"] == "SURV_RENOUNCE_TELL_NEXT")
    assert "放棄は代襲の原因ではない" in n["caution"]["note"]


def test_extension_is_not_granted_just_by_asking():
    """伸長は当然には認められない。延びるのも、申し立てた人の分だけである。"""
    import json
    d = json.loads((ROOT / "data/nodes/nodes_survivor.json").read_text(encoding="utf-8"))
    n = next(x for x in d["nodes"] if x["id"] == "SURV_EXTEND_DELIBERATION")
    note = n["caution"]["note"]
    assert "当然に認められるわけではない" in note
    assert "他の全員の期間が当然に延びるわけではない" in note
    assert "数週間前" in n["today_action"]


def test_court_acceptance_is_not_described_as_the_end_of_the_matter():
    """受理は公証であって、あらゆる債権者との関係で確定するわけではない。"""
    import json
    d = json.loads((ROOT / "data/nodes/nodes_survivor.json").read_text(encoding="utf-8"))
    n = next(x for x in d["nodes"] if x["id"] == "SURV_DECIDE_ACCEPT_OR_RENOUNCE")
    assert "受理されたから安心" in n["caution"]["note"]


def test_three_months_are_counted_by_the_calendar_not_by_days():
    """
    初日不算入、応当日の前日に満了（民法140条・143条2項）。
    9月10日に知ったなら、満了は12月10日。日数に換算しない。
    """
    from datetime import date
    from src.engine.engine import add_months
    assert add_months(date(2026, 9, 10), 3) == date(2026, 12, 10)
    assert add_months(date(2026, 11, 30), 3) == date(2027, 2, 28)   # 応当日のない月は末日


def test_the_start_date_is_asked_on_day_one():
    """
    ルール4。「知った時」から数える期限があるなら、その日を1日目に聞く。

    先生の判断：1日目。放棄の判断が近づいてから聞くのでは遅い。
    そのころには、いつ知ったかを示すもの（封筒、メール）が捨てられている。
    """
    import json
    schema = json.loads((ROOT / "data/schema/conditions.json").read_text(encoding="utf-8"))
    day_one = schema["ask_order"]["DAY_ONE"]
    assert "days_since_known" in day_one

    q = schema["conditions"]["days_since_known"]
    assert q["question"].endswith("？") or q["question"].endswith("。")
    # 何を聞いているのかが、聞かれた人に分かること
    assert "相続人になった" in q["question"]
    note = q["question_note"]
    assert "同じ日なら、同じ日数で結構です" in note      # ふつうの人には、ただの確認
    assert "放棄したために" in note                      # 繰り上がった人には、別の日
    assert "人によって、この日は違います" in note
    assert "一つ残しておいてください" in note            # 証拠は1日目にしか残せない


def test_day_one_questions_are_few():
    """
    遺族の1日目に質問を増やすのは、それ自体が負担である。
    増やしてよいのは、答えが地図ぜんぶの日数を変える問いだけ。
    """
    import json
    schema = json.loads((ROOT / "data/schema/conditions.json").read_text(encoding="utf-8"))
    assert len(schema["ask_order"]["DAY_ONE"]) <= 3


def test_not_answering_is_allowed_and_says_so():
    """答えないまま進める。そのとき何が起きるかを、その場で言う。"""
    from src.main import show_questions
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        show_questions()
    out = buf.getvalue()
    assert "答えなくても先へ進めます" in out
    assert "およそ" in out


def test_no_markdown_marks_leak_into_what_people_read():
    """
    画面に出る言葉に ** を混ぜない。Markdown の強調は、端末ではただの文字になる。
    """
    import json
    for f in list((ROOT / "data" / "nodes").glob("*.json")) + \
             [ROOT / "data/schema/conditions.json"]:
        assert "**" not in f.read_text(encoding="utf-8"), f.name


# ---- 期限は別格である（S03 ルール4） ---------------------------------------

def test_every_deadline_has_been_checked_against_a_primary_source():
    """
    期限がなければ、このアプリは意味を持たない。
    期限が間違っていれば、実害が出る。
    75件のうち期限を持つのは10件。いちばん大事で、いちばん小さい。
    ここだけは、一次資料に当たっていないものを通さない。
    """
    import json
    for f in (ROOT / "data" / "nodes").glob("*.json"):
        for n in json.loads(f.read_text(encoding="utf-8"))["nodes"]:
            if not n.get("deadline"):
                continue
            lm = n.get("legal_metadata") or {}
            assert lm.get("verification_status") in ("SOURCE_VERIFIED", "EXPERT_VERIFIED"), \
                f'{n["id"]}: 期限を持つのに一次資料で確かめていない'
            assert lm.get("source_url"), f'{n["id"]}: 期限を持つのに出典のURLがない'
            assert lm.get("verified_at"), f'{n["id"]}: 期限を持つのに確認日がない'


def test_deadline_sources_are_official():
    """期限の出どころは、公的機関に限る（エージェントの関門と同じ物差し）。"""
    import json
    from src.agent.guards import is_official
    for f in (ROOT / "data" / "nodes").glob("*.json"):
        for n in json.loads(f.read_text(encoding="utf-8"))["nodes"]:
            if not n.get("deadline"):
                continue
            url = (n["legal_metadata"] or {})["source_url"]
            assert is_official(url), f'{n["id"]}: 期限の出典が公的機関でない: {url}'


def test_the_screen_shows_where_the_deadline_came_from():
    """
    期限を出すなら、根拠も一緒に出す。
    「あと82日」とだけ言われても、読む人は確かめようがない。
    """
    from src.engine.engine import ShioriEngine
    from src.engine.state import UserCondition
    eng = ShioriEngine(data_dir=ROOT / "data" / "nodes")
    r = eng.evaluate(actor_scope="SURVIVOR",
                     condition=UserCondition(days_since_death=10), today=TODAY)
    for m in r["map"]:
        if m["deadline_label"]:
            assert m["deadline_source"], f'{m["node_id"]}: 根拠が画面に渡っていない'
            assert m["deadline_checked_at"]
    h = body("surv-day10-nomemo")
    assert "確かめた先" in h or "確かめた先" in __import__(
        "tools.render_map_svg", fromlist=["JS"]).JS


def test_the_deadline_sheet_is_one_page_of_ten():
    """
    外部の専門家に渡す一枚。75件を渡せば読まれない。10行なら読まれる。
    """
    from tools.deadline_table import build, collect
    rows = collect()
    assert len(rows) <= 12, "期限が増えすぎている。増やすなら、一次資料もいっしょに"
    md = build(date(2026, 9, 4))
    # 内部の言葉を持ち込まない
    for word in ("node_id", "SOURCE_VERIFIED", "exists_if", "SURV_", "SELF_"):
        assert word not in md, f"外部に出す紙に内部の言葉が入っている: {word}"
    # 責任の切り方を必ず書く
    assert "個別の事案についての助言をお願いするものではありません" in md
    assert "お名前を出すかどうかはお選びいただけます" in md
    # 三択の返事欄
    assert "これは書くべきでない" in md


def test_the_deadline_sheet_is_ordered_by_urgency_not_by_file():
    """短い期限から並べる。読む人は、急ぐものから見たい。"""
    from tools.deadline_table import collect
    rows = collect()
    assert rows[0][1]["id"] == "SURV_DEATH_NOTICE"      # 7日がいちばん上
