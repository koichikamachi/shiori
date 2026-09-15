"""
制度追従エージェント（src/agent/agent.py）の単体テスト

ネットワーク・APIアクセスを完全に遮断し、モックを用いて自律動作を検証。
APIキー未設定時のフォールバック、各種エラーハンドリング、五役の協調処理（ハッピーパス）をテスト。
"""
import json
from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from src.agent.agent import ExplorerRole, ReaderRole, ShioriAgent
from src.agent.schema import CheckResult, Evidence, ProposedPatch

TODAY = date(2026, 9, 15)

MOCK_CHECK = {
    "check_id": "SELF_BIZ_SUCCESSION_TAX#1",
    "node_id": "SELF_BIZ_SUCCESSION_TAX",
    "claim": "適用期限は2027-12-31である",
    "question": "適用期限は延長されたか",
}

MOCK_NODE = {
    "id": "SELF_BIZ_SUCCESSION_TAX",
    "actor": "SELF",
    "title": "事業承継税制の確認",
    "today_action": "適用期限を確認する",
    "deadline": {
        "absolute_date": "2027-12-31",
        "clock": "KNOWN",
        "trigger": "相続開始日",
        "gate": "STAYS_OPEN"
    },
    "legal_metadata": {
        "verification_status": "SELF_REVIEWED",
        "verified_at": "2026-09-02",
        "verified_by": "human",
        "source_url": "https://www.chusho.meti.go.jp/fake",
        "needs_check": ["適用期限は延長されたか"]
    }
}


def test_agent_fallback_when_api_key_is_missing(monkeypatch):
    """APIキーが無い環境（またはクライアント初期化未完了）で、クラッシュせずに UNCERTAIN フォールバックされることを検証。"""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    
    # クライアントを作らないエージェント
    agent = ShioriAgent(api_key=None)
    assert agent.client is None
    
    result = agent.run_check(MOCK_CHECK, MOCK_NODE, TODAY)
    assert isinstance(result, CheckResult)
    assert result.verdict == "UNCERTAIN"
    assert result.confidence == "LOW"
    assert "APIキーが設定されていない" in result.risk_if_wrong


def test_explorer_and_reader_cooperation():
    """探す役 (Explorer) と 読む役 (Reader) の連携、および検索・取得結果なし時の動作を検証。"""
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value.text = "事業承継税制 適用期限"

    with patch("src.agent.agent.search_public_sites") as mock_search, \
         patch("src.agent.agent.fetch_page_text") as mock_fetch:
         
        # 1. 探す役の検証
        mock_search.return_value = [{"title": "公的情報", "url": "https://www.nta.go.jp/fake"}]
        explorer = ExplorerRole(mock_client)
        urls = explorer.generate_query_and_search(MOCK_CHECK)
        assert len(urls) == 1
        assert urls[0]["url"] == "https://www.nta.go.jp/fake"
        mock_search.assert_called_once_with("事業承継税制 適用期限")

        # 2. 読む役の検証
        mock_fetch.return_value = "取得できた本文です"
        reader = ReaderRole()
        pages = reader.fetch_pages(urls)
        assert "https://www.nta.go.jp/fake" in pages
        assert pages["https://www.nta.go.jp/fake"] == "取得できた本文です"
        mock_fetch.assert_called_once_with("https://www.nta.go.jp/fake")


def test_agent_fallback_when_no_public_pages_found(monkeypatch):
    """公的ページが1件も取得できなかった場合に NOT_FOUND が返ることを検証。"""
    monkeypatch.setenv("GEMINI_API_KEY", "dummy_key")
    
    with patch("google.genai.Client") as mock_client_cls, \
         patch("src.agent.agent.search_public_sites") as mock_search:
         
        mock_search.return_value = [] # 検索結果ゼロ
        
        agent = ShioriAgent(api_key="dummy_key")
        result = agent.run_check(MOCK_CHECK, MOCK_NODE, TODAY)
        
        assert result.verdict == "NOT_FOUND"
        assert result.confidence == "LOW"
        assert "公的情報が見つからなかった" in result.risk_if_wrong


@patch("google.genai.Client")
@patch("src.agent.agent.search_public_sites")
@patch("src.agent.agent.fetch_page_text")
def test_agent_happy_path(mock_fetch, mock_search, mock_client_cls, monkeypatch):
    """五役が協調し、Gemini APIからの出力が正常に関門をパスして CheckResult を出力するハッピーパスを検証。"""
    monkeypatch.setenv("GEMINI_API_KEY", "dummy_key")
    
    # 検索とテキスト取得のモック
    mock_search.return_value = [{"title": "公的情報", "url": "https://www.nta.go.jp/fake"}]
    mock_fetch.return_value = "特例承継計画の提出期限は令和9年9月30日までに延長されました。特例措置の適用期限（令和9年12月31日）は延長されていません。"

    # Geminiクライアントのモック
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    
    # 比べる役・疑う役・書く役を内包したLLM返答のモック
    mock_response = MagicMock()
    mock_response.text = json.dumps({
        "check_id": "SELF_BIZ_SUCCESSION_TAX#1",
        "node_id": "SELF_BIZ_SUCCESSION_TAX",
        "claim": "適用期限は2027-12-31である",
        "verdict": "UNCHANGED",
        "confidence": "HIGH",
        "checked_at": "2026-09-15",
        "evidence": [
            {
                "url": "https://www.nta.go.jp/fake",
                "publisher": "国税庁",
                "page_date": "2026-04-01",
                "quote": "特例措置の適用期限（令和9年12月31日）は延長されていません",
                "quote_verified": False # guards.py 側で True になるはず
            }
        ],
        "proposed_patch": None,
        "risk_if_wrong": "期限切れを放置するリスク",
        "escalation": None,
        "human_review_required": True
    })
    mock_client.models.generate_content.return_value = mock_response

    agent = ShioriAgent(api_key="dummy_key")
    result = agent.run_check(MOCK_CHECK, MOCK_NODE, TODAY)

    # 最終的な CheckResult フィールドの検証
    assert result.verdict == "UNCHANGED"
    # 単一の公的ソースのみなので、guards.py の制限（r.confidence = "MEDIUM"）が働き
    # HIGH から MEDIUM に適切に降格（安全フォールバック）されていることを検証
    assert result.confidence == "MEDIUM"
    assert len(result.evidence) == 1
    assert result.evidence[0].quote_verified is True # guards.py の sanitize で検証済みマークが付いている
    assert result.evidence[0].url == "https://www.nta.go.jp/fake"


@patch("google.genai.Client")
@patch("src.agent.agent.search_public_sites")
@patch("src.agent.agent.fetch_page_text")
def test_agent_llm_failure_graceful_fallback(mock_fetch, mock_search, mock_client_cls, monkeypatch):
    """Gemini APIの呼び出しエラーやJSON構造破損が起きても、クラッシュせず UNCERTAIN で返されることを検証。"""
    monkeypatch.setenv("GEMINI_API_KEY", "dummy_key")
    
    mock_search.return_value = [{"title": "公的情報", "url": "https://www.nta.go.jp/fake"}]
    mock_fetch.return_value = "正常なページ内容"

    # APIエラーを模倣
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.models.generate_content.side_effect = RuntimeError("Gemini API Quota Limit Exceeded")

    agent = ShioriAgent(api_key="dummy_key")
    result = agent.run_check(MOCK_CHECK, MOCK_NODE, TODAY)

    assert result.verdict == "UNCERTAIN"
    assert result.confidence == "LOW"
    assert "LLM推論またはJSON構造化処理中にエラーが発生しました" in result.risk_if_wrong
    assert "Gemini API Quota Limit Exceeded" in result.escalation
