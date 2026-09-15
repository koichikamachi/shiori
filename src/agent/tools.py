"""
制度追従エージェント用のツールモジュール（S07 第5節、S13）

公的ドメイン（.go.jp / .lg.jp）厳守、商品名・ベンダー名のハードコード禁止。
WEB検索、HTMLページ取得、テキスト抽出、および提案（Proposal）のJSONエクスポート。
"""
import os
import re
import json
import urllib.request
import urllib.parse
from pathlib import Path
from datetime import date
from html.parser import HTMLParser
from typing import Optional

from src.agent.guards import is_official
from src.agent.schema import CheckResult
from src.config import config


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
