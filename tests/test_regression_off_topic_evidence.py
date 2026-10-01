"""
回帰テスト：「親にしか答えられないことだけに絞る」で起きた取り違え（2026-09-24）

文部科学省の高等教育の負担軽減のページ
（https://www.mext.go.jp/a_menu/koutou/hutankeigen/1422578.htm）を、
.go.jp だというだけで一次証憑に選び、その引用を根拠として出していた。

確かめること:
  - テーマ外（高等教育・子育てなど）の公的ページは、ドメインにかかわらず選定しない（即死フィルター）
  - Gemini が IRRELEVANT と答えた候補も選定しない
  - 有効な一次証憑がゼロなら、無関係なページを読まず、引用も出さず、
    「公的基準に直接の該当なし」として返す
  - クエリ生成に、業務文脈と除外指示が入っている
"""
from datetime import date
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from server import app
from src.agent.agent import ShioriAgent
from src.agent.tools import EvaluatorRole, is_off_topic

MEXT = {"title": "mext.go.jp", "url": "https://www.mext.go.jp/a_menu/koutou/hutankeigen/1422578.htm", "snippet": ""}
MOJ = {"title": "moj.go.jp", "url": "https://www.moj.go.jp/MINJI/minji07_00025.html", "snippet": ""}
NODE = {"id": "FAMILY_NARROW_WHAT_TO_ASK", "title": "親にしか答えられないことだけに絞る",
        "today_action": "聞きたいことを全部書き出し、そのうち『親にしか答えられないこと』だけに印をつける。"}
CHECK = {"check_id": "FAMILY_NARROW_WHAT_TO_ASK#1", "node_id": "FAMILY_NARROW_WHAT_TO_ASK",
         "claim": NODE["title"], "question": NODE["title"]}


def _client(answer="PRIMARY_SOURCE"):
    mc = MagicMock()
    mc.models.generate_content.return_value = MagicMock(text=answer)
    return mc


def test_the_mext_tuition_page_is_off_topic_by_rule():
    assert is_off_topic(MEXT)
    assert not is_off_topic(MOJ)


def test_off_topic_is_never_selected_even_if_gemini_calls_it_primary():
    best, ev = EvaluatorRole(_client("PRIMARY_SOURCE")).evaluate_candidates([MEXT], CHECK["claim"], {"node": NODE})
    assert best is None
    assert MEXT["url"] in ev["excluded_urls"]


def test_gemini_irrelevant_verdict_excludes_a_candidate():
    best, ev = EvaluatorRole(_client("IRRELEVANT")).evaluate_candidates([MOJ], CHECK["claim"], {"node": NODE})
    assert best is None and MOJ["url"] in ev["excluded_urls"]


def test_no_valid_evidence_means_no_public_standard_and_no_quote():
    mc = _client("PRIMARY_SOURCE")
    seen = []
    with patch("src.agent.agent.genai.Client", return_value=mc), \
         patch("src.agent.agent.search_public_sites", return_value=[dict(MEXT)]), \
         patch("src.agent.agent.fetch_page_text") as fetch:
        r = ShioriAgent(api_key="k", on_log=seen.append).run_check(CHECK, NODE, date(2026, 9, 24))
    assert r.verdict == "NOT_FOUND"
    assert r.detected_constraints == {"reason": "NO_RELEVANT_PUBLIC_SOURCE"}
    assert r.evidence == []
    fetch.assert_not_called()                       # 無関係なページは読みにも行かない
    assert any("公的基準は見つかりませんでした" in e["action"] for e in seen)


def test_off_topic_pages_are_not_handed_to_the_reader():
    mc = _client("PRIMARY_SOURCE")
    fetched = []
    with patch("src.agent.agent.genai.Client", return_value=mc), \
         patch("src.agent.agent.search_public_sites", return_value=[dict(MEXT), dict(MOJ)]), \
         patch("src.agent.agent.fetch_page_text", side_effect=lambda u: fetched.append(u) or "本文"):
        ShioriAgent(api_key="k").run_check(CHECK, NODE, date(2026, 9, 24))
    assert fetched == [MOJ["url"]]


def test_query_generation_carries_the_business_context_and_the_exclusion():
    mc = _client("相続 親 意向")
    with patch("src.agent.agent.genai.Client", return_value=mc), \
         patch("src.agent.agent.search_public_sites", return_value=[]):
        ShioriAgent(api_key="k").run_check(CHECK, NODE, date(2026, 9, 24))
    first_prompt = mc.models.generate_content.call_args_list[0].kwargs["contents"]
    assert "相続・終活・生前整理" in first_prompt
    assert "高等教育" in first_prompt and "含めないでください" in first_prompt


def test_the_drawer_knows_how_to_say_no_public_standard():
    html = TestClient(app).get("/map/surv-day1").text
    assert "【公的基準に直接の該当なし】" in html
    assert "家族間の対話や個別状況に基づく手続きです。" in html
