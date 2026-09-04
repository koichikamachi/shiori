"""
鍵が入り込んでいないかを、機械で見る。

このプロジェクトは GitHub に公開する。**一度でも鍵を commit すると、
消しても履歴に残る。** 消したつもりで残るのが、いちばん危ない。

だから、人が気をつけるのではなく、テストで落とす。
`pytest` を回すたびに見るので、commit の前に必ず一度は通る。

見つけ方は、鍵の「かたち」である。
    Gemini / Google API キー … AIza で始まる39文字
    Anthropic のキー         … sk-ant- で始まる
    OpenAI のキー            … sk- で始まる長い文字列
    サービスアカウントの鍵    … 秘密鍵のかたまり、種別が service アカウントのJSON

パターンそのものを文字列で書くと、このファイル自身が引っかかる。
だから部品に割って組み立てる。
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 見ないところ。作られたものと、外の道具の置き場
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".venv", "node_modules",
             "_ref", "docs/print", "docs/submission"}
SKIP_SUFFIX = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".docx", ".xlsx",
               ".pptx", ".zip", ".ico", ".woff", ".woff2"}

# パターンは部品から組み立てる（このファイル自身が引っかからないように）
PATTERNS = [
    ("Google/Gemini の APIキー", re.compile("AIza" + r"[0-9A-Za-z_\-]{35}")),
    ("Anthropic の APIキー", re.compile("sk-" + "ant-" + r"[0-9A-Za-z_\-]{20,}")),
    ("OpenAI の APIキー", re.compile(r"\bsk-" + r"[0-9A-Za-z]{32,}")),
    ("サービスアカウントの秘密鍵", re.compile("BEGIN " + "PRIVATE KEY")),
    ("サービスアカウントのJSON", re.compile('"type"' + r'\s*:\s*"' + 'service_account"')),
    ("GitHub のトークン", re.compile("gh" + "[pousr]_" + r"[0-9A-Za-z]{36}")),
]


def files_to_check():
    for p in ROOT.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(ROOT).as_posix()
        if any(rel == d or rel.startswith(d + "/") for d in SKIP_DIRS):
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.suffix.lower() in SKIP_SUFFIX:
            continue
        yield p, rel


def test_no_api_key_is_committed():
    """
    鍵の「かたち」をした文字列が、どのファイルにも無いこと。

    引っかかったら、消すだけでは足りない。**すでに commit していれば履歴に残る。**
    その鍵は使えないものとして、発行元で無効にして、作り直すこと。
    """
    found = []
    for p, rel in files_to_check():
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for name, pat in PATTERNS:
            m = pat.search(text)
            if m:
                found.append(f"{rel}：{name}（{m.start()}文字目のあたり）")
    assert not found, (
        "鍵らしきものが入っている。消すだけでは足りない。"
        "commit 済みなら履歴に残るので、その鍵を無効にして作り直すこと。\n  "
        + "\n  ".join(found))


def test_gitignore_keeps_the_usual_key_files_out():
    """鍵を置きがちな名前が、はじめから .gitignore に入っていること。"""
    gi = (ROOT / ".gitignore")
    assert gi.exists(), ".gitignore がない"
    text = gi.read_text(encoding="utf-8")
    for pattern in (".env", "service-account*.json", "credentials*.json",
                    "*.key", "*.pem"):
        assert pattern in text, f".gitignore に {pattern} がない"


def test_no_env_file_is_tracked_by_accident():
    """
    うっかり .env を置いていないか。置いてあること自体は正しい使い方だが、
    リポジトリの中に**追跡されている**状態は危ない。
    ここでは、無視されるべき名前のファイルが実在するかだけを知らせる。
    """
    risky = [p.relative_to(ROOT).as_posix() for p, _ in files_to_check()
             if p.name == ".env" or p.name.endswith(".env")
             or p.name.startswith("service-account")
             or p.name.startswith("credentials")]
    # 実在してもよい（.gitignore で無視されるので）。
    # ただし、実在するなら、それは確かに無視される名前であること。
    gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for r in risky:
        assert ".env" in gi and "credentials*.json" in gi, \
            f"{r} があるのに .gitignore が守っていない"
