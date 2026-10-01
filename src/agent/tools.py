"""
制度追従エージェント用のツールモジュール（S07 第5節、S13）

公的ドメイン（.go.jp / .lg.jp）厳守、商品名・ベンダー名のハードコード禁止。
WEB検索、HTMLページ取得、テキスト抽出、および提案（Proposal）のJSONエクスポート。

Day 5: EvaluatorRole を追加。複数の公的候補から「一次証憑」を自動選定し、
5形態の曖昧さを検出する Stage 2 を実装。
"""
import os
import re
import json
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path
from datetime import date
from html.parser import HTMLParser
from typing import Optional, List, Tuple, Callable
from dataclasses import dataclass

from src.agent.guards import is_official
from src.agent.schema import CheckResult
from src.config import config


@dataclass
class SearchResult:
    """検索結果を表現するデータクラス"""
    url: str
    title: str
    snippet: str
    credibility_level: str = ""
    icon: str = ""
    score: int = 0


class HTMLTextExtractor(HTMLParser):
    """
    HTML から不要なタグ（script, style, head, noscript, svg, iframe など）を除去し、
    クリーンなプレーンテキストを抽出するためのパーサー。
    """
    VOID_ELEMENTS = {
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr"
    }

    def __init__(self):
        super().__init__()
        self.exclude_tags = {"script", "style", "head", "noscript", "svg", "iframe", "meta", "link"}
        self.tag_stack = []
        self.text_parts = []

    def handle_starttag(self, tag, attrs):
        tag_lower = tag.lower()
        if tag_lower not in self.VOID_ELEMENTS:
            self.tag_stack.append(tag_lower)

    def handle_endtag(self, tag):
        tag_lower = tag.lower()
        # スタックにタグが存在する場合のみ、安全にそこまで巻き戻す
        if tag_lower in self.tag_stack:
            while self.tag_stack:
                t = self.tag_stack.pop()
                if t == tag_lower:
                    break

    def handle_data(self, data):
        # 現在のスタックに除外対象のタグが一切含まれていない場合のみテキストを記録
        if not any(t in self.exclude_tags for t in self.tag_stack):
            self.text_parts.append(data)

    def get_text(self) -> str:
        raw = "".join(self.text_parts)
        # 空行や前後の無駄な空白を取り除き、綺麗な行に整理
        lines = [line.strip() for line in raw.splitlines()]
        return "\n".join(line for line in lines if line)


def search_public_sites(query: str, num_results: int = 5) -> list[dict]:
    """
    公的サイト（.go.jp, .lg.jp）を対象としたWEB検索を行う。
    Google Custom Search API（GOOGLE_API_KEY / GOOGLE_CSE_ID）を使用する。
    未設定の場合は安全に空リストを返す。
    """
    # 検索対象を公的機関（.go.jp、.lg.jp）に制限する修飾語を強制付加
    restricted_query = f"{query} (site:go.jp OR site:lg.jp)"
    
    api_key = os.environ.get("GOOGLE_API_KEY") or config.GEMINI_API_KEY
    cx = os.environ.get("GOOGLE_CSE_ID")
    
    if not api_key or not cx:
        # APIキーが未設定の場合は、エラーにせず空のリストを返す
        return []
        
    params = {
        "key": api_key,
        "cx": cx,
        "q": restricted_query,
        "num": num_results
    }
    url = "https://www.googleapis.com/customsearch/v1?" + urllib.parse.urlencode(params)
    
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "ShioriAgent/1.0 (Mozilla-compatible)"}
        )
        # セキュアなタイムアウト（10秒）
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
            results = []
            for item in data.get("items", []):
                link = item.get("link", "")
                # 非公的ドメインが混入していた場合はガードにより徹底排除
                if is_official(link):
                    results.append({
                        "title": item.get("title", ""),
                        "url": link,
                        "snippet": item.get("snippet", "")
                    })
            return results
    except Exception:
        # ネットワーク障害やAPIエラーなどの例外は安全にキャッチして空リストを返す
        return []


# ---- 業務文脈と、テーマ外の即死フィルター --------------------------------------
# しおりが扱うのは「相続・終活・生前整理・高齢の親の意思決定支援」だけである。
# 公的ドメイン（go.jp / lg.jp）であっても、この範囲の外の制度は根拠にならない。
BUSINESS_CONTEXT = (
    "このシステムの目的は、相続・終活・生前整理、および高齢の親の意思決定（意向確認）の支援である。"
    "扱う制度は、相続手続・遺言・相続税・贈与・成年後見・死亡に伴う届出や名義変更・年金や保険の死亡時手続などに限る。"
)
EXCLUDED_TOPICS_JA = "子育て支援、保育、児童手当、学校教育、高等教育（大学・高校の学費無償化や奨学金・修学支援）、入試"

# URL・題名・抜粋にこれらが現れる候補は、テーマ外として機械的に落とす（Gemini の判断を待たない）。
OFF_TOPIC_MARKERS = (
    # URL に現れやすい語
    "hutankeigen", "shugakushien", "shougakukin", "kosodate", "hoiku", "jidouteate", "/koutou/",
    # 日本語
    "高等教育", "修学支援", "授業料", "奨学金", "学費", "無償化", "子育て", "保育", "児童手当", "入試",
)


def is_off_topic(candidate: dict) -> bool:
    """業務文脈（相続・終活・高齢の親）の外にある公的ページか。"""
    text = " ".join(str(candidate.get(k, "")) for k in ("url", "title", "snippet")).lower()
    return any(m.lower() in text for m in OFF_TOPIC_MARKERS)


def is_pdf_url(url: str) -> bool:
    """URL が PDF を指しているか（拡張子で見る）。"""
    return urllib.parse.urlsplit(url or "").path.lower().endswith(".pdf")


def source_tier(url: str) -> int:
    """
    根拠としての並び順。大きいほど先に読む。
      3: 国の機関（go.jp）の HTML ページ
      2: 自治体（lg.jp）の HTML ページ ―― 国の制度の解説であることが多いので、国の機関の次
      0: PDF（本文をテキストとして読めない）／公的ドメイン以外
    """
    if not url or not is_official(url):
        return 0
    if is_pdf_url(url):
        return 0
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if host.endswith(".go.jp"):
        return 3
    if host.endswith(".lg.jp"):
        return 2
    return 1


def cse_configured() -> bool:
    """Google Programmable Search（CSE）の鍵と ID が両方そろっているか。"""
    return bool((os.environ.get("GOOGLE_API_KEY") or config.GEMINI_API_KEY)
                and os.environ.get("GOOGLE_CSE_ID"))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """転送先の URL だけを知りたいので、転送には従わない。"""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _resolve_grounding_uri(uri: str, timeout: int = 8) -> str:
    """
    グラウンディングの出典は、Google の転送用 URL で返ってくる。
    本物の URL（転送先）を取り出す。公的ドメインかどうかは、ここで得た本物の URL で判定する。
    """
    if is_official(uri):
        return uri
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(uri, headers={"User-Agent": "ShioriAgent/1.0 (Mozilla-compatible)"})
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.geturl()
    except urllib.error.HTTPError as e:
        return e.headers.get("Location") or ""
    except Exception:
        return ""


def search_with_gemini_grounding(client, query: str, model: str, num_results: int = 5) -> list[dict]:
    """
    Gemini の「Google 検索によるグラウンディング」で公的ページを探す。
    GEMINI_API_KEY だけで動く（Custom Search JSON API は新規受付を終えているため、こちらを主経路にする）。

    - 出典（grounding_chunks）の転送 URL を本物の URL に直し、go.jp / lg.jp だけを残す。
    - API の失敗は握りつぶさず、呼び出し側に例外で知らせる（通信障害と「該当なし」を区別するため）。
    """
    from google.genai import types  # 遅延 import（テストでの差し替えを妨げない）
    prompt = (
        f"前提: {BUSINESS_CONTEXT}\n"
        f"{EXCLUDED_TOPICS_JA} に関するページは、この目的と無関係なので使わないでください。\n"
        "次の事項を確認できる日本の公的機関の公式ページを、Google 検索で探し、要点を短く述べてください。\n"
        "- 国の制度（民法・税法など）に関わる事項は、裁判所（courts.go.jp）・法務省（moj.go.jp）・"
        "国税庁（nta.go.jp）・e-Gov 法令検索（laws.e-gov.go.jp）など、国の機関（go.jp）の HTML ページを優先してください。\n"
        "- 市区町村（lg.jp）のページは、手続の窓口が市区町村である事項のときだけ使ってください。\n"
        "- PDF、民間サイト、まとめ記事は使わないでください。\n"
        f"検索語: {query} site:go.jp OR site:lg.jp"
    )
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())]),
    )
    results, seen = [], set()
    try:
        chunks = response.candidates[0].grounding_metadata.grounding_chunks or []
        chunks = list(chunks)
    except Exception:
        chunks = []
    for ch in chunks[:10]:
        web = getattr(ch, "web", None)
        uri = getattr(web, "uri", None) if web else None
        if not isinstance(uri, str) or not uri:
            continue
        real = _resolve_grounding_uri(uri)
        if not real or real in seen or not is_official(real):
            continue
        seen.add(real)
        title = getattr(web, "title", "") or ""
        results.append({"title": title if isinstance(title, str) else "", "url": real, "snippet": ""})
        if len(results) >= num_results:
            break
    return results


def fetch_page_text(url: str, timeout: int = 10) -> str:
    """
    指定されたURLのWEBページを取得し、タグを除去したプレーンテキストを返す。
    公的ドメイン（.go.jp / .lg.jp）以外のURLは、セキュリティおよび制度確認の信頼性の観点から拒否する。
    """
    if not is_official(url):
        raise ValueError(f"公的ドメインではないため取得を拒否しました: {url}")
        
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "ShioriAgent/1.0 (Mozilla-compatible)"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            html_bytes = response.read()
            
            # レスポンスヘッダからエンコード方式を取得
            encoding = response.headers.get_content_charset()
            if not encoding:
                # ヘッダに指定がない場合、HTMLの冒頭（最初の2048バイト）からmetaタグのcharsetを探索する
                try:
                    meta_scan = html_bytes[:2048].decode("ascii", errors="ignore")
                    match = re.search(r'<meta\s+charset=["\']?([a-zA-Z0-9_-]+)', meta_scan, re.IGNORECASE)
                    if not match:
                        match = re.search(r'content=["\'][^"\']*charset=([a-zA-Z0-9_-]+)', meta_scan, re.IGNORECASE)
                    if match:
                        encoding = match.group(1).lower()
                except Exception:
                    pass
            
            # まだ決定していない場合は cp932 へのフォールバックを行う
            if not encoding:
                encoding = "cp932"
                
            try:
                html_content = html_bytes.decode(encoding, errors="replace")
            except LookupError:
                # 不正なエンコーディング名がメタ等に指定されていた場合も cp932 にフォールバック
                html_content = html_bytes.decode("cp932", errors="replace")
            
            # テキスト抽出
            extractor = HTMLTextExtractor()
            extractor.feed(html_content)
            return extractor.get_text()
    except Exception as e:
        raise RuntimeError(f"公的ページの取得に失敗しました ({url}): {e}")


def export_proposal(result: CheckResult, data_dir: Optional[Path] = None) -> Path:
    """
    CheckResult オブジェクト（提案案）を JSON ファイルとして書き出す。
    保存先: data_dir/proposals/YYYY-MM-DD/<node_id>__<n>.json
    重複しないように <n>（1から始まる連番）を自動採番する。
    """
    base_dir = data_dir if data_dir is not None else config.DATA_DIR
    
    # 日付フォルダ YYYY-MM-DD
    date_str = result.checked_at.isoformat()
    proposal_dir = base_dir / "proposals" / date_str
    proposal_dir.mkdir(parents=True, exist_ok=True)
    
    node_id = result.node_id
    
    # 重複しないファイル名を連番で探索
    seq = 1
    while True:
        file_path = proposal_dir / f"{node_id}__{seq}.json"
        
        # 解決（絶対パス化）して proposals ディレクトリ配下であることを徹底検証（パストラバーサル遮断）
        resolved_proposal_dir = proposal_dir.resolve()
        resolved_file_path = file_path.resolve()
        if not str(resolved_file_path).startswith(str(resolved_proposal_dir)):
            raise ValueError("Path traversal attempt detected")
            
        if not file_path.exists():
            break
        seq += 1
        
    try:
        # Pydantic v2 の serialization 機構で date 型などもJSON互換型にシリアライズしたdictに変換
        data_dict = result.model_dump(mode="json")
        # 日本語が生のUTF-8文字で書き出されるように ensure_ascii=False を指定
        json_str = json.dumps(data_dict, indent=2, ensure_ascii=False)
        
        # 必ず UTF-8 エンコーディングで保存
        file_path.write_text(json_str, encoding="utf-8")
        return file_path
    except Exception as e:
        raise RuntimeError(f"提案ファイルの書き出しに失敗しました ({file_path}): {e}")


def detect_local_jurisdictions(search_results: list[dict]) -> dict:
    """
    検索結果の URL から自治体を判定し、複数の異なる自治体が混在している場合を検出。
    
    例）
    - https://www.city.osaka.lg.jp/... → 大阪市
    - https://www.pref.tokyo.lg.jp/... → 東京都
    - https://www.city.kyoto.lg.jp/... → 京都市
    
    戻り値：
    {
        "has_multiple": bool,  # True if 2+ 異なる自治体が検出
        "jurisdictions": list[str],  # 検出された自治体の一覧
        "confidence": str,  # "HIGH" / "MEDIUM" / "LOW"
        "detail": str  # 判定理由（日本語）
    }
    """
    jurisdictions = set()
    
    for result in search_results:
        url = result.get("url", "")
        if not url:
            continue
            
        # URL から自治体を抽出
        # パターン1: city.XXX.lg.jp → XXX 市
        if ".city." in url:
            match = re.search(r"\.city\.([a-z_]+)\.lg\.jp", url)
            if match:
                jurisdiction = match.group(1).replace("_", "市")
                jurisdictions.add(f"{jurisdiction}市")
        
        # パターン2: pref.XXX.lg.jp → XXX 都道府県
        elif ".pref." in url:
            match = re.search(r"\.pref\.([a-z_]+)\.lg\.jp", url)
            if match:
                pref_name = match.group(1).replace("_", "")
                # 都道府県名（例：tokyo → 東京都）の簡易マッピング
                pref_map = {
                    "tokyo": "東京都", "osaka": "大阪府", "kyoto": "京都府",
                    "hyogo": "兵庫県", "aichi": "愛知県", "saitama": "埼玉県",
                    "kanagawa": "神奈川県", "chiba": "千葉県",
                }
                jurisdiction = pref_map.get(pref_name, f"{pref_name}県")
                jurisdictions.add(jurisdiction)
    
    has_multiple = len(jurisdictions) > 1
    confidence = "HIGH" if len(jurisdictions) >= 2 else "MEDIUM" if len(jurisdictions) == 1 else "LOW"
    
    detail = ""
    if has_multiple:
        detail = f"複数の自治体ローカルルールが検出されました: {', '.join(sorted(jurisdictions))}"
    elif len(jurisdictions) == 1:
        detail = f"単一の自治体ルール（{list(jurisdictions)[0]}）を検出"
    else:
        detail = "自治体情報を自動判定できませんでした"
    
    return {
        "has_multiple": has_multiple,
        "jurisdictions": sorted(list(jurisdictions)),
        "confidence": confidence,
        "detail": detail
    }


class EvaluatorRole:
    """
    Day 5: Evaluator ロール（Stage 2）
    複数の公的候補から「一次証憑」（最も信頼できるソース）を自動選定し、
    5形態の曖昧さを検出する。
    """

    # 信頼度レベルの定義
    CREDIBILITY_LEVELS = {
        "PRIMARY_SOURCE": {"icon": "✓", "score": 4, "jp": "一次資料（官報・公式ページ）"},
        "AUTHORITATIVE_EXPLANATION": {"icon": "◎", "score": 3, "jp": "権威ある解釈（省庁通知・公式ガイド）"},
        "SECONDARY": {"icon": "△", "score": 2, "jp": "二次資料（新聞・専門家解説）"},
        "UNRELIABLE": {"icon": "✗", "score": 1, "jp": "信頼度低（ブログ・個人見解）"},
        # 公的ドメインでも、駅のテーマと中身が合わないもの。スコア 0 で、一次証憑には選ばない。
        "IRRELEVANT": {"icon": "✗", "score": 0, "jp": "テーマと無関係のため除外"},
    }

    # ドメインベースの信頼度推測ルール
    DOMAIN_CREDIBILITY = {
        ".go.jp": ("PRIMARY_SOURCE", "✓", 4),  # 中央省庁
        ".lg.jp": ("PRIMARY_SOURCE", "✓", 4),  # 自治体
        "kanpo.go.jp": ("PRIMARY_SOURCE", "✓", 4),  # 官報
        "nta.go.jp": ("PRIMARY_SOURCE", "✓", 4),  # 国税庁
        "mhlw.go.jp": ("PRIMARY_SOURCE", "✓", 4),  # 厚生労働省
        "news.": ("SECONDARY", "△", 2),  # ニュースサイト
        "blog": ("UNRELIABLE", "✗", 1),  # ブログ
    }

    def __init__(self, gemini_client=None, skip_config: bool = False):
        """
        Args:
            gemini_client: Google Generative AI クライアント（オプション）
            skip_config: テストモード時は config を読まない
        """
        self.client = gemini_client
        self.model = "gemini-3.6-flash"

    def evaluate_candidates(self, candidates: List[dict], claim: str, context: dict = None,
                            on_step: Optional[Callable[[dict, str, List[str]], None]] = None) -> Tuple[dict, dict]:
        """
        複数候補を評価し、最適候補を選定。

        Args:
            candidates: 検索結果のリスト（title, url, snippet を含む dict）
            claim: 検証対象の主張
            context: 追加情報（オプション）

        Returns:
            (selected_candidate, evaluation_details)
            evaluation_details = {
                'detected_ambiguities': List[str],
                'early_warnings': List[str],
                'evaluation_log': List[str]
            }
        """
        if not candidates:
            return None, {
                'detected_ambiguities': [],
                'early_warnings': ['候補が見つかりません'],
                'evaluation_log': []
            }

        best_candidate = None
        best_score = -1
        excluded_urls = []
        detected_ambiguities = []
        early_warnings = []
        evaluation_log = []

        for idx, candidate in enumerate(candidates, 1):
            # (1) 信頼度評価
            cred_level, icon, score = self._evaluate_single_candidate(candidate, claim, context or {})

            # (2) 5 形態の曖昧さ検出
            # テーマ外の候補からは曖昧さを拾わない（無関係な資料の揺れを、駅の曖昧さと取り違えないため）
            ambiguities = self._detect_ambiguities(candidate, claim, context or {}) if score > 0 else []
            detected_ambiguities.extend(ambiguities)

            # (3) 評価ログを記録
            eval_log = f"  [{idx}/{len(candidates)}] {candidate.get('title', '')[:40]}: {icon} {cred_level}"
            evaluation_log.append(eval_log)

            if ambiguities:
                for amb in ambiguities:
                    evaluation_log.append(f"    → {amb}")

            # 候補を一つ評価するたびに外へ知らせる（ドロワーに逐次流すため）
            if on_step:
                try:
                    on_step(candidate, f"{icon} {self.CREDIBILITY_LEVELS.get(cred_level, {}).get('jp', cred_level)}", ambiguities)
                except Exception:
                    pass

            # (4) 関連性ゼロ（テーマ外）は、公的ドメインであっても選定しない
            if score <= 0:
                excluded_urls.append(candidate.get('url', ''))
                continue

            # (5) 最高スコア候補を選定。信頼度が並んだら、国の機関の HTML ページ（source_tier）を優先する
            total = score * 10 + source_tier(candidate.get('url', ''))
            if total > best_score:
                best_candidate = candidate
                best_score = total

        # 選定結果をログに追加
        if best_candidate:
            early_warnings.append(f"✓ 『{best_candidate.get('title', '')[:50]}』を選定")
            if detected_ambiguities:
                early_warnings.append(f"⚡ 検出された曖昧さ：{', '.join(set(detected_ambiguities))}")
        else:
            early_warnings.append("テーマに合う公的資料は見つかりませんでした")

        return best_candidate, {
            'excluded_urls': excluded_urls,
            'detected_ambiguities': list(set(detected_ambiguities)),  # 重複排除
            'early_warnings': early_warnings,
            'evaluation_log': evaluation_log
        }

    def _evaluate_single_candidate(self, candidate: dict, claim: str, context: dict) -> Tuple[str, str, int]:
        """
        単一候補を信頼度評価（Gemini または domain-based）

        Returns:
            (credibility_level, icon, score)
        """
        url = candidate.get('url', '')

        # 即死フィルター：業務文脈の外（高等教育・子育てなど）は、ドメインにかかわらず関連性ゼロ
        if is_off_topic(candidate):
            return ("IRRELEVANT", "✗", 0)

        # Gemini が利用可能な場合は Gemini で評価
        if self.client:
            try:
                result = self._evaluate_with_gemini(candidate, claim, context)
                if result:
                    return result
            except Exception:
                pass

        # フォールバック：domain-based 評価
        return self._infer_credibility_from_domain(url)

    def _evaluate_with_gemini(self, candidate: dict, claim: str, context: dict) -> Optional[Tuple[str, str, int]]:
        """
        Gemini を使用して候補を評価（実装は google.genai クライアント対応）
        """
        try:
            title = candidate.get('title', '')
            snippet = candidate.get('snippet', '')
            url = candidate.get('url', '')

            node = (context or {}).get("node") or {}
            theme = " / ".join(x for x in (node.get("title", ""), node.get("today_action", "")) if x)
            prompt = (
                f"前提: {BUSINESS_CONTEXT}\n\n"
                f"以下の公的情報源を評価してください。\n"
                f"URL: {url}\n"
                f"タイトル: {title}\n"
                f"スニペット: {snippet}\n"
                f"駅のテーマ: {theme}\n"
                f"検証対象の主張: {claim}\n\n"
                f"まず関連性を審査してください。ドメインが .go.jp / .lg.jp であっても、"
                f"URL・タイトル・スニペットから見て、駅のテーマ（相続・終活・高齢の親の意向確認など）と"
                f"中身が合致していなければ、IRRELEVANT とだけ返してください。"
                f"{EXCLUDED_TOPICS_JA} に関するページは、必ず IRRELEVANT です。\n"
                f"関連性がある場合に限り、信頼度を次から選んでください：\n"
                f"- PRIMARY_SOURCE: 官報、公式ページ（✓、スコア4）\n"
                f"- AUTHORITATIVE_EXPLANATION: 政府省庁の解釈通知、公式ガイドライン（◎、スコア3）\n"
                f"- SECONDARY: 新聞記事、専門家解説（△、スコア2）\n"
                f"- UNRELIABLE: ブログ、個人見解（✗、スコア1）\n\n"
                f"1語で信頼度レベルのみを返してください（解説は不要）。"
            )

            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt
            )

            level_text = response.text.strip().upper()
            if "IRRELEVANT" in level_text:
                return ("IRRELEVANT", "✗", 0)

            # Gemini の応答を信頼度レベルにマッピング
            for level_name, level_info in self.CREDIBILITY_LEVELS.items():
                if level_name in level_text or level_name[:4] in level_text:
                    return (level_name, level_info["icon"], level_info["score"])

            # マッピング失敗時はフォールバック
            return None
        except Exception:
            return None

    def _detect_ambiguities(self, candidate: dict, claim: str, context: dict) -> List[str]:
        """
        5形態の曖昧さをキーワード検出

        Returns:
            List[str]: 検出された曖昧さのリスト（FORM_N: 形態名）
        """
        ambiguities = []
        text = (candidate.get('title', '') + " " + candidate.get('snippet', '')).lower()

        # FORM_1: 複数情報源の存在
        if any(kw in text for kw in ["複数", "異なる", "それぞれ", "複数の"]):
            ambiguities.append("FORM_1: 複数情報源の矛盾・解釈の乖離")

        # FORM_2: 時点軸による変化
        if any(kw in text for kw in ["変更", "改正", "新規", "廃止", "施行", "2024", "2025"]):
            ambiguities.append("FORM_2: 時点軸による変化")

        # FORM_3: 解釈幅の広さ
        if any(kw in text for kw in ["ただし", "例外", "または", "場合によって", "可能性"]):
            ambiguities.append("FORM_3: 解釈幅の広さ")

        # FORM_4: 例外・特例の存在
        if any(kw in text for kw in ["特例", "除く", "対象外", "例外", "特措法"]):
            ambiguities.append("FORM_4: 例外・特例の存在")

        # FORM_5: 情報の鮮度矛盾
        if any(kw in text for kw in ["旧", "従来", "廃止予定", "従前", "以前", "廃止"]):
            ambiguities.append("FORM_5: 情報の鮮度矛盾")

        return ambiguities

    def _infer_credibility_from_domain(self, url: str) -> Tuple[str, str, int]:
        """
        URL のドメインから信頼度を推測（フォールバック）

        Returns:
            (credibility_level, icon, score)
        """
        if not url:
            return ("UNRELIABLE", "✗", 1)

        url_lower = url.lower()

        # 特定ドメインの優先チェック
        for domain, (level, icon, score) in self.DOMAIN_CREDIBILITY.items():
            if domain in url_lower:
                return (level, icon, score)

        # デフォルト：信頼度低
        return ("UNRELIABLE", "✗", 1)
