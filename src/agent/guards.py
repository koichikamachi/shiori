"""
エージェントの出力を、人が見る前に機械が落とす（S07 第4節）

ここには LLM を一切使わない。使うと、嘘を嘘で確かめることになる。
落とした理由は捨てずに返す。何が落ちたか分からないと、直しようがない。
"""
import copy
import unicodedata
from datetime import date
from urllib.parse import urlsplit

from src.agent.schema import FORBIDDEN_STATUS, CheckResult

# 根拠として認めるのは公的機関だけ
OFFICIAL_SUFFIXES = (".go.jp", ".lg.jp")

# 書き換えを許す場所。ここに無い項目は、エージェントからは触れない。
PATCHABLE_PREFIXES = (
    "/deadline/",
    "/irreversibility/warning_fact",
    "/irreversibility/warning_exception",
    "/legal_metadata/verification_status",
    "/legal_metadata/verified_at",
    "/legal_metadata/verified_by",
    "/legal_metadata/source_url",
    "/legal_metadata/source_draft",
    "/today_action",
)


def is_official(url: str) -> bool:
    """go.jp / lg.jp のみ。ここを緩めると、根拠の意味が消える。"""
    if not url.lower().startswith("https://"):
        return False
    try:
        hostname = urlsplit(url).hostname
        if not hostname:
            return False
        host = hostname.lower()
        return host.endswith(OFFICIAL_SUFFIXES)
    except Exception:
        return False


def _norm(text: str) -> str:
    """引用の突き合わせ用。全角半角と空白のゆれだけを均す。"""
    t = unicodedata.normalize("NFKC", text)
    return "".join(t.split())


def quote_appears(quote: str, page_text: str) -> bool:
    """取得ページに、その文字列が実際にあるか。要約は通さない。"""
    q = _norm(quote)
    if len(q) < 10:          # 短すぎる引用は、偶然一致するので根拠にしない
        return False
    return q in _norm(page_text)


def resolve_pointer(obj, pointer: str):
    """JSON Pointer をたどる。無ければ KeyError。"""
    cur = obj
    for raw in pointer.split("/")[1:]:
        key = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(cur, list):
            cur = cur[int(key)]
        else:
            if not isinstance(cur, dict) or key not in cur:
                raise KeyError(pointer)
            cur = cur[key]
    return cur


def validate_patch(node: dict, patch) -> list[str]:
    """直す案が、当ててよい形かを見る。"""
    errs = []
    ptr = patch.json_pointer

    if not any(ptr == p or ptr.startswith(p) for p in PATCHABLE_PREFIXES):
        errs.append(f"触ってよい場所ではない: {ptr}")
        return errs

    try:
        current = resolve_pointer(node, ptr)
    except (KeyError, IndexError, ValueError):
        errs.append(f"その場所がノードに無い: {ptr}")
        return errs

    if patch.proposed is None:
        errs.append("直す値が空である")
        return errs

    if current is not None and type(current) is not type(patch.proposed):
        errs.append(f"型が変わる: {type(current).__name__} → {type(patch.proposed).__name__}")

    if patch.proposed == current:
        errs.append("いまと同じ値である")

    if ptr.endswith("/verification_status") and patch.proposed == FORBIDDEN_STATUS:
        errs.append(f"{FORBIDDEN_STATUS} へは、人以外が上げられない")

    return errs


def sanitize(result: CheckResult, pages: dict[str, str], node: dict,
             today: date) -> tuple[CheckResult, list[str]]:
    """
    関門をまとめて通す。
    pages … {url: 実際に取得した本文}。取れなかった URL は入っていない。
    戻り値 … (通したあとの答え, 落とした理由)
    """
    r = result.model_copy(deep=True)
    rejected: list[str] = []

    kept = []
    for ev in r.evidence:
        if not is_official(ev.url):
            rejected.append(f"公的機関ではない: {ev.url}")
            continue
        if ev.url not in pages:
            rejected.append(f"ページを取れていない: {ev.url}")
            continue
        if ev.page_date and ev.page_date > today:
            rejected.append(f"ページの日付が未来: {ev.url} {ev.page_date}")
            continue
        if not quote_appears(ev.quote, pages[ev.url]):
            rejected.append(f"引用がページに無い: {ev.url}")
            continue
        ev.quote_verified = True     # 機械が付ける。自己申告は上書きする。
        kept.append(ev)
    r.evidence = kept

    # 根拠が残らなければ、断定はできない
    if not r.evidence and r.verdict in ("CHANGED", "UNCHANGED"):
        rejected.append(f"根拠が残らないので {r.verdict} を名乗れない")
        r.verdict = "UNCERTAIN"

    if r.verdict == "UNCERTAIN":
        r.confidence = "LOW"
    elif len(r.evidence) < 2 and r.confidence == "HIGH":
        r.confidence = "MEDIUM"

    if r.proposed_patch is not None:
        errs = validate_patch(node, r.proposed_patch)
        if errs:
            rejected.extend(errs)
            r.proposed_patch = None
        elif not r.evidence:
            rejected.append("根拠が無いのに直す案が付いている")
            r.proposed_patch = None

    if r.checked_at != today:
        rejected.append(f"確認日が今日でない: {r.checked_at}")
        r.checked_at = today

    r.human_review_required = True
    return r, rejected


def apply_patch(node: dict, patch) -> dict:
    """採用が決まったものだけを当てる。元は壊さない。"""
    out = copy.deepcopy(node)
    parts = [p.replace("~1", "/").replace("~0", "~")
             for p in patch.json_pointer.split("/")[1:]]
    cur = out
    for key in parts[:-1]:
        cur = cur[int(key)] if isinstance(cur, list) else cur[key]
    last = parts[-1]
    if isinstance(cur, list):
        cur[int(last)] = patch.proposed
    else:
        cur[last] = patch.proposed
    return out


def validate_node_id(node_id: str) -> bool:
    """node_id のバリデーション（パストラバーサルや不正フォーマットの防止）を行う"""
    if not isinstance(node_id, str):
        return False
    # 安全な英数字、アンダースコア、ハイフン、シャープのみ許可し、
    # パストラバーサルの危険な文字列（.. や / や \\）を完全に排除する。
    import re
    return bool(re.match(r"^[A-Za-z0-9_\-#]+$", node_id))

