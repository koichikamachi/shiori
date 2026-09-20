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
from src.config import config

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
    # config は起動時に .env を読んで保持している。環境変数を消すだけでは、
    # 開発機（.env に鍵がある）で「鍵が無い状態」を再現できない。そこも外す。
    monkeypatch.setattr(config, "GEMINI_API_KEY", None)

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


# ---- 実機で踏んだ3つの地雷の回帰テスト ------------------------------------
# いずれも「静かに UNCERTAIN / NOT_FOUND に落ちる」形で現れるので、
# 気づくのに手間がかかった。二度踏まないよう、ここで固定する。

def test_gemini_schema_carries_no_additional_properties():
    """
    `extra: "forbid"` は additionalProperties を生み、Gemini API は
    400 INVALID_ARGUMENT（Unknown name "additional_properties"）で
    リクエスト全体を弾く。送る側からは必ず落ちていること。
    """
    import json as _json
    from src.agent.agent import gemini_schema

    s = gemini_schema(CheckResult)
    dumped = _json.dumps(s)
    assert "additional_properties" not in dumped
    assert "additionalProperties" not in dumped
    # $ref は Gemini 側で解決されない。展開されていること。
    assert "$ref" not in dumped and "$defs" not in dumped
    # 型名はプロトコルの enum 名に合わせて大文字
    assert s["type"] == "OBJECT"
    assert s["properties"]["check_id"]["type"] == "STRING"
    # Optional[...] は anyOf ではなく nullable に畳まれていること
    assert s["properties"]["escalation"]["nullable"] is True


def test_gemini_schema_hides_the_fields_the_machine_fills():
    """関門の判定結果は機械が付ける。モデルに書かせる余地を与えない。"""
    from src.agent.agent import gemini_schema

    s = gemini_schema(CheckResult)
    assert "rejected" not in s["properties"]
    assert "rejected" not in s.get("required", [])


def test_the_retired_model_is_not_used():
    """gemini-2.5-flash は新規利用に対して 404 を返す。使っていないこと。"""
    from src.agent.agent import GEMINI_MODEL

    assert GEMINI_MODEL != "gemini-2.5-flash"


def test_seed_url_is_taken_only_from_a_verified_primary_source():
    """
    検索が使えないときの逃げ道は、**人が一次資料として確かめた URL** に限る。
    下書き段階の URL を機械が勝手に根拠として読みに行く道は作らない。
    """
    verified = {
        "title": "確かめた駅",
        "legal_metadata": {"verification_status": "SOURCE_VERIFIED",
                           "source_url": "https://www.moj.go.jp/MINJI/x.html"},
    }
    assert ExplorerRole.seed_from_node(verified)[0]["url"] == "https://www.moj.go.jp/MINJI/x.html"

    # 下書きは種にしない
    draft = {"legal_metadata": {"verification_status": "SELF_REVIEWED",
                                "source_url": "https://www.moj.go.jp/MINJI/x.html"}}
    assert ExplorerRole.seed_from_node(draft) == []

    # 公的ドメインでない URL は、確かめ済みでも種にしない
    outside = {"legal_metadata": {"verification_status": "SOURCE_VERIFIED",
                                  "source_url": "https://example.com/x.html"}}
    assert ExplorerRole.seed_from_node(outside) == []

    assert ExplorerRole.seed_from_node(None) == []
    assert ExplorerRole.seed_from_node({}) == []


def _mock_gemini(mock_client_cls, payload):
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    response = MagicMock()
    response.text = json.dumps(payload)
    mock_client.models.generate_content.return_value = response
    return mock_client


@patch("google.genai.Client")
@patch("src.agent.agent.search_public_sites")
@patch("src.agent.agent.fetch_page_text")
def test_human_review_required_is_forced_true_even_if_the_model_says_false(
        mock_fetch, mock_search, mock_client_cls, monkeypatch):
    """
    モデルは human_review_required に false や null を返してくる。
    そのまま検証にかけると ValidationError で UNCERTAIN に落ちる。
    自己申告は機械が上書きすること。
    """
    monkeypatch.setenv("GEMINI_API_KEY", "dummy_key")
    mock_search.return_value = [{"title": "公的情報", "url": "https://www.nta.go.jp/fake"}]
    mock_fetch.return_value = "特例措置の適用期限（令和9年12月31日）は延長されていません。"

    _mock_gemini(mock_client_cls, {
        "check_id": "SELF_BIZ_SUCCESSION_TAX#1",
        "node_id": "SELF_BIZ_SUCCESSION_TAX",
        "claim": "適用期限は2027-12-31である",
        "verdict": "UNCHANGED",
        "confidence": "MEDIUM",
        "checked_at": "2026-09-15",
        "evidence": [{"url": "https://www.nta.go.jp/fake",
                      "quote": "特例措置の適用期限（令和9年12月31日）は延長されていません"}],
        "human_review_required": False,          # ← モデルの自己申告
    })

    result = ShioriAgent(api_key="dummy_key").run_check(MOCK_CHECK, MOCK_NODE, TODAY)
    assert result.verdict == "UNCHANGED"          # UNCERTAIN に落ちていない
    assert result.human_review_required is True


@patch("google.genai.Client")
@patch("src.agent.agent.search_public_sites")
@patch("src.agent.agent.fetch_page_text")
def test_rejected_reasons_are_not_thrown_away(
        mock_fetch, mock_search, mock_client_cls, monkeypatch):
    """関門が落とした理由が結果に残ること。捨てると、何が落ちたか分からない。"""
    monkeypatch.setenv("GEMINI_API_KEY", "dummy_key")
    mock_search.return_value = [{"title": "公的情報", "url": "https://www.nta.go.jp/fake"}]
    mock_fetch.return_value = "取得できた本文です"

    _mock_gemini(mock_client_cls, {
        "check_id": "SELF_BIZ_SUCCESSION_TAX#1",
        "node_id": "SELF_BIZ_SUCCESSION_TAX",
        "claim": "適用期限は2027-12-31である",
        "verdict": "CHANGED",
        "confidence": "HIGH",
        "checked_at": "2026-09-15",
        # 取得できていない URL を根拠に出してくる
        "evidence": [{"url": "https://www.mof.go.jp/not-fetched", "quote": "延長されました"}],
        "proposed_patch": {"json_pointer": "/deadline/absolute_date",
                           "current": "2027-12-31", "proposed": "2029-12-31"},
        "human_review_required": True,
    })

    result = ShioriAgent(api_key="dummy_key").run_check(MOCK_CHECK, MOCK_NODE, TODAY)
    assert result.verdict == "UNCERTAIN"          # 根拠が残らないので断定できない
    assert result.proposed_patch is None
    assert any("ページを取れていない" in r for r in result.rejected)
    assert any("根拠が残らないので" in r for r in result.rejected)


@patch("google.genai.Client")
@patch("src.agent.agent.search_public_sites")
@patch("src.agent.agent.fetch_page_text")
def test_case_a_patch_current_is_filled_in_by_the_machine(
        mock_fetch, mock_search, mock_client_cls, monkeypatch):
    """
    ケースA。current はノードから機械が入れ直す。
    ここがモデルの自己申告のままだと、人が見る差分そのものが嘘になる。
    """
    monkeypatch.setenv("GEMINI_API_KEY", "dummy_key")
    mock_search.return_value = [{"title": "公的情報", "url": "https://www.nta.go.jp/fake"}]
    mock_fetch.return_value = "適用期限は令和11年12月31日までに延長されました。"

    _mock_gemini(mock_client_cls, {
        "check_id": "SELF_BIZ_SUCCESSION_TAX#1",
        "node_id": "SELF_BIZ_SUCCESSION_TAX",
        "claim": "適用期限は2027-12-31である",
        "verdict": "CHANGED",
        "confidence": "MEDIUM",
        "checked_at": "2026-09-15",
        "evidence": [{"url": "https://www.nta.go.jp/fake",
                      "quote": "適用期限は令和11年12月31日までに延長されました"}],
        "proposed_patch": {"json_pointer": "/deadline/absolute_date",
                           "current": "でたらめな現在値", "proposed": "2029-12-31",
                           "rationale": "延長されたため"},
        "human_review_required": True,
    })

    result = ShioriAgent(api_key="dummy_key").run_check(MOCK_CHECK, MOCK_NODE, TODAY)
    assert result.verdict == "CHANGED"
    assert result.proposed_patch is not None
    assert result.proposed_patch.current == MOCK_NODE["deadline"]["absolute_date"]
    assert result.proposed_patch.proposed == "2029-12-31"
    assert result.evidence[0].quote_verified is True
    assert result.rejected == []
