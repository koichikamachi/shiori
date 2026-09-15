"""
制度追従エージェント用のツールモジュール（src/agent/tools.py）の単体テスト

ネットワークアクセスを完全に遮断し、モックを用いてすべての挙動を検証。
"""
import os
import json
import urllib.request
import urllib.error
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.agent.schema import CheckResult, Evidence, ProposedPatch
from src.agent.tools import (
    HTMLTextExtractor,
    fetch_page_text,
    search_public_sites,
    export_proposal,
)


def test_html_text_extractor():
    """HTMLTextExtractor が不要なタグを除去しプレーンテキストを抽出できることを検証。"""
    html = """
    <!doctype html>
    <html>
      <head>
        <title>テスト制度</title>
        <style>body { font-size: 14px; }</style>
        <meta charset="utf-8">
      </head>
      <body>
        <h1>制度案内</h1>
        <!-- コメント -->
        <script>
          const key = "value";
          console.log(key);
        </script>
        <div class="content">
          <p>これは、公的制度の解説文です。</p>
          <noscript>JavaScriptを有効にしてください</noscript>
          <svg><path d="M10 10" /></svg>
          <iframe>広告</iframe>
        </div>
        <footer>
          <span>© 2026 政府機関</span>
        </footer>
      </body>
    </html>
    """
    extractor = HTMLTextExtractor()
    extractor.feed(html)
    text = extractor.get_text()

    # 含まれるべき文字列
    assert "制度案内" in text
    assert "これは、公的制度の解説文です。" in text
    assert "© 2026 政府機関" in text

    # 排除されるべき文字列（head, script, style, noscript, svg, iframe などの内容）
    assert "テスト制度" not in text
    assert "font-size" not in text
    assert "console.log" not in text
    assert "JavaScriptを有効にしてください" not in text
    assert "M10 10" not in text
    assert "広告" not in text


@patch("urllib.request.urlopen")
def test_fetch_page_text_success(mock_urlopen):
    """公的ドメインのURLを指定し、正常にテキストが取得・抽出できることを検証。"""
    # レスポンスのモック作成
    mock_response = MagicMock()
    mock_response.read.return_value = "<html><body><h1>公的サイトのテキスト</h1></body></html>".encode("utf-8")
    mock_response.headers.get_content_charset.return_value = "utf-8"
    mock_urlopen.return_value.__enter__.return_value = mock_response

    official_url = "https://www.mhlw.go.jp/index.html"
    text = fetch_page_text(official_url)

    assert text == "公的サイトのテキスト"
    mock_urlopen.assert_called_once()


def test_fetch_page_text_non_official():
    """非公的ドメイン（例: example.com, google.com）は guards により取得拒否されることを検証。"""
    non_official_url = "https://example.com/news"
    with pytest.raises(ValueError, match="公的ドメインではないため取得を拒否しました"):
        fetch_page_text(non_official_url)


@patch("urllib.request.urlopen")
def test_fetch_page_text_error(mock_urlopen):
    """通信エラーや404などの例外発生時に RuntimeError を送出することを検証。"""
    # 接続エラーを模倣
    mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

    official_url = "https://www.mhlw.go.jp/notfound"
    with pytest.raises(RuntimeError, match="公的ページの取得に失敗しました"):
        fetch_page_text(official_url)


def test_search_public_sites_no_credentials(monkeypatch):
    """APIキーなどの設定が無い場合、安全に空リストを返すことを検証。"""
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_CSE_ID", raising=False)
    
    # config側のキーも一時的に無効化
    with patch("src.agent.tools.config") as mock_config:
        mock_config.GEMINI_API_KEY = None
        results = search_public_sites("相続税")
        assert results == []


@patch("urllib.request.urlopen")
def test_search_public_sites_with_credentials(mock_urlopen, monkeypatch):
    """APIキー設定時、正しいクエリ（ドメイン制限付き）でGoogle検索APIを叩き、公的ドメインのみ結果として残ることを検証。"""
    monkeypatch.setenv("GOOGLE_API_KEY", "test_api_key")
    monkeypatch.setenv("GOOGLE_CSE_ID", "test_cse_id")

    # モックのAPIレスポンス。1つ目は公的、2つ目は非公的URL
    mock_api_response = {
        "items": [
            {
                "title": "公的サイト1",
                "link": "https://www.nta.go.jp/souzoku",
                "snippet": "相続税の基本情報です"
            },
            {
                "title": "非公的サイト2",
                "link": "https://www.example-advisors.com/souzoku",
                "snippet": "相続の相談はこちらへ"
            }
        ]
    }
    
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps(mock_api_response).encode("utf-8")
    mock_urlopen.return_value.__enter__.return_value = mock_response

    results = search_public_sites("事業承継税制", num_results=3)

    # アサーション
    assert len(results) == 1
    assert results[0]["title"] == "公的サイト1"
    assert results[0]["url"] == "https://www.nta.go.jp/souzoku"
    assert results[0]["snippet"] == "相続税の基本情報です"

    # API呼び出し時のURLに、site制限やキーがパラメータとして含まれていることを検証
    called_url = mock_urlopen.call_args[0][0].full_url
    assert "key=test_api_key" in called_url
    assert "cx=test_cse_id" in called_url
    # クエリ（%20はスペース、%28 %29 は括弧）に site:go.jp が入っていること
    assert "site:go.jp" in urllib.parse.unquote(called_url)


def test_export_proposal(tmp_path):
    """CheckResult 提案が UTF-8 の JSON 形式でインクリメンタルな連番で書き出されることを検証。"""
    # ダミーの CheckResult を作成
    result = CheckResult(
        check_id="TEST_CHECK_1",
        node_id="SELF_BIZ_TAX",
        claim="制度の適用期限は変更されていない",
        verdict="UNCHANGED",
        confidence="HIGH",
        checked_at=date(2026, 9, 15),
        evidence=[
            Evidence(
                url="https://www.chusho.meti.go.jp/shoukei",
                publisher="中小企業庁",
                page_date=date(2026, 4, 1),
                quote="本税制の適用期限は、令和X年まで...",
                quote_verified=True,
            )
        ],
        proposed_patch=ProposedPatch(
            json_pointer="/legal_metadata/verification_status",
            current="SELF_REVIEWED",
            proposed="SOURCE_VERIFIED",
            rationale="中小企業庁の発表資料で生存確認",
        ),
        risk_if_wrong="期限切れの手続きを案内してしまうリスク",
        human_review_required=True,
    )

    # 1回目の書き出し
    file_path_1 = export_proposal(result, data_dir=tmp_path)
    
    # 期待されるパス: <tmp_path>/proposals/2026-09-15/SELF_BIZ_TAX__1.json
    assert file_path_1.name == "SELF_BIZ_TAX__1.json"
    assert file_path_1.parent.name == "2026-09-15"
    assert file_path_1.exists()

    # 中身の検証（日本語が生の文字列で、UTF-8で保存されていること）
    content = file_path_1.read_text(encoding="utf-8")
    assert "中小企業庁" in content
    assert "制度の適用期限は変更されていない" in content
    
    # JSONパース確認
    loaded_data = json.loads(content)
    assert loaded_data["check_id"] == "TEST_CHECK_1"
    assert loaded_data["verdict"] == "UNCHANGED"
    assert loaded_data["evidence"][0]["quote_verified"] is True

    # 2回目の書き出し（重複回避による連番のインクリメント検証）
    file_path_2 = export_proposal(result, data_dir=tmp_path)
    assert file_path_2.name == "SELF_BIZ_TAX__2.json"
    assert file_path_2.exists()
    assert file_path_1 != file_path_2


def test_url_spoofing_prevention():
    """is_official が parser differential による URL 偽装（#@等）を完全に検出し遮断できることを検証。"""
    from src.agent.guards import is_official
    assert not is_official("https://evil.com#@www.nta.go.jp")


def test_path_traversal_prevention(tmp_path):
    """Pydanticのバリデーターおよび export_proposal の両方でパストラバーサル（../../）が防がれることを検証。"""
    from pydantic import ValidationError
    
    # 1. Pydantic バリデーターによる英数字・アンダースコア制限でのブロック
    with pytest.raises(ValidationError):
        CheckResult(
            check_id="TEST#1",
            node_id="../../nodes/evil",  # 攻撃的 node_id
            claim="不正ノード",
            verdict="UNCHANGED",
            checked_at=date(2026, 9, 15),
        )


def test_void_elements_handling():
    """<body>内に閉じタグのない <meta> や <link> が来ても、それ以降の本文テキストが全滅しないことを検証。"""
    html = """
    <html>
      <body>
        <p>前半の段落</p>
        <meta charset="utf-8">
        <link rel="stylesheet" href="style.css">
        <p>後半の段落</p>
      </body>
    </html>
    """
    extractor = HTMLTextExtractor()
    extractor.feed(html)
    text = extractor.get_text()
    assert "前半の段落" in text
    assert "後半の段落" in text


@patch("urllib.request.urlopen")
def test_charset_meta_scanning_and_fallback(mock_urlopen):
    """Content-Type ヘッダに charset が無い場合、meta タグの charset スキャンおよび cp932 へのフォールバックが機能することを検証。"""
    # モックの作成（ヘッダの charset なし、HTML冒頭に cp932 指定）
    mock_response = MagicMock()
    # cp932 でエンコードされた日本語テキスト
    text_cp932 = "こんにちは、公的制度"
    html_content = f'<html><head><meta charset="cp932"></head><body>{text_cp932}</body></html>'
    mock_response.read.return_value = html_content.encode("cp932")
    # charset 情報をヘッダから削除
    mock_response.headers.get_content_charset.return_value = None
    mock_urlopen.return_value.__enter__.return_value = mock_response

    official_url = "https://www.mhlw.go.jp/test.html"
    res_text = fetch_page_text(official_url)
    assert "こんにちは、公的制度" in res_text

    # メタタグもなく、ヘッダもない場合の cp932 フォールバック検証
    mock_response_fallback = MagicMock()
    # CP932 でデコード可能
    html_content_fallback = f'<html><body>{text_cp932}</body></html>'
    mock_response_fallback.read.return_value = html_content_fallback.encode("cp932")
    mock_response_fallback.headers.get_content_charset.return_value = None
    mock_urlopen.return_value.__enter__.return_value = mock_response_fallback

    res_text_fallback = fetch_page_text(official_url)
    assert "こんにちは、公的制度" in res_text_fallback
