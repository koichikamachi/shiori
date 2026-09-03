"""
相続人は「確定」しない（放棄と、順位の繰り上がり）

もとは実務の一件である。前の配偶者のもとで育った子が土壇場で相続を放棄し、
順位が繰り上がって故人の兄弟姉妹が相続人になった。人数が多く、面識も薄く、
遺産分割の合意を取りつけるのに極めて難渋した。

この形をエンジンが表現できているかを、ここで固定する。
"""
from datetime import date
from pathlib import Path

import pytest

from src.engine.engine import EVENT_RESETS, ShioriEngine
from src.engine.state import NodeStatus, UserCondition

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 9, 3)

# 話し合いまで済ませたところ
FAR_ALONG = {
    "SURV_DEATH_NOTICE", "SURV_FUNERAL_ARRANGEMENT", "SURV_KNOW_SAFE_ACTIONS",
    "SURV_FIND_ENTRY", "SURV_WARN_DEPOSIT", "SURV_COLLECT_KOSEKI",
    "SURV_DETERMINE_HEIRS", "SURV_DISCOVER_ACCOUNTS", "SURV_DISCOVER_DEBT",
    "SURV_LIST_ASSETS", "SURV_DECIDE_ACCEPT_OR_RENOUNCE", "SURV_ESTATE_DIVISION",
}


@pytest.fixture(scope="module")
def engine():
    return ShioriEngine(data_dir=ROOT / "data" / "nodes")


def run(engine, scope="SURVIVOR", done=None, **cond):
    return engine.evaluate(actor_scope=scope, condition=UserCondition(**cond),
                           completed_node_ids=done or set(), today=TODAY)


# ---- 放棄が起きると、相続人の確定が崩れる ----

def test_renunciation_undoes_the_heir_determination(engine):
    """
    放棄した人は初めから相続人でなかったことになる。だから相続人の顔ぶれが変わる。
    ここが崩れないと、しおりは「相続人はもう確定しています」と言い続ける。
    """
    r = run(engine, done=FAR_ALONG, days_since_death=100, events=["HEIR_RENOUNCED"])
    assert "SURV_DETERMINE_HEIRS" in r["reset_by_events"]
    assert "SURV_ESTATE_DIVISION" in r["reset_by_events"]


def test_reset_list_and_durability_agree(engine):
    """
    EVENT_RESETS に名前があっても durability が VOLATILE でなければ戻らない。
    書いてあるのに効かない、という形をここで防ぐ。
    """
    for event, ids in EVENT_RESETS.items():
        for nid in ids:
            node = engine.nodes_by_scope["SURVIVOR"].get(nid)
            assert node is not None, f"{event} が知らないノードを指している: {nid}"
            assert node["durability"] == "VOLATILE", \
                f"{nid} は {event} で戻す対象なのに VOLATILE でない"


def test_what_survived_is_shown(engine):
    """崩れたものより、生き残った範囲を見せる（S03 ルール7）。"""
    r = run(engine, done=FAR_ALONG, days_since_death=100, events=["HEIR_RENOUNCED"])
    survived = {s["node_id"] for s in r["surviving_after_event"]}
    assert "SURV_LIST_ASSETS" in survived      # 財産の一覧は無駄になっていない
    assert "SURV_COLLECT_KOSEKI" in survived   # 集めた戸籍も無駄になっていない
    assert survived.isdisjoint(set(r["reset_by_events"]))


# ---- 期限の時計が、人によって違う ----

def test_three_months_starts_when_you_learned_not_when_they_died(engine):
    """
    繰り上がって相続人になった人の3か月は、死亡日ではなく知った時から始まる。
    ここを死亡日で数えると、まだ間に合う人に「もう過ぎています」と言う。
    """
    nid = "SURV_DECIDE_ACCEPT_OR_RENOUNCE"
    late = run(engine, done={"SURV_DEATH_NOTICE"}, days_since_death=100)
    shifted = run(engine, done={"SURV_DEATH_NOTICE"}, days_since_death=100, days_since_known=10)

    assert late["days_left"][nid] < 0                    # 死亡から数えると過ぎている
    assert late["urgency"][nid] == "OVERDUE"
    assert shifted["days_left"][nid] > 60                # 知った時から数えるとまだある
    assert shifted["urgency"][nid] == "CAN_WAIT"


def test_death_clock_is_unchanged_when_not_specified(engine):
    """clock を書いていない期限は、これまでどおり死亡日から数える。"""
    nid = "SURV_DEATH_NOTICE"                            # 死亡の事実を知った日から7日
    a = run(engine, days_since_death=3)
    b = run(engine, days_since_death=3, days_since_known=0)
    assert a["days_left"][nid] == b["days_left"][nid] == 4


def test_known_clock_is_declared_in_data_not_in_code(engine):
    """どの時計で数えるかは、コードではなくノードに書く。"""
    nodes = engine.nodes_by_scope["SURVIVOR"]
    assert nodes["SURV_DECIDE_ACCEPT_OR_RENOUNCE"]["deadline"]["clock"] == "KNOWN"
    assert nodes["SURV_TAX_FILING"]["deadline"]["clock"] == "KNOWN"
    assert "clock" not in nodes["SURV_DEATH_NOTICE"]["deadline"]


# ---- 繰り上がった人に、届くこと ----

def test_rank_shift_warning_reaches_the_next_in_line(engine):
    r = run(engine, done={"SURV_DEATH_NOTICE", "SURV_KNOW_SAFE_ACTIONS"},
            days_since_death=100, days_since_known=10)
    band = [w["node_id"] for w in r["irreversible_warnings"]]
    assert "SURV_RANK_SHIFT_NOTICE" in band
    w = next(w for w in r["irreversible_warnings"] if w["node_id"] == "SURV_RANK_SHIFT_NOTICE")
    assert "知らせません" in w["warning_fact"]           # 裁判所は通知しない
    assert "知った時から" in w["warning_exception"]      # まだ間に合う


def test_rank_shift_warning_does_not_crowd_day_one(engine):
    """1日目の赤い帯は「預金に触る前に」だけ。順位の話はその次から。"""
    r = run(engine, days_since_death=1)
    assert [w["node_id"] for w in r["irreversible_warnings"]] == ["SURV_WARN_DEPOSIT"]


def test_telling_the_next_person_appears_after_the_renounce_decision(engine):
    """裁判所は次順位の人に通知しない。だから自分から伝える道が要る。"""
    nid = "SURV_RENOUNCE_TELL_NEXT"
    before = run(engine, done={"SURV_DEATH_NOTICE"}, days_since_death=30)
    after = run(engine, done=FAR_ALONG, days_since_death=100)
    assert before["statuses"][nid] == "NOT_READY"
    assert after["statuses"][nid] == "READY"


# ---- 本人の側。子の有無で、打てる手がまったく違う ----

def test_everyone_is_asked_who_their_heirs_would_be(engine):
    for cond in (dict(has_spouse=True, children_count=2),
                 dict(children_count=0),
                 dict(is_business_owner=True)):
        r = run(engine, scope="SELF", **cond)
        assert "SELF_HEIR_ESTIMATE" in r["statuses"]


def test_koseki_can_be_pulled_forward_only_when_there_are_children(engine):
    """
    自分・配偶者・直系尊属・直系卑属の戸籍は取れる。兄弟姉妹の戸籍は取れない。
    だから子がいない人には、この前倒しが効かない。
    """
    with_child = run(engine, scope="SELF", has_spouse=True, children_count=2)
    no_child = run(engine, scope="SELF", has_spouse=True, children_count=0)

    assert "SELF_KOSEKI_AHEAD" in with_child["statuses"]
    assert "SELF_KOSEKI_AHEAD" not in no_child["statuses"]

    gone = next(a for a in no_child["absent"] if a["node_id"] == "SELF_KOSEKI_AHEAD")
    assert "取れない" in gone["note"]        # できないと、はっきり言う


def test_the_childless_are_given_the_one_thing_that_works(engine):
    """
    できないと言うだけで終わらせない。兄弟姉妹には遺留分がないので、
    遺言を書けば遺産分割の話し合いそのものが要らなくなる。
    """
    r = run(engine, scope="SELF", has_spouse=True, children_count=0)
    assert "SELF_NO_CHILD_ROUTE" in r["statuses"]
    w = next(w for w in r["irreversible_warnings"] if w["node_id"] == "SELF_NO_CHILD_ROUTE")
    assert "取れません" in w["warning_fact"]
    assert "遺留分がありません" in w["warning_exception"]

    with_child = run(engine, scope="SELF", has_spouse=True, children_count=2)
    assert "SELF_NO_CHILD_ROUTE" not in with_child["statuses"]


def test_the_childless_will_removes_the_hardest_survivor_step(engine):
    """遺言を書き終えると、遺族の側で何が消えるかを見せる（S03 ルール3・表示専用）。"""
    r = run(engine, scope="SELF", done={"SELF_HEIR_ESTIMATE", "SELF_NO_CHILD_ROUTE"},
            has_spouse=True, children_count=0)
    eased = {(m["node_id"], m["effect"]) for m in r["mitigation_preview"]}
    assert ("SELF_NO_CHILD_ROUTE", "SKIPPED") in eased


def test_renounce_chain_warning_never_eats_todays_three(engine):
    """行き先が変わるという警告は、赤い帯に出す。今日の3件には数えない。"""
    r = run(engine, scope="SELF", has_spouse=True, children_count=2)
    assert "SELF_RENOUNCE_CHAIN_WARN" not in [n["id"] for n in r["today_actions"]]
    assert "SELF_RENOUNCE_CHAIN_WARN" in [w["node_id"] for w in r["irreversible_warnings"]]
