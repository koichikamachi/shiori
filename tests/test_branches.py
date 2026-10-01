"""
分かれる道（branches）と、乗り換え案内（点線のヒゲ）。

「相続するか、放棄するか、限定して承認するかを決める」の駅から、道は3本に分かれる。
このアプリは選び方を示さない。どの場面で検討される道かを、同じ重さで並べるだけである。
"""
import json
import re
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from server import app
from tools.render_map_svg import render

ROOT = Path(__file__).resolve().parent.parent
TODAY = date(2026, 9, 26)
JUNCTION = "SURV_DECIDE_ACCEPT_OR_RENOUNCE"


def _nodes():
    out = {}
    for f in (ROOT / "data" / "nodes").glob("*.json"):
        for n in json.loads(f.read_text(encoding="utf-8"))["nodes"]:
            out[n["id"]] = n
    return out


def test_the_junction_has_three_neutral_paths():
    nodes = _nodes()
    br = nodes[JUNCTION]["branches"]
    labels = [p["label"] for p in br["paths"]]
    assert labels == ["引き継ぐ（単純承認）", "放棄する（相続放棄）", "限定して承認する（限定承認）"]
    for p in br["paths"]:
        assert p["basis"].startswith("民法")
        if p.get("to_node"):
            assert p["to_node"] in nodes, p["to_node"]
        # 場面を書く。指図はしない
        for bad in ("べき", "しなさい", "おすすめ", "推奨"):
            assert bad not in p["when"] and bad not in p.get("aside", ""), (p["key"], bad)
    assert "選び方を示しません" in br["note"]
    assert "延長" in br["note"] and "第915条第1項ただし書" in br["note"]


def test_whiskers_only_for_destinations_not_on_the_map():
    h = render("surv-day10-nomemo", TODAY)[0]
    # 案内は二行に分けて描くので、一本ぶんの全文は囲みの aria-label から読む
    labels = re.findall(r'class="whg" aria-label="([^"]+)"', h)
    # 承認か放棄か：話し合い・次順位に伝える駅は線で描かれているので、ヒゲは限定承認の1本だけ
    assert "分からないときは：限定承認（全員で家裁へ） ↗" in labels
    # 「次順位への通知」駅は削除した（2026-09-27）。放棄の道の行き先（家裁への申述）は地図に無いので、ヒゲで示す
    assert "借金が多いときは：相続放棄（家裁へ） ↗" in labels
    # 遺言の有無・相続税の判定：地図に無い行き先だけ。1駅あたり2本まで
    assert "自筆・自宅保管なら：家裁で検認 ↗" in labels
    assert "基礎控除以下なら：申告不要 ↗" in labels
    assert len(labels) <= 2 * 3


def test_the_two_new_junctions_follow_the_facts_not_a_choice():
    nodes = _nodes()
    for nid, n_paths in (("SURV_FIND_WILL", 3), ("SURV_TAX_FILING_NEED", 3)):
        br = nodes[nid]["branches"]
        assert len(br["paths"]) == n_paths
        assert br["when_label"] == "当てはまる場合"
        for p in br["paths"]:
            assert len(p["station"]) <= 8
            for bad in ("べき", "しなさい", "おすすめ", "推奨"):
                assert bad not in p["when"] and bad not in p.get("aside", ""), (nid, p["key"], bad)
    assert "このアプリでは行いません" in nodes["SURV_TAX_FILING_NEED"]["branches"]["note"]


def test_express_and_local_stations_are_drawn_differently():
    h = render("surv-day10-nomemo", TODAY)[0]
    assert 'class="stt exp">相続人の確定<' in h
    assert 'class="stt loc">戸籍の収集<' in h


def test_no_whisker_on_maps_without_a_junction():
    h = render("self-pensioner", TODAY)[0]
    assert 'class="whlbl"' not in h


def test_the_drawer_draws_the_branches_as_a_small_rail_map():
    """
    ドロワーの分かれる道は、箇条書きではなく小さな路線図で描く（2026-09-26 試作どおり）。
    起点の駅 → 同じ太さのレール → 終点の駅。説明は終点の駅を押したときだけ開く。
    期間の延長は、起点に戻る待避線で表す。
    """
    html = TestClient(app).get("/map/surv-day10-nomemo").text
    assert "function renderBranches(br)" in html and "function showBranch(i)" in html
    assert 'id="br-svg"' in html
    assert "期間の延長を申し立てる" in html
    assert "D[id].branches" in html and "D[id].short" in html


def test_the_junction_has_short_nouns_for_the_rail_map():
    n = _nodes()[JUNCTION]
    assert n["short_title"] == "承認か放棄か"
    assert n["branches"]["origin"] == "承認か放棄か"
    assert [p["station"] for p in n["branches"]["paths"]] == ["話し合いへ", "家裁で放棄", "全員で家裁へ"]
    for p in n["branches"]["paths"]:
        assert len(p["station"]) <= 8


def test_the_audit_log_is_folded_away_for_readers():
    """
    読む人には結論と安心の一文だけ。照合の足取り・段・内部統制は、検証用の折りたたみ（初期は閉）へ。
    人の確認を「専門家監修済み」と言い換えない（実際にはまだ人の確認待ちの項目があるため）。
    """
    html = TestClient(app).get("/map/surv-day10-nomemo").text
    assert 'id="audit-details"' in html and "<details id=\"audit-details\" open" not in html
    assert "照合プロセスの詳細・監査ログを見る（検証用）" in html
    assert "専門家監修" not in html
    assert "【さらに大事なこと】" in html and "D[id].important" in html
