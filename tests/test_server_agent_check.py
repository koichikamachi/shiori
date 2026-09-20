"""
/api/agent/check エンドポイントの単体テスト

不正なノードIDに対する拒絶、存在しないノードIDへのエラー、
およびAPIキー有無に応じた正常系（DRY_RUN / Mock Live Run）の検証を行います。
"""
import sys
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
from server import app
from src.agent.schema import CheckResult
from src.config import config

client = TestClient(app)


def test_api_agent_check_invalid_node_id_format():
    """パストラバーサルや不正なフォーマットのnode_idが適切に400 Bad Requestで拒絶されることを検証。"""
    # ディレクトリトラバーサルの疑いがあるパターン
    bad_ids = [
        "../nodes_self",
        "../../etc/passwd",
        "SELF_BANK_LIST/..",
        "SELF_BANK_LIST\\",
        "SELF BANK LIST",  # スペース混入
        "SELF_BANK_LIST;--",  # インジェクション風味
        "SELF_BANK_LIST$",
    ]
    for bad_id in bad_ids:
        response = client.post("/api/agent/check", json={"node_id": bad_id})
        assert response.status_code == 400
        data = response.json()
        assert data["ok"] is False
        assert "Invalid node_id format" in data["error"]


def test_api_agent_check_node_not_found():
    """存在しない正しいフォーマットのnode_idに対して404 Not Foundが返ることを検証。"""
    response = client.post("/api/agent/check", json={"node_id": "NON_EXISTENT_NODE_ID"})
    assert response.status_code == 404
    data = response.json()
    assert data["ok"] is False
    assert "Node not found" in data["error"]


def test_api_agent_check_dry_run_when_no_api_key(monkeypatch):
    """APIキーが無い環境下で、安全に静的解析結果を含むDRY_RUNレスポンスが返ることを検証。"""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    # server.py は環境変数が無ければ config（起動時に .env を読んで保持）を見る。
    # 開発機で「鍵が無い状態」を再現するには、そちらも外す必要がある。
    monkeypatch.setattr(config, "GEMINI_API_KEY", None)

    # テスト対象に実在するノードを使用
    response = client.post("/api/agent/check", json={"node_id": "SELF_BANK_LIST"})
    assert response.status_code == 200

    data = response.json()
    assert data["ok"] is True
    assert data["result"]["action"] == "DRY_RUN"
    assert "details" in data["result"]
    assert data["result"]["details"]["node_id"] == "SELF_BANK_LIST"
    assert data["result"]["details"]["title"] == "口座のある金融機関名のメモ"


@patch("src.agent.agent.ShioriAgent.run_check")
def test_api_agent_check_live_run_mock(mock_run_check, monkeypatch):
    """APIキーが存在する際に、ShioriAgentによる自律制度チェックが呼び出され、結果が正しくJSON返答されることを検証。"""
    monkeypatch.setenv("GEMINI_API_KEY", "test_gemini_api_key_12345")

    # モック返答を作成
    mock_check_result = CheckResult(
        check_id="SELF_BANK_LIST#1",
        node_id="SELF_BANK_LIST",
        claim="口座のある金融機関名のメモ",
        verdict="UNCHANGED",
        confidence="HIGH",
        checked_at="2026-09-16",
        evidence=[],
        proposed_patch=None,
        risk_if_wrong="確認漏れによる手続遅延",
        escalation="特になし",
        human_review_required=True
    )
    mock_run_check.return_value = mock_check_result

    response = client.post("/api/agent/check", json={"node_id": "SELF_BANK_LIST"})
    assert response.status_code == 200

    data = response.json()
    assert data["ok"] is True
    assert data["result"]["action"] == "UNCHANGED"
    assert data["result"]["details"]["node_id"] == "SELF_BANK_LIST"

    # エージェントのrun_checkが呼び出されたことを確認
    mock_run_check.assert_called_once()
