"""
制度追従エージェント本体（S07、S13）

協調型五役構成アーキテクチャ（Explorer, Reader, Comparator, Disruptor, Writer）を実装。
防壁（Guards）によるセキュリティおよび公的ソース厳守の自動検証を通過させる。
"""
import os
import re
import json
from datetime import date
from typing import Optional
from google import genai
from google.genai import types

from src.agent.schema import CheckResult, Evidence, ProposedPatch
from src.agent.guards import sanitize
from src.agent.tools import search_public_sites, fetch_page_text, export_proposal
from src.config import config


class ExplorerRole:
    """
    1. 探す役 (Explorer/Finder)
    ノードの確認事項（needs_check/question等）から公的サイト向けの検索クエリを生成し、
    search_public_sites を呼び出す。
    """
    def __init__(self, client=None):
        self.client = client

    def generate_query_and_search(self, check: dict) -> list[dict]:
        question = check.get("question", "")
        claim = check.get("claim", "")
        
        # デフォルトは質問と主張をそのまま利用
        query = f"{question} {claim}".strip()
        
        if self.client:
            try:
                # LLMを使って適切な公的検索用クエリ（キーワード3語以内）に最適化
                prompt = (
                    "以下の質問と主張に基づいて、日本の省庁や自治体の公的情報を検索するための、"
                    "最適かつ簡潔な日本語の検索キーワード（3語以内、スペース区切り）を出力してください。\n"
                    f"質問: {question}\n主張: {claim}\n"
                    "余計な説明や挨拶は一切含めず、検索キーワードのみを出力してください。"
                )
                response = self.client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=prompt
                )
                generated = response.text.strip()
                # 異常に長いクエリは拒否してフォールバック
                if generated and len(generated) < 100:
                    query = generated
            except Exception:
                pass

        # 検索実行
        return search_public_sites(query[:200])


class ReaderRole:
    """
    2. 読む役 (Reader)
    公的URLから本文を取得（fetch_page_text）。
    """
    def fetch_pages(self, search_results: list[dict]) -> dict[str, str]:
        pages = {}
        # 富栄養化防止のため、最大3件の信頼度の高い公的ページを対象とする
        for item in search_results[:3]:
            url = item.get("url")
            if url:
                try:
                    text = fetch_page_text(url)
                    if text:
                        pages[url] = text
                except Exception:
                    continue
        return pages


class ShioriAgent:
    """
    しおり制度追従エージェント（五役構成アーキテクチャ）
    """
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or config.GEMINI_API_KEY
        self.client = None
        if self.api_key:
            try:
                self.client = genai.Client(api_key=self.api_key)
            except Exception:
                pass

    def run_check(self, check: dict, node: dict, today: date) -> CheckResult:
        """
        指定されたCheck項目を検証し、防壁を通過した提案（CheckResult）を生成して返す。
        """
        # APIキーがない場合はクラッシュさせずに安全に UNCERTAIN でフォールバック
        if not self.client:
            return CheckResult(
                check_id=check["check_id"],
                node_id=check["node_id"],
                claim=check["claim"],
                verdict="UNCERTAIN",
                confidence="LOW",
                checked_at=today,
                evidence=[],
                proposed_patch=None,
                risk_if_wrong="APIキーが設定されていないため、自律検証を実行できませんでした。",
                escalation="APIキーが未設定です。環境変数 GEMINI_API_KEY を設定してください。",
                human_review_required=True
            )

        # 1. 探す役 (Explorer) の起動
        explorer = ExplorerRole(self.client)
        search_results = explorer.generate_query_and_search(check)

        # 2. 読む役 (Reader) の起動
        reader = ReaderRole()
        pages = reader.fetch_pages(search_results)

        # 公的情報が全く取得できなかった場合は、NOT_FOUND を即時返す
        if not pages:
            return CheckResult(
                check_id=check["check_id"],
                node_id=check["node_id"],
                claim=check["claim"],
                verdict="NOT_FOUND",
                confidence="LOW",
                checked_at=today,
                evidence=[],
                proposed_patch=None,
                risk_if_wrong="公的情報が見つからなかった、または取得できませんでした。",
                escalation="指定された条件に合致する公的サイトの情報が見つかりませんでした。",
                human_review_required=True
            )

        # 3, 4, 5. 比べる役、疑う役、書く役の協調処理（Gemini API）
        try:
            # ページテキストを連結してプロンプトを作成
            pages_text = ""
            for url, text in pages.items():
                # 1ページあたり最大5000文字にクリップしてコンテキスト制限とトークンを保護
                pages_text += f"\n--- URL: {url} ---\n{text[:5000]}\n"
                
            prompt = (
                "あなたは制度追従エージェントとして以下の3つの役割（Comparator、Disruptor、Writer）を同時にこなし、"
                "制度案内ノードの主張が最新の公的情報と一致しているかを精密に検証してください。\n\n"
                "【あなたの3つの役割】\n"
                "1. 比べる役 (Comparator): 以下のノードの「質問（question）」および「主張（claim）」と、"
                "取得された「公的情報（Retrieved Public Webpages）」の内容を1文字ずつ丁寧に照合し、"
                "制度の変更、廃止、延長、例外の有無を比較判定してください。\n"
                "2. 疑う役 (Disruptor): 根拠データの鮮度や、特例措置と一般措置の混同（例：事業承継税制における『計画提出期限』と『適用期限』の混同など）を"
                "極めて懐疑的に検証し、確信度（HIGH/MEDIUM/LOW）を冷徹に評価してください。\n"
                "3. 書く役 (Writer): 最終決定を CheckResult のスキーマに落とし込み、JSON形式で出力してください。\n\n"
                "【検証対象の情報】\n"
                f"- 検証ID: {check['check_id']}\n"
                f"- ノードID: {check['node_id']}\n"
                f"- 主張内容: {check['claim']}\n"
                f"- 検証したい質問: {check['question']}\n"
                f"- 本日の日付: {today.isoformat()}\n\n"
                "【取得された公的情報】\n"
                f"{pages_text}\n\n"
                "【出力形式および注意ルール】\n"
                "- 判定（verdict）は以下から選んでください：\n"
                "  - 'UNCHANGED': 公的情報を確認し、主張は正しいままであることが確認できた。\n"
                "  - 'CHANGED': 制度改正などにより、主張や期限を変更・修正する必要がある。\n"
                "  - 'AMBIGUOUS': ページは存在するが、該当事項についての明確な記述が無い。\n"
                "  - 'NOT_FOUND': ページそのもの、または関連情報が見つからない。\n"
                "  - 'UNCERTAIN': 情報が矛盾している、または不確かである。\n"
                "- evidence の quote には、ページ内から『一字一句そのまま（10文字以上）』一致する日本語テキストを正確に抜き出してください。"
                "一文字でも差分がある場合や要約は、後続のガードによって徹底排除されます。\n"
                "- 修正案（proposed_patch）を提示する場合、json_pointer はノードに存在する適切なキー"
                "（例：'/deadline/absolute_date' や '/legal_metadata/source_draft'）を指し、"
                "proposed（修正値）は current（現在値）と同じ型にしてください。絶対に status を EXPERT_VERIFIED に格上げするパッチを含めないでください。\n"
                "- 出力は以下の Pydantic スキーマ（CheckResult）に準拠した JSON オブジェクトのみを返してください。不要な雑談や解説は一切含めないでください。\n\n"
            )

            # Structured Output の要請
            response = self.client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=CheckResult,
                ),
            )
            
            # Pydanticモデルとしてデコード
            result = CheckResult.model_validate_json(response.text)
            
        except Exception as e:
            # 万が一のパースエラーやAPIエラー時の安全なフォールバック
            return CheckResult(
                check_id=check["check_id"],
                node_id=check["node_id"],
                claim=check["claim"],
                verdict="UNCERTAIN",
                confidence="LOW",
                checked_at=today,
                evidence=[],
                proposed_patch=None,
                risk_if_wrong="LLM推論またはJSON構造化処理中にエラーが発生しました。",
                escalation=f"エージェント実行エラー: {str(e)}",
                human_review_required=True
            )

        # 6. 関門（guards.py）による機械的バリデーションの適用
        sanitized_result, rejected = sanitize(result, pages, node, today)
        return sanitized_result
