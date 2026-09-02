"""
しおり 判定エンジンの単体テスト (pytest)

前半は立場ごとの分岐（年金生活者・地主・会社オーナー・海外・負債・家族）。
後半は S03 の決めごとを固定する構造テスト。

    python -m pytest tests/ -v

構造テストの ②⑤⑧ が落ちたら、それは実装の細部ではなく設計を踏み外している。
直すのはテストではなくコードである。
"""
from datetime import date, timedelta
from pathlib import Path

import pytest

from src.engine.engine import ShioriEngine, add_months
from src.engine.expr import ExprError, evaluate as eval_expr
from src.engine.state import NodeStatus, UserCondition

TODAY = date(2026, 9, 5)          # 当日を固定して、結果が日によって変わらないようにする
DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "nodes"


@pytest.fixture(scope="module")
def engine():
    return ShioriEngine(data_dir=DATA_DIR)


def ids(nodes):
    return [n["id"] for n in nodes]


def run(engine, scope, cond, completed=None, statuses=None):
    return engine.evaluate(
        actor_scope=scope, condition=cond,
        completed_node_ids=completed, statuses=statuses, today=TODAY,
    )


# =============================================================================
# 1. 本人編（SELF）— 立場ごとの分岐
# =============================================================================

def test_self_pensioner_default_flow(engine):
    """年金生活者: 基礎控除内・不動産なし。税と地主のノードは出ない。"""
    cond = UserCondition(has_own_home=False, estate_over_basic_deduction=False)
    res = run(engine, "SELF", cond)
    active = {n["id"] for n in res["all_active_nodes"]}
    assert "SELF_BANK_LIST" in active
    assert "SELF_INSURANCE_NOTE" in active
    assert "SELF_DIGITAL_PASS_NOTE" in active
    assert "SELF_ENTRY_AND_KEY" in active
    assert "SELF_TAX_MAGNITUDE" not in active
    assert "SELF_LAND_SMALL_RESIDENTIAL" not in active
    assert "SELF_BIZ_STOCK_REGISTER" not in active


def test_self_pensioner_with_home(engine):
    """持ち家のある年金生活者にも、家の名義と不動産の封筒が届く。

    persona_labels ではなく exists_if で判定していることの確認。
    LANDOWNER タグを持たない人にも届かなければ意味がない。
    """
    cond = UserCondition(has_own_home=True, estate_over_basic_deduction=False)
    res = run(engine, "SELF", cond)
    active = {n["id"] for n in res["all_active_nodes"]}
    assert "SELF_HOME_TITLE_CHECK" in active
    assert "SELF_LAND_ENVELOPE" in active
    assert "SELF_LAND_ANCESTOR_TITLE" not in active   # 自宅以外の不動産はない


def test_self_landowner_and_tax_flow(engine):
    """地主: 持ち家・他不動産・貸地あり、基礎控除超え。"""
    cond = UserCondition(
        has_own_home=True, has_other_real_estate=True,
        has_leased_land=True, estate_over_basic_deduction=True,
    )
    res = run(engine, "SELF", cond)
    active = {n["id"] for n in res["all_active_nodes"]}
    for nid in ("SELF_LAND_ENVELOPE", "SELF_LAND_ANCESTOR_TITLE", "SELF_LAND_BOUNDARY",
                "SELF_LAND_LEASE_NOTE", "SELF_LAND_SMALL_RESIDENTIAL", "SELF_CASH_FOR_TAX"):
        assert nid in active, nid
    warn = {w["node_id"] for w in res["irreversible_warnings"]}
    assert "SELF_LAND_SMALL_RESIDENTIAL" in warn


def test_self_business_owner_flow(engine):
    """会社オーナー: 株主名簿・個人保証・事業承継税制・倒れた翌日の備え。"""
    cond = UserCondition(is_business_owner=True)
    res = run(engine, "SELF", cond)
    active = {n["id"] for n in res["all_active_nodes"]}
    for nid in ("SELF_BIZ_STOCK_REGISTER", "SELF_BIZ_GUARANTEE_NOTE",
                "SELF_BIZ_SUCCESSION_TAX", "SELF_BIZ_CONTINUITY_KIT", "SELF_BIZ_LOAN_BETWEEN"):
        assert nid in active, nid
    warn = {w["node_id"] for w in res["irreversible_warnings"]}
    assert "SELF_BIZ_SUCCESSION_TAX" in warn
    # 法人版と個人版は別の制度。法人だけのときに個人版は出ない。
    assert "SELF_BIZ_INDIVIDUAL_SUCCESSION_TAX" not in active


def test_self_sole_proprietor_flow(engine):
    """個人事業主: 個人版だけが出て、法人版は出ない。"""
    cond = UserCondition(is_sole_proprietor=True)
    res = run(engine, "SELF", cond)
    active = {n["id"] for n in res["all_active_nodes"]}
    assert "SELF_BIZ_INDIVIDUAL_SUCCESSION_TAX" in active
    assert "SELF_BIZ_SUCCESSION_TAX" not in active


def test_self_foreign_assets_flow(engine):
    """海外資産: 国名メモ・現地手続・国外財産の届出。"""
    cond = UserCondition(has_foreign_assets=True)
    res = run(engine, "SELF", cond)
    active = {n["id"] for n in res["all_active_nodes"]}
    for nid in ("SELF_FOREIGN_COUNTRY_NOTE", "SELF_FOREIGN_PROBATE_CHECK",
                "SELF_FOREIGN_REPORT_CHECK"):
        assert nid in active, nid
    warn = {w["node_id"] for w in res["irreversible_warnings"]}
    assert "SELF_FOREIGN_PROBATE_CHECK" in warn


def test_self_debt_flow(engine):
    """負債あり: 一覧・触る前の言い残し・放棄されても残る道。"""
    cond = UserCondition(has_debt_or_guarantee=True)
    res = run(engine, "SELF", cond)
    active = {n["id"] for n in res["all_active_nodes"]}
    for nid in ("SELF_DEBT_LIST_NOTE", "SELF_DEBT_LEAVE_INSTRUCTION",
                "SELF_DEBT_INSURANCE_ROUTE", "SELF_BIZ_GUARANTEE_NOTE"):
        assert nid in active, nid


def test_self_donation_and_partner_flow(engine):
    """家族以外へ渡す: 遺留分・受入確認・執行者。籍を入れていない相手。"""
    cond = UserCondition(wishes_donation=True, has_unmarried_partner=True)
    res = run(engine, "SELF", cond)
    active = {n["id"] for n in res["all_active_nodes"]}
    for nid in ("SELF_RESERVED_SHARE_CHECK", "SELF_BEQUEST_ORG_CHECK",
                "SELF_DESIGNATE_EXECUTOR", "SELF_PARTNER_PROVISION"):
        assert nid in active, nid


def test_family_child_scope(engine):
    """親を気にかける家族: 親の協力を前提にしないノードがあること。"""
    res = run(engine, "FAMILY_CHILD", UserCondition())
    active = {n["id"] for n in res["all_active_nodes"]}
    assert "FAMILY_NARROW_WHAT_TO_ASK" in active
    assert "FAMILY_RESEARCH_WITHOUT_PARENT" in active
    # 本人・遺族のノードが混ざっていないこと
    assert all(nid.startswith("FAMILY_") for nid in active)


# =============================================================================
# 2. 遺族編（SURVIVOR）— 立場ごとの分岐
# =============================================================================

def test_survivor_immediate_three_items(engine):
    """① 死の直後。自分で決めるのは3件まで。

    S02 第三部2 は三つを別のものとして渡している。
      今日やること／今日はやらなくてよいこと／触る前に知っておくこと
    三つめを「今日やること」に数えると、3件の枠を一つ潰してしまう。
    """
    cond = UserCondition(days_since_death=1)
    res = run(engine, "SURVIVOR", cond)
    today = ids(res["today_actions"])
    assert len(today) <= 3
    assert "SURV_KNOW_SAFE_ACTIONS" in today
    assert "SURV_FUNERAL_ARRANGEMENT" in today
    assert "SURV_FIND_ENTRY" in today

    # 触る前の注意は、赤い帯に出る。作業としては数えない
    assert "SURV_WARN_DEPOSIT" not in today
    warn = {w["node_id"] for w in res["irreversible_warnings"]}
    assert "SURV_WARN_DEPOSIT" in warn


def test_survivor_day1_warning_band_is_not_crowded(engine):
    """死の直後、赤い帯に並ぶのは今かかわりのあるものだけ。

    まだ何か月も先の不可逆（遺産分割のやり直しなど）を直後に並べると、
    本当に危ない一つが埋もれる。
    """
    res = run(engine, "SURVIVOR", UserCondition(days_since_death=1))
    warn = [w["node_id"] for w in res["irreversible_warnings"]]
    assert warn[0] == "SURV_WARN_DEPOSIT"
    assert len(warn) == 1
    assert "SURV_ESTATE_DIVISION" not in warn


def test_self_warnings_are_not_filtered(engine):
    """本人の側では、まだ手を打てる不可逆こそ先に知らせる（S02 第一部8）。

    遺族と同じ絞り込みをすると、事前の告知という目的そのものが消える。
    """
    cond = UserCondition(has_own_home=True, estate_over_basic_deduction=True,
                         is_business_owner=True)
    res = run(engine, "SELF", cond)
    warn = {w["node_id"] for w in res["irreversible_warnings"]}
    assert "SELF_LAND_SMALL_RESIDENTIAL" in warn   # 前提が未了でも出る
    assert "SELF_BIZ_SUCCESSION_TAX" in warn


def test_shortcut_note_is_shown_when_route_disappears(engine):
    """消えた経路は、消えたと伝える。

    「その作業は要りません」と言えなければ、本人の一手が効いたことが伝わらない。
    """
    cond = UserCondition(days_since_death=10, entry_found=True, entry_locked=False)
    res = run(engine, "SURVIVOR", cond)
    notes = {s["node_id"]: s["note"] for s in res["shortcuts"]}
    assert "SURV_DISCOVER_ACCOUNTS" in notes
    assert "SURV_DISCOVER_DEBT" in notes
    assert "要りません" in notes["SURV_DISCOVER_ACCOUNTS"]

    # メモがなければ、経路は消えていないので何も言わない
    res2 = run(engine, "SURVIVOR", UserCondition(days_since_death=10, entry_found=False))
    assert "SURV_DISCOVER_ACCOUNTS" not in {s["node_id"] for s in res2["shortcuts"]}


def test_survivor_death_notice_not_counted_in_three(engine):
    """死亡届は葬儀社が代行する。3件には数えず、別の欄に出す。"""
    cond = UserCondition(days_since_death=1)
    res = run(engine, "SURVIVOR", cond)
    assert "SURV_DEATH_NOTICE" not in ids(res["today_actions"])
    assert "SURV_DEATH_NOTICE" in {n["node_id"] for n in res["handled_by_others"]}


def test_survivor_standard_route_when_no_memo(engine):
    """④ 何も残されていない場合が標準の経路である。"""
    cond = UserCondition(days_since_death=10, entry_found=False)
    res = run(engine, "SURVIVOR", cond)
    active = {n["id"] for n in res["all_active_nodes"]}
    assert "SURV_DISCOVER_ACCOUNTS" in active
    assert "SURV_DISCOVER_DEBT" in active


def test_survivor_shortcut_when_entry_found(engine):
    """③ 入口が見つかり開けられたとき、探索の経路が消える。"""
    cond = UserCondition(days_since_death=10, entry_found=True, entry_locked=False)
    res = run(engine, "SURVIVOR", cond)
    active = {n["id"] for n in res["all_active_nodes"]}
    assert "SURV_DISCOVER_ACCOUNTS" not in active
    assert "SURV_DISCOVER_DEBT" not in active


def test_survivor_entry_found_but_locked(engine):
    """入口があっても開けられなければ、中身は手元にない。鍵の預け先を探す。"""
    cond = UserCondition(days_since_death=3, entry_found=True, entry_locked=True)
    res = run(engine, "SURVIVOR", cond)
    active = {n["id"] for n in res["all_active_nodes"]}
    assert "SURV_FIND_KEY_HOLDER" in active
    assert "SURV_DISCOVER_ACCOUNTS" in active   # 中身はないので標準の経路が残る


def test_survivor_non_heir_scope(engine):
    """⑥ 相続人でない人には、相続人しかできないノードを出さない。"""
    cond = UserCondition(days_since_death=5, viewer_is_legal_heir=False)
    res = run(engine, "SURVIVOR", cond)
    active = {n["id"] for n in res["all_active_nodes"]}
    assert "SURV_NON_HEIR_SCOPE" in active
    assert "SURV_ESTATE_DIVISION" not in active
    assert "SURV_DECIDE_ACCEPT_OR_RENOUNCE" not in active
    assert "SURV_DEATH_NOTICE" in active        # 届出はできる


def test_survivor_foreign_route(engine):
    """海外資産があるとき、現地の手続が経路に加わる。"""
    cond = UserCondition(days_since_death=20, has_foreign_assets=True)
    res = run(engine, "SURVIVOR", cond)
    active = {n["id"] for n in res["all_active_nodes"]}
    assert "SURV_FOREIGN_PROBATE_START" in active


# =============================================================================
# 3. 構造テスト — S03 の決めごとを固定する
# =============================================================================

def test_rule2_vanished_prerequisite_is_satisfied(engine):
    """② ルール2。条件で消えた前提は、満たされたものとみなす。

    入口が見つかると SURV_DISCOVER_ACCOUNTS と SURV_DISCOVER_DEBT が消える。
    その二つを前提に持つ SURV_LIST_ASSETS が、永久に開かないままにならないこと。
    ここが落ちると、一番見せたい場面（本人の一手で遺族の道が短くなる）が壊れる。
    """
    cond = UserCondition(days_since_death=10, entry_found=True, entry_locked=False)
    res = run(engine, "SURVIVOR", cond)
    assert res["statuses"]["SURV_LIST_ASSETS"] == NodeStatus.READY.value


def test_rule2_prerequisite_still_blocks_when_present(engine):
    """ルール2の裏。前提が存在していて未了なら、ちゃんと塞がれていること。"""
    cond = UserCondition(days_since_death=10, entry_found=False)
    res = run(engine, "SURVIVOR", cond)
    assert res["statuses"]["SURV_LIST_ASSETS"] == NodeStatus.NOT_READY.value


def test_rule3_survivor_result_never_contains_self_nodes(engine):
    """⑤ ルール3。遺族の計算に本人のノードが混ざらない。

    本人のアプリと遺族のアプリは通信しない。混ざったら設計思想が崩れている。
    """
    cond = UserCondition(days_since_death=10, entry_found=True)
    res = run(engine, "SURVIVOR", cond)
    collected = (
        ids(res["all_active_nodes"])
        + ids(res["today_actions"])
        + [w["node_id"] for w in res["irreversible_warnings"]]
        + [a["node_id"] for a in res["deadline_alerts"]]
        + list(res["statuses"].keys())
    )
    assert collected, "何も返っていない"
    assert not [nid for nid in collected if nid.startswith("SELF_")]
    assert res["mitigation_preview"] == []


def test_rule4_deadline_alert_even_when_blocked(engine):
    """⑧ ルール4の例外。期限が近いものは、前提が終わっていなくても出す。

    死後80日。放棄の期限は3か月。前提（戸籍・相続人・目録）は終わっていない。
    それでも画面に出なければ、期限に間に合わない。
    """
    cond = UserCondition(days_since_death=80, entry_found=False)
    res = run(engine, "SURVIVOR", cond)
    assert res["statuses"]["SURV_DECIDE_ACCEPT_OR_RENOUNCE"] == NodeStatus.NOT_READY.value
    alerts = {a["node_id"]: a for a in res["deadline_alerts"]}
    assert "SURV_DECIDE_ACCEPT_OR_RENOUNCE" in alerts
    a = alerts["SURV_DECIDE_ACCEPT_OR_RENOUNCE"]
    assert a["urgency"] == "MUST_IMMEDIATE"
    assert 0 <= a["days_left"] <= 14
    assert a["blocked_by"], "何に塞がれているかを示せていない"


def test_rule4_extend_deliberation_is_reachable_early(engine):
    """期間の伸長は、調べの途中でも手が届くこと（間に合わないときの逃げ道）。"""
    cond = UserCondition(days_since_death=70, entry_found=False)
    res = run(engine, "SURVIVOR", cond,
              completed={"SURV_DEATH_NOTICE", "SURV_FIND_ENTRY", "SURV_DISCOVER_DEBT"})
    assert res["statuses"]["SURV_EXTEND_DELIBERATION"] == NodeStatus.READY.value


def test_rule4_add_months_rounds_at_month_end():
    """⑨-1 月は月で数える。月末は繰り上げずに丸める。"""
    # 2026-01-31 の3か月後は 2026-04-30。90日後なら 2026-05-01 になってしまう。
    assert add_months(date(2026, 1, 31), 3) == date(2026, 4, 30)
    assert date(2026, 1, 31) + timedelta(days=90) == date(2026, 5, 1)


def test_rule4_engine_counts_months_not_days(engine):
    """⑨-2 エンジンが実際に月で数えていること。

    死後92日。起算日は 2026-06-05。
      3か月で数えると期限は 2026-09-05 ＝ 今日。残り0日、まだ間に合う。
      90日で数えると期限は 2026-09-03。もう過ぎたことになる。
    ここを取り違えると、間に合う人に「もう過ぎました」と言うことになる。
    """
    cond = UserCondition(days_since_death=92, entry_found=False)
    res = run(engine, "SURVIVOR", cond)
    assert res["days_left"]["SURV_DECIDE_ACCEPT_OR_RENOUNCE"] == 0
    assert res["urgency"]["SURV_DECIDE_ACCEPT_OR_RENOUNCE"] == "MUST_IMMEDIATE"


def test_rule5_ordering_is_deterministic(engine):
    """⑪ 同じ入力なら、並びまで完全に一致すること。"""
    cond = UserCondition(days_since_death=30, entry_found=False)
    a = ids(run(engine, "SURVIVOR", cond)["today_actions"])
    b = ids(run(engine, "SURVIVOR", cond)["today_actions"])
    assert a == b
    assert len(set(a)) == len(a)


def test_rule5_irreversible_ranks_above_plain(engine):
    """ルール5。不可逆のあるものは、同じ緊急度の普通のものより先に出る。"""
    cond = UserCondition(days_since_death=100, entry_found=False)
    done = {"SURV_DEATH_NOTICE", "SURV_FUNERAL_ARRANGEMENT", "SURV_KNOW_SAFE_ACTIONS",
            "SURV_FIND_ENTRY", "SURV_WARN_DEPOSIT", "SURV_COLLECT_KOSEKI",
            "SURV_DETERMINE_HEIRS", "SURV_DISCOVER_ACCOUNTS", "SURV_DISCOVER_DEBT",
            "SURV_LIST_ASSETS"}
    today = ids(run(engine, "SURVIVOR", cond, completed=done)["today_actions"])
    # 期限を過ぎた不可逆（承認・放棄の判断）が、普通の作業より先に出る
    assert today[0] == "SURV_DECIDE_ACCEPT_OR_RENOUNCE"


def test_rule6_warnings_have_fact_and_exception(engine):
    """⑦ 警告は必ず二段（事実と逃げ道）であること。"""
    for scope, cond in (
        ("SURVIVOR", UserCondition(days_since_death=1)),
        ("SELF", UserCondition(is_business_owner=True, has_foreign_assets=True,
                               has_own_home=True, estate_over_basic_deduction=True)),
    ):
        res = run(engine, scope, cond)
        assert res["irreversible_warnings"], scope
        for w in res["irreversible_warnings"]:
            assert w["warning_fact"].strip(), w["node_id"]
            assert w["warning_exception"].strip(), w["node_id"]
            assert w["lost_option"].strip(), w["node_id"]


def test_rule6_no_warning_for_finished_node(engine):
    """済んだノードの警告は出さない。赤い帯が埋もれるのを防ぐ。"""
    cond = UserCondition(days_since_death=40)
    res = run(engine, "SURVIVOR", cond, completed={"SURV_WARN_DEPOSIT"})
    assert "SURV_WARN_DEPOSIT" not in {w["node_id"] for w in res["irreversible_warnings"]}


def test_rule7_event_resets_only_volatile(engine):
    """⑩ 新しい遺言が出ても、戸籍と目録は無駄にならない。"""
    cond = UserCondition(days_since_death=100, entry_found=False, events=["NEW_WILL_FOUND"])
    done = {"SURV_DEATH_NOTICE", "SURV_COLLECT_KOSEKI", "SURV_DETERMINE_HEIRS",
            "SURV_DISCOVER_ACCOUNTS", "SURV_DISCOVER_DEBT", "SURV_LIST_ASSETS",
            "SURV_DECIDE_ACCEPT_OR_RENOUNCE", "SURV_ESTATE_DIVISION"}
    res = run(engine, "SURVIVOR", cond, completed=done)
    assert res["reset_by_events"] == ["SURV_ESTATE_DIVISION"]
    survived = {s["node_id"] for s in res["surviving_after_event"]}
    assert "SURV_COLLECT_KOSEKI" in survived
    assert "SURV_LIST_ASSETS" in survived
    assert "SURV_ESTATE_DIVISION" not in survived
    assert res["statuses"]["SURV_COLLECT_KOSEKI"] == NodeStatus.DONE.value


def test_rule8_nothing_claims_expert_verification(engine):
    """⑫ 専門家の確認を受けたと書いてあるノードは一つもない。未検証の表示が出る。"""
    res = run(engine, "SURVIVOR", UserCondition(days_since_death=1))
    assert res["verification_notice"].strip()
    for scope in ("SELF", "FAMILY_CHILD", "SURVIVOR"):
        for node in engine.nodes_by_scope[scope].values():
            lm = node["legal_metadata"]
            assert lm["verification_status"] != "EXPERT_VERIFIED", node["id"]
            if lm["verification_status"] in ("DRAFT", "SELF_REVIEWED"):
                assert lm["verified_at"] is None, node["id"]
            else:
                assert lm["verified_at"] and lm["source_url"], node["id"]


def test_waiting_is_distinct_from_not_ready(engine):
    """「まだできない」と「相手の返事を待っている」は別物である。"""
    cond = UserCondition(days_since_death=30, entry_found=False)
    res = run(engine, "SURVIVOR", cond,
              completed={"SURV_DEATH_NOTICE", "SURV_FIND_ENTRY"},
              statuses={"SURV_COLLECT_KOSEKI": NodeStatus.WAITING})
    waiting = {w["node_id"] for w in res["waiting"]}
    assert "SURV_COLLECT_KOSEKI" in waiting
    reason = next(w["reason"] for w in res["waiting"] if w["node_id"] == "SURV_COLLECT_KOSEKI")
    assert "落ち度" in reason
    assert res["not_ready_count"] > 0
    assert "SURV_COLLECT_KOSEKI" not in ids(res["today_actions"])


# =============================================================================
# 4. 式の読み取り — 黙って消えないこと
# =============================================================================

def test_expr_reads_json_style_syntax():
    """JSONに書いてある書き方（true / && / ||）がそのまま読めること。"""
    v = {"a": True, "b": False, "c": None}
    assert eval_expr("condition.a == true", v) is True
    assert eval_expr("condition.b == true", v) is False
    assert eval_expr("condition.a == true && condition.b == true", v) is False
    assert eval_expr("condition.a == true || condition.b == true", v) is True
    assert eval_expr("condition.b != true", v) is True
    assert eval_expr("condition.c == null", v) is True
    assert eval_expr("condition.c == true", v) is False   # 未判定は「超える」ではない
    assert eval_expr(None, v) is True


def test_expr_raises_instead_of_silently_hiding_node():
    """書き間違いは例外にする。False を返してノードを黙って消してはならない。"""
    with pytest.raises(ExprError):
        eval_expr("condition.no_such_key == true", {"a": True})
    with pytest.raises(ExprError):
        eval_expr("condition.a ==", {"a": True})
    with pytest.raises(ExprError):
        eval_expr("condition.a == true &&", {"a": True})


def test_every_exists_if_in_data_is_readable(engine):
    """すべてのノードの exists_if が、実際に読めること。

    式の誤りが一つでもあれば、そのノードは本番で黙って現れない。
    ここで落としておけば、当日その事故は起きない。
    """
    values = UserCondition().to_eval_dict()
    for scope in ("SELF", "FAMILY_CHILD", "SURVIVOR"):
        for node in engine.nodes_by_scope[scope].values():
            try:
                eval_expr(node.get("exists_if"), values)
            except ExprError as e:
                pytest.fail(f"{node['id']} の exists_if が読めない: {e}")
