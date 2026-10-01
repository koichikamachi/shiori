"""
回帰テスト：「相続人を確定する」で起きた取り違え（2026-09-24）

検索結果に複数の市のページ（funabashi / nichinan / sosa）が並んだだけで、
Gemini に読ませる前に UNVERIFIED と決めつけ、
「自治体によって異なります。市役所に問い合わせてください」という案を返していた。
民法の相続人の順位は国の制度であり、市ごとに違うものではない。

ここでは次のことを確かめる。
  - 自治体ページが混じっても、比べる役（Gemini）まで進むこと
  - PDF は読まずに飛ばすこと
  - 一次証憑には、国の機関の HTML ページを選ぶこと
  - 原文と照合できた引用の無い結果は、「検証済アーカイブ」に残さないこと
"""
import json
from datetime import date
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import server
from src.agent.agent import ShioriAgent
from src.agent.schema import CheckResult
from src.agent.tools import EvaluatorRole, source_tier

CHECK = {"check_id": "SURV_DETERMINE_HEIRS#1", "node_id": "SURV_DETERMINE_HEIRS",
         "claim": "相続人を確定する　根拠（下書き）: 民法第887条〜第890条（相続人）",
         "question": "放棄による順位の繰り上がりと、代襲の範囲"}
RESULTS = [
    {"title": "courts", "url": "https://www.courts.go.jp/sendai/vc-files/sendai/file/2018shosiki2.pdf", "snippet": ""},
    {"title": "funabashi", "url": "https://www.city.funabashi.lg.jp/machi/juutaku/009/p097365.html", "snippet": ""},
    {"title": "nichinan", "url": "https://www.city.nichinan.lg.jp/soshikikarasagasu/zeimuka/1/1/6792.html", "snippet": ""},
    {"title": "sosa", "url": "https://www.city.sosa.lg.jp/page/x.html", "snippet": ""},
    {"title": "moj", "url": "https://www.moj.go.jp/MINJI/minji07_00025.html", "snippet": ""},
]
MODEL_JSON = json.dumps({"check_id": CHECK["check_id"], "node_id": CHECK["node_id"], "claim": CHECK["claim"],
                         "verdict": "UNCHANGED", "confidence": "MEDIUM", "checked_at": "2026-09-24",
                         "evidence": [], "risk_if_wrong": "r", "human_review_required": True})


def _run():
    mc = MagicMock()
    mc.models.generate_content.return_value = MagicMock(text=MODEL_JSON)
    fetched, seen = [], []
    with patch("src.agent.agent.genai.Client", return_value=mc), \
         patch("src.agent.agent.search_public_sites", return_value=list(RESULTS)), \
         patch("src.agent.agent.fetch_page_text", side_effect=lambda u: fetched.append(u) or "本文"):
        r = ShioriAgent(api_key="k", on_log=seen.append).run_check(CHECK, {"id": CHECK["node_id"]}, date(2026, 9, 24))
    return r, fetched, seen, mc


def test_municipal_pages_do_not_short_circuit_to_unverified():
    r, _, seen, mc = _run()
    # Gemini の判定まで届いている（根拠の引用が無いので、関門が UNCERTAIN に下げるのは正しい）
    assert r.verdict != "UNVERIFIED"
    assert r.proposed_patch is None                       # call_local_government_office のような案は出ない
    assert any(e["role"] == "Comparator" for e in seen)
    prompt = mc.models.generate_content.call_args_list[-1].kwargs["contents"]
    assert "それだけを理由に UNVERIFIED にしないでください" in prompt


def test_pdf_is_skipped_and_the_national_page_is_read_first():
    _, fetched, seen, _ = _run()
    assert not any(u.endswith(".pdf") for u in fetched)
    assert fetched[0].startswith("https://www.moj.go.jp/")


def test_reader_skips_a_pdf_even_when_it_comes_first():
    from src.agent.agent import ReaderRole
    seen = []
    with patch("src.agent.agent.fetch_page_text", return_value="本文") as f:
        pages = ReaderRole().fetch_pages(RESULTS[:2], log=lambda *a: seen.append(a))
    assert list(pages) == [RESULTS[1]["url"]]
    assert any("PDF" in a[1] for a in seen)


def test_evaluator_prefers_a_national_html_page():
    best, _ = EvaluatorRole(None).evaluate_candidates(RESULTS, CHECK["claim"])
    assert best["url"].startswith("https://www.moj.go.jp/")
    assert source_tier(RESULTS[0]["url"]) < source_tier(RESULTS[1]["url"]) < source_tier(RESULTS[4]["url"])


@patch("src.agent.agent.ShioriAgent.run_check")
def test_a_result_without_a_verified_quote_is_not_archived(mock_run, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    mock_run.return_value = CheckResult(check_id=CHECK["check_id"], node_id=CHECK["node_id"], claim="c",
                                        verdict="UNVERIFIED", checked_at="2026-09-24", human_review_required=True)
    TestClient(server.app).post("/api/agent/check", json={"node_id": "SURV_DETERMINE_HEIRS"})
    assert not (server.ARCHIVE_DIR / "SURV_DETERMINE_HEIRS.json").exists()
