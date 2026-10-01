"""
Phase 3：本線（その場の照合）と、検証済アーカイブへの切り替え。

約束は三つ。
  1. 画面に「デモ」の切り替えは無い。リクエストは node_id だけ。
  2. 外部に接続できないときは、以前に本線で実際に照合した記録を、照合日つきで出す。
     記録が無ければ、照合していないと明示する（作り物を出さない）。
  3. 照合の足取り（Explorer → Evaluator → Reader → …）は、起きた順にストリームで流れる。
"""
import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import server
from server import app
from src.agent.schema import CheckResult, Evidence
from src.config import config

client = TestClient(app)
NODE = "SELF_BANK_LIST"


def _no_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(config, "GEMINI_API_KEY", None)


def _stream(node_id=NODE):
    r = client.post("/api/agent/check/stream", json={"node_id": node_id})
    assert r.status_code == 200
    return [json.loads(l) for l in r.text.splitlines() if l.strip()]


def _ok_result():
    return CheckResult(check_id=f"{NODE}#1", node_id=NODE, claim="c", verdict="UNCHANGED",
                       confidence="HIGH", checked_at="2026-09-23", risk_if_wrong="r",
                       early_warnings=["✓ 『x』を選定"], detected_ambiguities=["FORM_2: 時点軸による変化"],
                       evidence=[Evidence(url="https://www.moj.go.jp/a.html", quote="原文の一節です。",
                                          quote_verified=True)],
                       human_review_required=True)


def test_the_page_has_no_demo_switch():
    html = client.get("/map/surv-day10-nomemo").text
    assert "demo-mode-checkbox" not in html
    assert "demo_mode" not in html
    assert "デモ" not in html
    assert "/api/agent/check/stream" in html


def test_stream_without_key_and_without_record_says_it_did_not_check(monkeypatch):
    _no_key(monkeypatch)
    events = _stream()
    assert events[-1]["type"] == "result"
    res = events[-1]["result"]
    assert res["action"] == "DRY_RUN"
    assert "archive" not in res
    assert "照合はまだ行っていません" in res["reason"]
    assert any(e["type"] == "log" and e["role"] == "System" for e in events)


@patch("src.agent.agent.ShioriAgent.run_check")
def test_a_live_success_is_archived_and_shown_later_with_its_date(mock_run, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    mock_run.return_value = _ok_result()
    live = client.post("/api/agent/check", json={"node_id": NODE}).json()
    assert live["result"]["action"] == "UNCHANGED"
    assert "archive" not in live["result"]
    assert (server.ARCHIVE_DIR / f"{NODE}.json").exists()

    _no_key(monkeypatch)
    res = _stream()[-1]["result"]
    assert res["action"] == "UNCHANGED"
    assert res["archive"]["archived_at"]
    assert "GEMINI_API_KEY" in res["archive"]["reason"]
    assert res["early_warnings"] == ["✓ 『x』を選定"]


@patch("src.agent.agent.ShioriAgent.run_check")
def test_a_network_failure_falls_back_to_the_record_only_if_one_exists(mock_run, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    failed = CheckResult(check_id=f"{NODE}#1", node_id=NODE, claim="c", verdict="UNCERTAIN",
                         checked_at="2026-09-23", human_review_required=True)

    def run_failing(self, *a, **k):
        self.infra_error = "Gemini の呼び出しに失敗しました: 503"
        return failed

    # 記録が無ければ、本線の途中結果をそのまま出す（アーカイブを装わない）
    with patch("src.agent.agent.ShioriAgent.run_check", run_failing):
        res = client.post("/api/agent/check", json={"node_id": NODE}).json()["result"]
    assert res["action"] == "UNCERTAIN" and "archive" not in res
    assert not (server.ARCHIVE_DIR / f"{NODE}.json").exists()   # 失敗は記録しない

    # 成功の記録があれば、そちらを照合日つきで出す
    mock_run.return_value = _ok_result()
    client.post("/api/agent/check", json={"node_id": NODE})
    with patch("src.agent.agent.ShioriAgent.run_check", run_failing):
        res = client.post("/api/agent/check", json={"node_id": NODE}).json()["result"]
    assert res["action"] == "UNCHANGED"
    assert "503" in res["archive"]["reason"]


def test_stream_rejects_bad_ids_before_streaming():
    assert client.post("/api/agent/check/stream", json={"node_id": "../x"}).status_code == 400
    assert client.post("/api/agent/check/stream", json={"node_id": "NOPE_NODE"}).status_code == 404


@patch("src.agent.agent.genai.Client")
@patch("src.agent.agent.fetch_page_text")
@patch("src.agent.agent.search_public_sites")
def test_the_log_flows_explorer_then_evaluator_then_reader(mock_search, mock_fetch, mock_cls, monkeypatch):
    from datetime import date
    from src.agent.agent import ShioriAgent
    mock_search.return_value = [
        {"title": "相続登記の申請義務化", "url": "https://www.moj.go.jp/a.html", "snippet": "令和6年4月から施行"},
        {"title": "相続税の申告", "url": "https://www.nta.go.jp/b.html", "snippet": "ただし例外があります"},
    ]
    mock_fetch.return_value = "本文"
    mc = MagicMock()
    mc.models.generate_content.side_effect = Exception("boom")   # Gemini は全部失敗させる
    mock_cls.return_value = mc

    seen = []
    agent = ShioriAgent(api_key="k", on_log=seen.append)
    result = agent.run_check({"check_id": "X#1", "node_id": "X", "claim": "c", "question": "q"},
                             {"id": "X"}, date(2026, 9, 23))
    roles = [e["role"] for e in seen]
    assert roles.index("Explorer") < roles.index("Evaluator") < roles.index("Reader")
    assert any("一次証憑" in e["action"] for e in seen)
    assert any("曖昧さ検出" in e["action"] for e in seen)
    # 曖昧さと早期警告は、Evaluator（機械）が結果に載せる
    assert result.detected_ambiguities and result.early_warnings
    # Gemini の失敗は「通信の失敗」として区別され、判定の結論とは扱わない
    assert agent.infra_error and "Gemini" in agent.infra_error
