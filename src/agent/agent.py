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
from src.agent.guards import PATCHABLE_PREFIXES, is_official, sanitize
from src.agent.tools import search_public_sites, fetch_page_text, export_proposal, detect_local_jurisdictions
from src.config import config

# 使うモデルは一箇所で決める。
# gemini-2.5-flash は新規利用に対して 404（no longer available to new users）を返すため使えない。
GEMINI_MODEL = "gemini-3.6-flash"

# Gemini には書かせない項目。機械（関門）が付けるものを、モデルの自己申告で埋めさせない。
_NOT_FOR_MODEL = frozenset({"rejected"})


def gemini_schema(model: type) -> dict:
    """
    pydantic の JSON Schema を、Gemini の response_schema が受け付ける形に直す。

    なぜ必要か。`model_config = {"extra": "forbid"}` は additionalProperties を生むが、
    Gemini API はこのフィールドを知らないため、リクエスト全体が
    400 INVALID_ARGUMENT（Unknown name "additional_properties"）で弾かれる。
    forbid はバリデーション側に残したいので、送る側だけを削る。

    あわせて、
      ・$ref / $defs を展開する（Gemini は参照を解決しない）
      ・Optional[X] の anyOf[X, null] を nullable: true に畳む
      ・type を大文字にする（プロトコルの enum 名に合わせる）
      ・_NOT_FOR_MODEL の項目を落とす
    """
    src = model.model_json_schema()
    defs = src.get("$defs", {})

    def conv(s):
        if not isinstance(s, dict):
            return s
        if "$ref" in s:
            return conv(defs[s["$ref"].rsplit("/", 1)[-1]])
        if "anyOf" in s:
            # Optional[X] は anyOf[X, null] で来る。X を取り出して nullable を立てる。
            alts = [a for a in s["anyOf"] if a.get("type") != "null"]
            out = conv(alts[0]) if alts else {}
            if len(alts) != len(s["anyOf"]):
                out["nullable"] = True
            return out

        out = {}
        for k, v in s.items():
            if k in ("additionalProperties", "$defs", "title", "default"):
                continue
            elif k == "properties":
                out[k] = {pk: conv(pv) for pk, pv in v.items()
                          if pk not in _NOT_FOR_MODEL}
            elif k == "required":
                out[k] = [r for r in v if r not in _NOT_FOR_MODEL]
            elif k == "items":
                out[k] = conv(v)
            elif k == "type":
                out[k] = v.upper()
            else:
                out[k] = v
        return out

    return conv(src)


class ExplorerRole:
    """
    1. 探す役 (Explorer/Finder)
    ノードの確認事項（needs_check/question等）から公的サイト向けの検索クエリを生成し、
    search_public_sites を呼び出す。
    """
    def __init__(self, client=None):
        self.client = client

    def generate_query_and_search(self, check: dict, node: Optional[dict] = None) -> list[dict]:
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
                    model=GEMINI_MODEL,
                    contents=prompt
                )
                generated = response.text.strip()
                # 異常に長いクエリは拒否してフォールバック
                if generated and len(generated) < 100:
                    query = generated
            except Exception:
                pass

        # 検索実行
        results = search_public_sites(query[:200])

        # 検索そのものが使えないとき（鍵や CSE ID が無い、API 障害、該当なし）は、
        # ノードが持っている一次資料を種にして、そこへ直接あたる。
        if not results:
            results = self.seed_from_node(node)
        return results

    @staticmethod
    def seed_from_node(node: Optional[dict]) -> list[dict]:
        """
        検索に頼らず、ノードに書かれている一次資料の URL を種として返す。

        **人が一次資料として確かめた（SOURCE_VERIFIED）URL だけを種にする。**
        下書き段階の URL を機械が勝手に根拠として読みに行くと、
        「確かめていないものを根拠にした」ことになる。そこは人の手前で止める。
        """
        if not node:
            return []
        lm = node.get("legal_metadata") or {}
        if lm.get("verification_status") != "SOURCE_VERIFIED":
            return []
        url = lm.get("source_url")
        if not url or not is_official(url):
            return []
        return [{
            "title": node.get("title", ""),
            "url": url,
            "snippet": "",
            # 検索ではなくノードの記載から来たことを、後段が見分けられるように残す
            "seeded_from_node": True,
        }]


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
        search_results = explorer.generate_query_and_search(check, node)

        # 1.5 複数自治体ローカルルール検出
        jurisdiction_info = detect_local_jurisdictions(search_results)
        has_multiple_jurisdictions = jurisdiction_info["has_multiple"]

        # 2. 読む役 (Reader) の起動
        reader = ReaderRole()
        pages = reader.fetch_pages(search_results)

        # 複数の自治体ローカルルールが検出された場合は、UNVERIFIED を即時返す
        if has_multiple_jurisdictions:
            return CheckResult(
                check_id=check["check_id"],
                node_id=check["node_id"],
                claim=check["claim"],
                verdict="UNVERIFIED",
                confidence="MEDIUM",
                checked_at=today,
                evidence=[result.get("url") and Evidence(url=result["url"], quote=result.get("snippet", "")) for result in search_results[:3] if result.get("url")],
                proposed_patch=ProposedPatch(
                    json_pointer="/next_actions",
                    current=check.get("claim", ""),
                    proposed="call_local_government_office",
                    rationale=f"この手続きは管轄する自治体によって異なります。検出された自治体: {', '.join(jurisdiction_info['jurisdictions'])}。各自治体の公式サイトでご確認いただくか、該当する市区町村役所へお問い合わせください。"
                ),
                risk_if_wrong="自治体ごとに異なる手続きやルールを無視した場合、書類不備や期限超過につながる可能性があります。",
                escalation=jurisdiction_info["detail"],
                detected_constraints=jurisdiction_info,
                human_review_required=True
            )

        # 公的情報が全く取得できなかった場合は、NOT_FOUND を即時返す
        if not pages:
            # なぜ取れなかったのかを残す。検索が0件だったのか、取得に失敗したのかで直し方が違う。
            if not search_results:
                why = ["検索が0件（GOOGLE_API_KEY / GOOGLE_CSE_ID の設定と、"
                       "ノードの source_url（SOURCE_VERIFIED のみ種にする）を確かめる）"]
            else:
                why = [f"ページを取れていない: {r.get('url')}" for r in search_results[:3]]
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
                rejected=why,
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
                "  - 'UNVERIFIED': 複数の自治体ローカルルール、または確定的な国家レベルの .go.jp が見つからない場合。この場合、detected_constraints に判定理由を記録し、proposed_patch で管轄窓口への電話確認などの提案を含めてください。\n"
                "- evidence の quote には、ページ内から『一字一句そのまま（10文字以上）』一致する日本語テキストを正確に抜き出してください。"
                "一文字でも差分がある場合や要約は、後続のガードによって徹底排除されます。\n"
                "- **verdict が 'CHANGED' の場合は、proposed_patch を必ず付けてください。**"
                "古い記述を見つけただけで直す案を出さないなら、人は何を直せばよいか分かりません。\n"
                "- 修正案（proposed_patch）の json_pointer は、次のいずれかで始まる場所だけを指してください。"
                "ここに無い場所を指した案は、後続のガードで機械的に破棄されます：\n"
                f"  {', '.join(PATCHABLE_PREFIXES)}\n"
                "  さらに、その場所は上記ノードに実在するキーであること、"
                "proposed（修正値）は current（現在値）と同じ型で、かつ現在値と異なる値であることが必要です。"
                "絶対に status を EXPERT_VERIFIED に格上げするパッチを含めないでください。\n"
                "- human_review_required は必ず true にしてください。"
                "この仕組みは案を出すところで止まり、JSON を書き換えるのは人です。false にする道はありません。\n"
                "\n【文体（rationale と risk_if_wrong は、そのまま当事者の画面に出ます）】\n"
                "- rationale（見直しの理由）および risk_if_wrong（誤りの場合のご注意）は、"
                "専門用語で突き放すのではなく、当事者の不安に寄り添う丁寧な『です・ます調』で"
                "分かりやすく記述してください。\n"
                "- 命令や助言（〜しなさい、〜すべき、〜する必要がある）ではなく、"
                "公的制度の客観的事実と因果関係を、丁寧な日本語で説明してください。\n"
                "- これを読むのは、身近な人を亡くした直後の方や、自分の死後の備えを考えている方です。"
                "急かさない、脅かさない、結論を断定しない。事実と、その事実がもたらす結果までを述べ、"
                "どうするかの判断は読む人に残してください。\n"
                "- 出力は以下の Pydantic スキーマ（CheckResult）に準拠した JSON オブジェクトのみを返してください。不要な雑談や解説は一切含めないでください。\n\n"
            )

            # Structured Output の要請
            response = self.client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=gemini_schema(CheckResult),
                ),
            )

            payload = json.loads(response.text)
            # 人以外がここを下げる道は作らない（schema.py の約束）。
            # モデルは false や null を返してくることがあるので、自己申告は機械が上書きする。
            payload["human_review_required"] = True
            # 関門の判定結果は機械が付ける。モデルに書かせない。
            for key in _NOT_FOR_MODEL:
                payload.pop(key, None)

            # Pydanticモデルとしてデコード
            result = CheckResult.model_validate(payload)

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
        # 落とした理由は捨てない。何が落ちたか分からないと、直しようがない。
        sanitized_result.rejected = rejected
        return sanitized_result
