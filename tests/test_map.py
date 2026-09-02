"""
地図の面（S03 ルール14）

「三件」は頼む手数の上限であって、視野の上限ではない。
ここが崩れると、地図を見せずに「次の交差点を右」とだけ言う道具に戻る。
"""
import json
from datetime import date
from pathlib import Path

import pytest

from src import main as cli
from src.engine.engine import ShioriEngine
from src.engine.state import NodeStatus, UserCondition

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 9, 2)


@pytest.fixture(scope="module")
def engine():
    return ShioriEngine(data_dir=ROOT / "data" / "nodes")


def run(engine, scope, **cond):
    return engine.evaluate(actor_scope=scope, condition=UserCondition(**cond),
                           completed_node_ids=set(), today=TODAY)


PENSIONER = dict(has_own_home=True, has_spouse=True, children_count=2)


# ---- 全体が見えること ----

def test_map_holds_every_node_of_the_scope(engine):
    """地図には、その立場のノードが一つ残らず出る。消えたものも出る。"""
    for scope in ("SELF", "FAMILY_CHILD", "SURVIVOR"):
        r = run(engine, scope, days_since_death=1)
        assert len(r["map"]) == len(engine.nodes_by_scope[scope])
        assert {m["node_id"] for m in r["map"]} == set(engine.nodes_by_scope[scope])


def test_three_item_limit_does_not_reach_the_map(engine):
    """今日の3件は今日の面の話である。地図がそれに縛られたら地図ではない。"""
    r = run(engine, "SELF", **PENSIONER)
    assert len(r["today_actions"]) <= 3
    assert len([m for m in r["map"] if m["present"]]) > 3
    assert len(r["map"]) == 34


def test_map_carries_no_verb(engine):
    """
    地図に today_action を出さない。出すと「今日やること」が34件あるように見える。
    """
    r = run(engine, "SELF", **PENSIONER)
    for m in r["map"]:
        assert "today_action" not in m
        assert "why_only_you" not in m
        assert "specialist_bridge" not in m


def test_absent_nodes_stay_on_the_map_with_a_reason(engine):
    """消えた道は、黙って消さない。理由の言葉を必ず持たせる。"""
    r = run(engine, "SELF", **PENSIONER)
    gone = [m for m in r["map"] if not m["present"]]
    assert gone, "年金生活者なら、通らない道があるはず"
    for m in gone:
        assert m["absent_reason"], f"{m['node_id']} の消えた理由が空である"


def test_every_conditional_node_has_absent_note():
    """exists_if があるなら absent_note がある。Lint と二重に見る。"""
    missing = []
    for f in ("nodes_self.json", "nodes_family.json", "nodes_survivor.json"):
        doc = json.loads((ROOT / "data" / "nodes" / f).read_text(encoding="utf-8"))
        for n in doc["nodes"]:
            if n.get("exists_if") and not n.get("absent_note"):
                missing.append(n["id"])
    assert missing == []


# ---- 「いいえ」と「まだ分からない」を分けること ----

def test_fog_is_distinguished_from_not_applicable(engine):
    """同じ「消えている」でも、答えた結果と、まだ答えていないのは別物である。"""
    # 「いいえ」と答えた人と、まだ答えていない人。消え方は同じでも、意味が違う。
    known = run(engine, "SELF", **PENSIONER, estate_over_basic_deduction=False)
    foggy = run(engine, "SELF", **PENSIONER, estate_over_basic_deduction=None)

    tax = "SELF_TAX_MAGNITUDE"
    assert next(a for a in known["absent"] if a["node_id"] == tax)["fog"] is False
    assert next(a for a in foggy["absent"] if a["node_id"] == tax)["fog"] is True
    assert foggy["progress"]["fog"] > known["progress"]["fog"]


def test_fog_node_is_never_asserted_as_settled(engine):
    """
    「不動産がないので名義変更は要りません」と、まだ分かっていないのに言い切らない。
    霧のノードは「この道は通らなくて済みます」に入れてはいけない。
    """
    r = run(engine, "SURVIVOR", days_since_death=1,
            unanswered=["has_real_estate_in_estate", "has_insurance"])
    fogged = {a["node_id"] for a in r["absent"] if a["fog"]}
    assert "SURV_REAL_ESTATE_REGISTRATION" in fogged
    assert fogged.isdisjoint({s["node_id"] for s in r["shortcuts"]})


def test_fog_group_says_how_many_open(engine):
    """「◯◯が分かると、この先に◯件つながります」を作れること。"""
    r = run(engine, "SELF", **PENSIONER, unanswered=["estate_over_basic_deduction"])
    g = next(g for g in r["fog_groups"] if g["key"] == "estate_over_basic_deduction")
    assert g["count"] == 4
    assert g["label"] == "財産が基礎控除を超えそう"


def test_unanswered_never_changes_the_judgement(engine):
    """
    unanswered は霧を描くためだけの箱である。判定に混ぜたら、
    S03 の他の決めごとが全部揺れる。
    """
    a = run(engine, "SELF", **PENSIONER)
    b = run(engine, "SELF", **PENSIONER,
            unanswered=["estate_over_basic_deduction", "is_business_owner",
                        "has_foreign_assets"])
    assert a["statuses"] == b["statuses"]
    assert [n["id"] for n in a["today_actions"]] == [n["id"] for n in b["today_actions"]]
    assert a["progress"]["present"] == b["progress"]["present"]


# ---- 地図として成り立っていること ----

def test_unlocks_is_the_reverse_of_prerequisites(engine):
    r = run(engine, "SURVIVOR", days_since_death=1)
    by_id = {m["node_id"]: m for m in r["map"]}
    koseki = by_id["SURV_COLLECT_KOSEKI"]
    heirs = by_id["SURV_DETERMINE_HEIRS"]
    assert heirs["title"] in koseki["unlocks"]
    assert koseki["title"] in heirs["blocked_by"]


def test_blocked_by_uses_titles_not_ids(engine):
    """idは利用者の言葉ではない。画面に出るのは名前である。"""
    r = run(engine, "SURVIVOR", days_since_death=1)
    for m in r["map"]:
        for b in m["blocked_by"]:
            assert not b.startswith(("SURV_", "SELF_", "FAM_"))


def test_map_order_is_stable(engine):
    """地図は毎回おなじ形をしていること。並びが揺れるものは地図ではない。"""
    a = run(engine, "SELF", **PENSIONER)
    b = run(engine, "SELF", **PENSIONER)
    assert [m["node_id"] for m in a["map"]] == [m["node_id"] for m in b["map"]]
    streams = [m["stream"] for m in a["map"]]
    assert streams == sorted(streams), "流れごとに束ねること"


def test_progress_adds_up(engine):
    for scope in ("SELF", "SURVIVOR"):
        r = run(engine, scope, days_since_death=1, **({} if scope == "SURVIVOR" else PENSIONER))
        p = r["progress"]
        assert p["present"] + p["absent"] == p["total"] == len(r["map"])
        assert p["done"] + p["ready"] + p["in_progress"] + p["waiting"] + p["not_ready"] \
            == p["present"]


def test_waiting_node_appears_on_the_map(engine):
    r = engine.evaluate(
        actor_scope="SURVIVOR",
        condition=UserCondition(days_since_death=80),
        completed_node_ids={"SURV_DEATH_NOTICE"},
        statuses={"SURV_COLLECT_KOSEKI": NodeStatus.WAITING},
        today=TODAY)
    m = next(m for m in r["map"] if m["node_id"] == "SURV_COLLECT_KOSEKI")
    assert m["status"] == "WAITING"


# ---- 既定の面（立場で変える） ----

def test_default_face_is_map_for_self_and_brief_for_survivor(capsys):
    cli.main(["-s", "self-pensioner", "--today", "2026-09-02"])
    assert "【しおり／地図】" in capsys.readouterr().out

    cli.main(["-s", "surv-day1", "--today", "2026-09-02"])
    out = capsys.readouterr().out
    assert "【しおり／地図】" not in out
    # 地図があることは、今日の面からも必ず伝わること
    assert "--map" in out


def test_map_can_be_opened_from_the_first_second(capsys):
    """地図は、今日の3件を終えた褒美ではない。何も済んでいなくても開く。"""
    cli.main(["-s", "surv-day1", "--map", "--today", "2026-09-02"])
    out = capsys.readouterr().out
    assert "全体は、こうなっています" in out
    assert "ぜんぶで 25 件" in out
