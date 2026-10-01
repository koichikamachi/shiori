"""
Writer の文体ルールは外部ファイル（src/agent/prompts/writer_style.md）から読む。
  - ファイルの中身が、Gemini への system instruction として届くこと
  - ファイルが無くても、既定の文体で照合が止まらずに進むこと
  - 引用（quote）とパッチ値には文体を当てない、という線引きがプロンプトに残っていること
"""
import json
from datetime import date
from unittest.mock import MagicMock, patch

from src.agent import agent as agent_mod
from src.agent.agent import ShioriAgent, load_writer_style, WRITER_STYLE_PATH

CHECK = {"check_id": "X#1", "node_id": "X", "claim": "c", "question": "q"}
OK_JSON = json.dumps({"check_id": "X#1", "node_id": "X", "claim": "c", "verdict": "UNCHANGED",
                      "confidence": "HIGH", "checked_at": "2026-09-24", "evidence": [],
                      "risk_if_wrong": "r", "human_review_required": True})


def test_the_shipped_style_file_exists_and_carries_the_monologue_rule():
    text = WRITER_STYLE_PATH.read_text(encoding="utf-8")
    assert "自問自答" in text and "（正当事由）" in text and "（起算点）" in text
    body, src = load_writer_style()
    assert body.strip() == text.strip()
    assert "writer_style.md" in src


def _run(monkeypatch):
    mc = MagicMock()
    mc.models.generate_content.return_value = MagicMock(text=OK_JSON)
    seen = []
    with patch("src.agent.agent.genai.Client", return_value=mc), \
         patch("src.agent.agent.search_public_sites",
               return_value=[{"title": "t", "url": "https://www.moj.go.jp/a.html", "snippet": ""}]), \
         patch("src.agent.agent.fetch_page_text", return_value="本文"):
        ShioriAgent(api_key="k", on_log=seen.append).run_check(CHECK, {"id": "X"}, date(2026, 9, 24))
    last = mc.models.generate_content.call_args_list[-1]
    return last.kwargs["config"].system_instruction, last.kwargs["contents"], seen


def test_the_style_file_reaches_gemini_as_system_instruction(tmp_path, monkeypatch):
    f = tmp_path / "style.md"
    f.write_text("# テスト用の文体\n- 独白で書く（起算点）", encoding="utf-8")
    monkeypatch.setenv("SHIORI_WRITER_STYLE", str(f))
    si, prompt, seen = _run(monkeypatch)
    assert "独白で書く（起算点）" in si
    assert "quote は公的ページの原文そのまま" in prompt
    assert any(e["role"] == "Writer" and "style.md" in e["detail"] for e in seen)


def test_a_missing_style_file_falls_back_to_the_default(tmp_path, monkeypatch):
    monkeypatch.setenv("SHIORI_WRITER_STYLE", str(tmp_path / "nothing.md"))
    si, _, seen = _run(monkeypatch)
    assert "です・ます調" in si
    assert any(e["role"] == "Writer" and "既定の文体" in e["detail"] for e in seen)
