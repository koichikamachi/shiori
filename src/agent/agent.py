"""
制度追従エージェント本体（S07、S13）

協調型五役構成アーキテクチャ（Explorer, Reader, Comparator, Disruptor, Writer）を実装。
防壁（Guards）によるセキュリティおよび公的ソース厳守の自動検証を通過させる。
"""
import os
import re
import json
from datetime import date
from pathlib import Path
from typing import Optional
from google import genai
from google.genai import types

from src.agent.schema import CheckResult, Evidence, ProposedPatch
from src.agent.guards import PATCHABLE_PREFIXES, is_official, sanitize
from src.agent.tools import (search_public_sites, fetch_page_text, export_proposal, detect_local_jurisdictions,
                             search_with_gemini_grounding, cse_configured, EvaluatorRole,
                             is_pdf_url, source_tier, BUSINESS_CONTEXT, EXCLUDED_TOPICS_JA)
from src.config import config

# 使うモデルは一箇所で決める。
# gemini-2.5-flash は新規利用に対して 404（no longer available to new users）を返すため使えない。
GEMINI_MODEL = "gemini-3.6-flash"

# ---- Writer の文体ルール（外部ファイル） ----------------------------------------
# 文体は src/agent/prompts/writer_style.md に置き、照合のたびに読み込む。
# 言い回しの調整でコードを触らずに済むようにするためである。
# ファイルが無い・読めない・空のときは、下の既定の文体で動く（照合は止めない）。
WRITER_STYLE_PATH = Path(__file__).resolve().parent / "prompts" / "writer_style.md"
_WRITER_STYLE_MAX_CHARS = 12000   # 誤って巨大なファイルを置いても、指示が溢れないように

_DEFAULT_WRITER_STYLE = (
    "【文体（rationale・risk_if_wrong・escalation は、そのまま当事者の画面に出ます）】\n"
    "- 専門用語で突き放すのではなく、当事者の不安に寄り添う、落ち着いた自然な『です・ます調』で書いてください。\n"
    "- 命令や助言（〜しなさい、〜すべき）ではなく、公的制度の事実と因果関係を説明してください。\n"
    "- 読むのは、身近な人を亡くした直後の方や、自分の死後の備えを考えている方です。"
    "急かさない、脅かさない、結論を断定しない。どうするかの判断は読む人に残してください。\n"
    "- 『〜でございます』『〜いたします』といった過剰な接客敬語は使わないでください。\n"
    "- evidence の quote と proposed_patch の current / proposed には、この文体を当てず、原文・値のまま書いてください。\n"
)


def load_writer_style() -> tuple[str, str]:
    """
    Writer の文体ルールを読む。戻り値は (本文, 出どころの説明)。
    環境変数 SHIORI_WRITER_STYLE でファイルの場所を差し替えられる。
    """
    path = Path(os.environ.get("SHIORI_WRITER_STYLE") or WRITER_STYLE_PATH)
    try:
        text = path.read_text(encoding="utf-8-sig").strip()
    except Exception:
        return _DEFAULT_WRITER_STYLE, "既定の文体（ルールファイルなし）"
    if not text:
        return _DEFAULT_WRITER_STYLE, "既定の文体（ルールファイルが空）"
    return text[:_WRITER_STYLE_MAX_CHARS], f"{path.name}（{len(text):,} 字）"


# Gemini には書かせない項目。機械（関門）が付けるものを、モデルの自己申告で埋めさせない。
# 曖昧さ検出と早期警告も Evaluator（機械）が付ける。
_NOT_FOR_MODEL = frozenset({"rejected", "detected_ambiguities", "early_warnings"})


class AgentLogger:
    """
    エージェントの内部ステップを記録し、UI（ドロワー）で動的に表示するための
    ログ記録機構。各ロール（Explorer, Reader, Comparator, Disruptor, Writer）の
    実行ステップをタイムスタンプ付きで記録する。
    """
    def __init__(self, sink=None):
        self.logs = []
        # 記録と同時に外へ流す先（サーバーのストリーム）。無ければ記録だけ。
        self.sink = sink

    def log_step(self, role: str, action: str, detail: str = "") -> None:
        """
        ステップを記録。

        Args:
            role: ロール名（"Explorer", "Reader", "Comparator", "Disruptor", "Writer"）
            action: 実行内容（例："🔍 最新情報を検索中...", "📖 ページテキスト取得..."）
            detail: 詳細情報（オプション。エラーメッセージやデバッグ情報）
        """
        from datetime import datetime
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "role": role,
            "action": action,
            "detail": detail
        }
        self.logs.append(log_entry)
        if self.sink:
            try:
                self.sink(log_entry)
            except Exception:
                pass

    def get_logs(self) -> list[dict]:
        """記録されたログの全エントリを返す。"""
        return self.logs.copy()

    def clear(self) -> None:
        """ログをクリア。"""
        self.logs.clear()

    def get_summary(self) -> str:
        """
        ログの要約を返す。UI表示用の簡潔なテキスト形式。
        例：
        🔍 最新情報を検索中...
        📖 ページテキスト取得... (3 pages)
        ⚠️ UNVERIFIED: 複数の自治体ルール検出
        """
        lines = []
        for entry in self.logs:
            lines.append(entry["action"])
            if entry["detail"]:
                lines.append(f"  → {entry['detail']}")
        return "\n".join(lines)


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

    def generate_query_and_search(self, check: dict, node: Optional[dict] = None,
                                  log=None) -> list[dict]:
        """
        検索クエリを作り、公的ページを探す。経路は三段で、上から順に試す。
          1. Google Programmable Search（GOOGLE_API_KEY と GOOGLE_CSE_ID がそろうとき）
          2. Gemini の Google 検索グラウンディング（GEMINI_API_KEY だけで動く）
          3. ノードに書かれた一次資料（人が SOURCE_VERIFIED とした URL のみ）
        どの経路で何件見つかったかを log に残す。通信の失敗は self.errors に積む。
        """
        log = log or (lambda *a, **k: None)
        self.errors: list[str] = []
        self.route = ""
        question = check.get("question", "")
        claim = check.get("claim", "")

        # デフォルトは質問と主張をそのまま利用
        query = f"{question} {claim}".strip()

        if self.client:
            try:
                # LLMを使って適切な公的検索用クエリ（キーワード3語以内）に最適化
                prompt = (
                    f"前提: {BUSINESS_CONTEXT}\n"
                    f"{EXCLUDED_TOPICS_JA} に関する語は、検索キーワードに含めないでください。"
                    "必要なら『相続』『終活』『高齢の親』などの文脈語を一つ含めてください。\n"
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
                    log("Explorer", "🧭 検索クエリを生成", query)
            except Exception as e:
                self.errors.append(f"クエリ生成: {e}")
                log("Explorer", "⚠️ クエリ生成に失敗（質問文をそのまま使います）", str(e)[:80])

        # 1. Programmable Search（設定されていれば）
        if cse_configured():
            log("Explorer", "🔍 Google 検索で公的ドメイン（go.jp / lg.jp）を探索中...")
        results = search_public_sites(query[:200])
        if results:
            self.route = "Google Programmable Search"

        # 2. Gemini × Google 検索（グラウンディング）
        if not results and self.client:
            log("Explorer", "🔍 Gemini × Google 検索で公的ドメイン（go.jp / lg.jp）を探索中...")
            try:
                results = search_with_gemini_grounding(self.client, query[:200], GEMINI_MODEL)
                if results:
                    self.route = "Gemini × Google 検索"
            except Exception as e:
                self.errors.append(f"検索: {e}")
                log("Explorer", "⚠️ 検索に接続できませんでした", str(e)[:80])
                results = []

        # 3. 検索そのものが使えないとき（鍵が無い、API 障害、該当なし）は、
        #    ノードが持っている一次資料を種にして、そこへ直接あたる。
        if not results:
            results = self.seed_from_node(node)
            if results:
                self.route = "ノード記載の一次資料"
                log("Explorer", "📎 ノードに記載された一次資料（人が確認済みの URL）へ直接あたります",
                    results[0]["url"])

        for r in results[:5]:
            host = (r.get("url", "").split("/") + ["", "", ""])[2]
            log("Explorer", f"🌐 {host}", (r.get("title") or "")[:60])
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
    def __init__(self):
        # 接続そのものの失敗（タイムアウト・名前解決など）。404 のような「ページが無い」は含めない。
        self.network_errors: list[str] = []

    def fetch_pages(self, search_results: list[dict], log=None) -> dict[str, str]:
        log = log or (lambda *a, **k: None)
        pages = {}
        # 富栄養化防止のため、読めた公的ページ 3 件までを対象とする（候補は先頭 6 件まで見る）
        for item in search_results[:6]:
            if len(pages) >= 3:
                break
            url = item.get("url")
            # PDF は本文をテキストとして取り出せない（バイナリを文字として読むと、根拠が意味をなさない）
            if url and is_pdf_url(url):
                log("Reader", "⏭️ PDF は読み取り対象外のため飛ばします", url)
                continue
            if url:
                try:
                    text = fetch_page_text(url)
                    if text:
                        pages[url] = text
                        log("Reader", f"📄 本文を取得（{len(text):,} 字）", url)
                except Exception as e:
                    msg = str(e)
                    if "HTTP Error" not in msg:
                        self.network_errors.append(msg)
                    log("Reader", "⚠️ 取得できませんでした", url)
                    continue
        return pages


class ShioriAgent:
    """
    しおり制度追従エージェント（五役構成アーキテクチャ）
    """
    def __init__(self, api_key: Optional[str] = None, on_log=None):
        self.api_key = api_key or config.GEMINI_API_KEY
        self.client = None
        self.logger = AgentLogger(sink=on_log)  # Day 4: ドロワーログ基盤
        # 外部との通信が原因で照合を終えられなかったときの理由。
        # 判定そのものの結論（NOT_FOUND など）と、通信の失敗を取り違えないために分けて持つ。
        self.infra_error: Optional[str] = None
        self.search_route: str = ""
        # 画面の「N件の資料にあたりました」に使う。探索で見つけた公的資料の数と、本文を読めた数。
        self.sources_found: int = 0
        self.pages_read: int = 0
        if self.api_key:
            try:
                self.client = genai.Client(api_key=self.api_key)
            except Exception:
                pass

    def _no_public_standard(self, check: dict, today: date, ev_fields: dict) -> CheckResult:
        """
        有効な一次証憑が一つも選べなかったときの結果。
        家族間の対話や心構えのように、国の画一的な手引きが無い駅はある。
        そこへ無関係な公的ページを当てはめて「整合」と言ってはいけない。引用も出さない。
        """
        return CheckResult(
            check_id=check["check_id"],
            node_id=check["node_id"],
            claim=check["claim"],
            verdict="NOT_FOUND",
            confidence="LOW",
            checked_at=today,
            evidence=[],
            proposed_patch=None,
            risk_if_wrong="",
            escalation=None,
            detected_constraints={"reason": "NO_RELEVANT_PUBLIC_SOURCE"},
            human_review_required=True,
            **ev_fields,
        )

    def run_check(self, check: dict, node: dict, today: date) -> CheckResult:
        """
        指定されたCheck項目を検証し、防壁を通過した提案（CheckResult）を生成して返す。
        """
        # Day 4: ドロワーログをクリアして新規開始
        self.logger.clear()
        self.infra_error = None
        self.search_route = ""
        self.sources_found = 0
        self.pages_read = 0
        self.logger.log_step("System", "✓ 検証開始", f"check_id={check['check_id']}")

        # APIキーがない場合はクラッシュさせずに安全に UNCERTAIN でフォールバック
        if not self.client:
            self.infra_error = "GEMINI_API_KEY が設定されていません"
            self.logger.log_step("System", "✗ APIキー未設定", "UNCERTAIN でフォールバック")
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
        search_results = explorer.generate_query_and_search(check, node, log=self.logger.log_step)
        self.search_route = explorer.route
        self.sources_found = len(search_results)
        self.logger.log_step("Explorer", "✓ 探索完了",
                             f"公的ページ {len(search_results)} 件" + (f"（経路: {explorer.route}）" if explorer.route else ""))

        # 1.2 評価する役 (Evaluator)：候補ごとに信頼度を見て一次証憑を選び、曖昧さを拾う
        evaluation = {"detected_ambiguities": [], "early_warnings": []}
        if search_results:
            self.logger.log_step("Evaluator", "⚖️ 候補を一件ずつ評価し、一次証憑を選定中...")

            def _on_eval(cand, level, ambs):
                host = (cand.get("url", "").split("/") + ["", "", ""])[2]
                self.logger.log_step("Evaluator", f"{level} — {host}", (cand.get("title") or "")[:50])
                for a in ambs:
                    self.logger.log_step("Evaluator", f"⚡ 曖昧さ検出: {a}", host)

            # 国の機関（裁判所・法務省・e-Gov など）→ 自治体 → PDF の順に並べ直してから評価する
            search_results = sorted(search_results, key=lambda r: -source_tier(r.get("url", "")))
            best, evaluation = EvaluatorRole(self.client).evaluate_candidates(
                search_results[:5], check.get("claim", ""), {"node": node}, on_step=_on_eval)
            # テーマ外として除外された候補は、読む役にも渡さない
            excluded = set(evaluation.get("excluded_urls") or [])
            if excluded:
                search_results = [r for r in search_results if r.get("url") not in excluded]
                self.logger.log_step("Evaluator", f"✗ テーマと無関係な公的ページを {len(excluded)} 件除外しました")
            if best:
                # 選ばれた一次証憑を先頭に。読む役は先頭から読む。
                search_results = [best] + [r for r in search_results if r is not best]
                self.logger.log_step("Evaluator", "📌 一次証憑として選定", best.get("url", ""))
            else:
                # 有効な一次証憑がゼロ。無関係な公的ページを当てはめて「整合」を作らない。
                self.logger.log_step("Evaluator", "📭 この駅に直接当てはまる公的基準は見つかりませんでした",
                                     "家族間の対話や個別の状況に基づく手続として扱います")
                return self._no_public_standard(check, today, {
                    "detected_ambiguities": [],
                    "early_warnings": evaluation.get("early_warnings", []),
                })
            if not evaluation.get("detected_ambiguities"):
                self.logger.log_step("Evaluator", "✓ 曖昧さの兆候は見つかりませんでした")
        ev_fields = {
            "detected_ambiguities": evaluation.get("detected_ambiguities", []),
            "early_warnings": evaluation.get("early_warnings", []),
        }

        # 1.5 自治体ページの混入を調べる。
        # **検索結果に複数の自治体のページが並んだことは、「手続が自治体ごとに違う」ことを意味しない。**
        # 国の制度（民法の相続人の順位など）を、各地の役所が自分のサイトで解説しているだけのことが多い。
        # だからここでは結論を出さず、事実として Gemini に渡す。UNVERIFIED にするかどうかは、
        # 読んだページ自体に「市区町村ごとに取扱いが異なる」と書いてあるかで決めさせる。
        jurisdiction_info = detect_local_jurisdictions(search_results)
        lg_hosts = sorted({(r.get("url", "").split("/") + ["", "", ""])[2]
                           for r in search_results if ".lg.jp" in r.get("url", "")})
        if lg_hosts:
            self.logger.log_step("Disruptor", "🏘️ 自治体のページが混じっています（国の制度の解説として参考扱い）",
                                 ", ".join(lg_hosts[:4]))

        # 2. 読む役 (Reader) の起動
        self.logger.log_step("Reader", "📖 公的ページの本文を取得中...")
        reader = ReaderRole()
        pages = reader.fetch_pages(search_results, log=self.logger.log_step)
        self.pages_read = len(pages)
        self.logger.log_step("Reader", "✓ ページ取得完了", f"{len(pages)} ページ")

        # 公的情報が全く取得できなかった場合は、NOT_FOUND を即時返す
        if not pages:
            self.logger.log_step("Reader", "✗ ページ取得失敗", "NOT_FOUND でフォールバック")
            # 通信の失敗で読めなかったのか、読みに行く先が無かったのかを分ける
            net_errors = explorer.errors + reader.network_errors
            if net_errors:
                self.infra_error = "外部への接続に失敗しました: " + net_errors[0][:120]
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
                human_review_required=True,
                **ev_fields
            )

        # 3, 4, 5. 比べる役、疑う役、書く役の協調処理（Gemini API）
        self.logger.log_step("Comparator", "🔄 Comparator: ノードと公的情報を照合中...")
        self.logger.log_step("Disruptor", "🤔 Disruptor: 根拠データを懐疑的に検証中...")
        self.logger.log_step("Writer", "✍️ Writer: CheckResult を生成中...")

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
                f"- 本日の日付: {today.isoformat()}\n"
                + (f"- 参考: 検索結果には自治体のページ（{', '.join(lg_hosts[:4])}）も含まれています。"
                   "自治体のページは、国の制度を各地の役所が解説しているだけのことが多いので、"
                   "それだけを理由に UNVERIFIED にしないでください。\n" if lg_hosts else "")
                + "\n【取得された公的情報】\n"
                f"{pages_text}\n\n"
                "【出力形式および注意ルール】\n"
                f"- 前提: {BUSINESS_CONTEXT}\n"
                "- 取得された公的情報が、この確認事項のテーマ（相続・終活・高齢の親の意向確認など）と"
                "中身の上で関係しない場合は、公的ドメインであっても根拠にしてはいけません。"
                "その場合は verdict を 'NOT_FOUND' とし、evidence は空にしてください。\n"
                "- 判定（verdict）は以下から選んでください：\n"
                "  - 'UNCHANGED': 公的情報を確認し、主張は正しいままであることが確認できた。\n"
                "  - 'CHANGED': 制度改正などにより、主張や期限を変更・修正する必要がある。\n"
                "  - 'AMBIGUOUS': ページは存在するが、該当事項についての明確な記述が無い。\n"
                "  - 'NOT_FOUND': ページそのもの、または関連情報が見つからない。\n"
                "  - 'UNCERTAIN': 情報が矛盾している、または不確かである。\n"
                "  - 'UNVERIFIED': 取得したページ自体に『取扱いが市区町村ごとに異なる』旨が書かれている場合に限る。"
                "この場合、detected_constraints に判定理由を記録し、proposed_patch は付けず、escalation に確認先（どの窓口に何を聞くか）を書いてください。\n"
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
                "\n【文体】\n"
                "- rationale・risk_if_wrong・escalation は、system instruction の『Writer 文体ルール』に従って書いてください。\n"
                "- evidence の quote は公的ページの原文そのまま、proposed_patch の current / proposed は値そのままとし、"
                "文体ルールを当てないでください（言い換えた引用は後続のガードで破棄されます）。\n"
                "- 出力は以下の Pydantic スキーマ（CheckResult）に準拠した JSON オブジェクトのみを返してください。不要な雑談や解説は一切含めないでください。\n\n"
            )

            # 文体ルールを外部ファイルから読み、system instruction として渡す
            style_text, style_src = load_writer_style()
            self.logger.log_step("Writer", "🖋️ 文体ルールを読み込み", style_src)

            # Structured Output の要請
            try:
                response = self.client.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction="# Writer 文体ルール\n" + style_text if not style_text.startswith("#") else style_text,
                        response_mime_type="application/json",
                        response_schema=gemini_schema(CheckResult),
                    ),
                )
            except Exception as api_err:
                # 通信・API 側の失敗。判定の結論ではない。
                self.infra_error = f"Gemini の呼び出しに失敗しました: {str(api_err)[:120]}"
                raise

            payload = json.loads(response.text)
            # 人以外がここを下げる道は作らない（schema.py の約束）。
            # モデルは false や null を返してくることがあるので、自己申告は機械が上書きする。
            payload["human_review_required"] = True
            # 関門の判定結果は機械が付ける。モデルに書かせない。
            for key in _NOT_FOR_MODEL:
                payload.pop(key, None)

            # Pydanticモデルとしてデコード
            result = CheckResult.model_validate(payload)
            # Evaluator の検出結果は機械が付ける
            result.detected_ambiguities = ev_fields["detected_ambiguities"]
            result.early_warnings = ev_fields["early_warnings"]

            # Day 4: 判定結果をログに記録
            self.logger.log_step("Writer", f"✓ 判定完了: {result.verdict}",
                                f"確信度={result.confidence}")

        except Exception as e:
            # 万が一のパースエラーやAPIエラー時の安全なフォールバック
            self.logger.log_step("System", "✗ 処理エラー", f"UNCERTAIN でフォールバック: {str(e)[:50]}")
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
                human_review_required=True,
                **ev_fields
            )

        # 6. 関門（guards.py）による機械的バリデーションの適用
        self.logger.log_step("Guards", "🛡️ セキュリティ関門によるバリデーション中...")
        sanitized_result, rejected = sanitize(result, pages, node, today)
        # 落とした理由は捨てない。何が落ちたか分からないと、直しようがない。
        sanitized_result.rejected = rejected

        # Day 4: 最終結果をログに記録
        if rejected:
            self.logger.log_step("Guards", f"⚠️ {len(rejected)} 項目が検証で落選",
                                f"rejected={', '.join(rejected[:3])}")
        else:
            self.logger.log_step("System", "✓ 検証完了", f"verdict={sanitized_result.verdict}")

        return sanitized_result
