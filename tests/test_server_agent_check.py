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


# ---- 生成文の言葉づかい --------------------------------------------------
# プロンプトで過剰敬語を禁じているが、言葉づかいは揺れる。
# 「〜可能性がございます」のような接客口調が混じると、公的案内の品格が落ちる。
# 人が読む手前で機械が均す。ここはその最後の一段である。

def test_over_polite_wording_is_flattened_before_it_reaches_the_reader():
    from server import _tidy

    assert _tidy("混乱を生じさせる可能性がございます。") == "混乱を生じさせる可能性があります。"
    assert _tidy("こちらが最新の期限でございます。") == "こちらが最新の期限です。"
    assert _tidy("該当する記載はございません。") == "該当する記載はありません。"
    assert _tidy("延長の措置がございます。") == "延長の措置があります。"
    assert _tidy("法務省が示しております。") == "法務省が示しています。"
    assert _tidy("修正をご提案いたします。") == "修正をご提案します。"
    assert _tidy("修正をご提案致します。") == "修正をご提案します。"

    # 端正な言い方は、そのまま通す（直しすぎない）
    for kept in ("令和9年3月31日までと定められています。",
                 "混乱を招く可能性があります。",
                 "相続登記の申請義務が始まりました。"):
        assert _tidy(kept) == kept

    # 文字列でないものは触らない
    assert _tidy(None) is None
    assert _tidy(3) == 3


def test_the_tidy_filter_never_touches_the_verified_quote_or_the_patch_value():
    """
    引用は「取得ページに一字一句あった」ことを機械が確かめた文字列である。
    ここを整えると、検証そのものが嘘になる。
    パッチの値も文章ではないので触らない。
    """
    from server import _tidy_details

    details = {
        "risk_if_wrong": "混乱が生じる可能性がございます。",
        "escalation": "専門家の確認が要る箇所でございます。",
        "evidence": [{"url": "https://www.moj.go.jp/x.html",
                      "quote": "こちらに記載がございます",      # ← ページの原文
                      "quote_verified": True}],
        "proposed_patch": {"json_pointer": "/deadline/note",
                           "current": "旧い記載でございます",
                           "proposed": "新しい記載でございます",
                           "rationale": "公式ページに定めがございます。"},
    }
    out = _tidy_details(details)

    # 説明の文は直る
    assert out["risk_if_wrong"] == "混乱が生じる可能性があります。"
    assert out["escalation"] == "専門家の確認が要る箇所です。"
    assert out["proposed_patch"]["rationale"] == "公式ページに定めがあります。"

    # 引用とパッチの値は、一字も変わらない
    assert out["evidence"][0]["quote"] == "こちらに記載がございます"
    assert out["proposed_patch"]["current"] == "旧い記載でございます"
    assert out["proposed_patch"]["proposed"] == "新しい記載でございます"


def test_the_tidy_filter_survives_a_result_without_a_patch():
    """パッチが無い回答（UNCHANGED など）でも落ちないこと。"""
    from server import _tidy_details

    out = _tidy_details({"risk_if_wrong": "影響はございません。", "proposed_patch": None})
    assert out["risk_if_wrong"] == "影響はありません。"
    assert out["proposed_patch"] is None
    assert _tidy_details({}) == {}


@patch("src.agent.agent.ShioriAgent.run_check")
def test_the_api_response_is_already_tidied(mock_run_check, monkeypatch):
    """
    整形はヘルパーの中だけでなく、**エンドポイントの返答**で効いていること。
    ドロワーはここを読んで描くので、ここを通らなければ意味がない。
    """
    monkeypatch.setenv("GEMINI_API_KEY", "test_gemini_api_key_12345")
    from src.agent.schema import Evidence, ProposedPatch

    mock_run_check.return_value = CheckResult(
        check_id="SURV_REAL_ESTATE_REGISTRATION#1",
        node_id="SURV_REAL_ESTATE_REGISTRATION",
        claim="不動産の名義変更",
        verdict="CHANGED",
        confidence="MEDIUM",
        checked_at="2026-09-20",
        evidence=[Evidence(url="https://www.moj.go.jp/x.html",
                           quote="こちらに記載がございます", quote_verified=True)],
        proposed_patch=ProposedPatch(json_pointer="/deadline/note",
                                     current="旧", proposed="新",
                                     rationale="公式ページに定めがございます。"),
        risk_if_wrong="混乱が生じる可能性がございます。",
        escalation="専門家の確認が要る箇所でございます。",
        human_review_required=True,
    )

    d = client.post("/api/agent/check",
                    json={"node_id": "SURV_REAL_ESTATE_REGISTRATION"}).json()["result"]

    assert d["summary"] == "混乱が生じる可能性があります。"
    assert d["details"]["risk_if_wrong"] == "混乱が生じる可能性があります。"
    assert d["details"]["escalation"] == "専門家の確認が要る箇所です。"
    assert d["details"]["proposed_patch"]["rationale"] == "公式ページに定めがあります。"
    # 検証済みの引用は、返答でも一字一句そのまま
    assert d["details"]["evidence"][0]["quote"] == "こちらに記載がございます"
