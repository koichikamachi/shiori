"""
制度追従エージェントの関門（S07 第4節・第6節）

APIの鍵がなくても回る。関門はすべて Python だからである。
ここが本体であって、LLM の出来はここの手前の話にすぎない。
"""
import json
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.agent.guards import (apply_patch, is_official, quote_appears,
                              resolve_pointer, sanitize, validate_patch)
from src.agent.schema import CheckResult, Evidence, ProposedPatch

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 9, 2)

URL = "https://www.chusho.meti.go.jp/zaimu/shoukei/shoukei_enkatsu_zouyo_souzoku.html"
PAGE = (
    "法人版事業承継税制（特例措置）について。"
    "特例承継計画の提出期限は令和9年9月30日までに延長されました。"
    "なお、特例措置の適用期限（令和9年12月31日）は延長されていません。"
)


@pytest.fixture(scope="module")
def node():
    doc = json.loads((ROOT / "data" / "nodes" / "nodes_self.json").read_text(encoding="utf-8"))
    return next(n for n in doc["nodes"] if n["id"] == "SELF_BIZ_SUCCESSION_TAX")


def make(verdict="CHANGED", evidence=None, patch=None, confidence="HIGH"):
    return CheckResult(
        check_id="SELF_BIZ_SUCCESSION_TAX#1",
        node_id="SELF_BIZ_SUCCESSION_TAX",
        claim="適用期限は2027-12-31である",
        verdict=verdict,
        confidence=confidence,
        checked_at=TODAY,
        evidence=evidence or [],
        proposed_patch=patch,
    )


def ev(url=URL, quote="特例措置の適用期限（令和9年12月31日）は延長されていません",
       page_date=date(2026, 4, 1)):
    return Evidence(url=url, publisher="中小企業庁", page_date=page_date, quote=quote)


# ---- 出どころ ----

def test_official_domains_only():
    assert is_official("https://www.nta.go.jp/x.htm")
    assert is_official("https://www.city.shibuya.lg.jp/x.html")
    assert not is_official("https://souzoku-matome.example.com/x")
    assert not is_official("https://www.chusho.meti.go.jp.evil.com/x")
    assert not is_official("http://www.nta.go.jp/x.htm")      # https でないものは通さない


def test_non_official_evidence_is_dropped(node):
    bad = ev(url="https://souzoku-blog.example.com/a")
    out, rej = sanitize(make(evidence=[bad]), {bad.url: PAGE}, node, TODAY)
    assert out.evidence == []
    assert any("公的機関ではない" in r for r in rej)


# ---- 引用の一致 ----

def test_quote_must_appear_verbatim():
    assert quote_appears("適用期限（令和9年12月31日）は延長されていません", PAGE)
    # 全角半角と空白のゆれは許す
    assert quote_appears("適用期限(令和9年12月31日)は 延長されていません", PAGE)
    # もっともらしい要約は通さない
    assert not quote_appears("適用期限も併せて延長されました", PAGE)
    # 短すぎる引用は偶然当たるので通さない
    assert not quote_appears("延長", PAGE)


def test_hallucinated_quote_is_dropped(node):
    fake = ev(quote="適用期限は令和11年12月31日まで延長されました")
    out, rej = sanitize(make(evidence=[fake]), {URL: PAGE}, node, TODAY)
    assert out.evidence == []
    assert any("引用がページに無い" in r for r in rej)


def test_evidence_from_unfetched_page_is_dropped(node):
    out, rej = sanitize(make(evidence=[ev()]), {}, node, TODAY)
    assert out.evidence == []
    assert any("ページを取れていない" in r for r in rej)


def test_future_page_date_is_dropped(node):
    out, rej = sanitize(make(evidence=[ev(page_date=date(2027, 1, 1))]),
                        {URL: PAGE}, node, TODAY)
    assert out.evidence == []
    assert any("未来" in r for r in rej)


def test_quote_verified_is_set_by_machine_not_by_agent(node):
    e = ev()
    e.quote_verified = True          # エージェントの自己申告
    bad = Evidence(url=URL, quote="ありもしない一文である。ここは長さも足りている。",
                   quote_verified=True)
    out, _ = sanitize(make(evidence=[e, bad]), {URL: PAGE}, node, TODAY)
    assert [x.url for x in out.evidence] == [URL]
    assert out.evidence[0].quote_verified is True


# ---- 根拠ゼロ ----

def test_changed_falls_back_to_uncertain_without_evidence(node):
    out, rej = sanitize(make(verdict="CHANGED"), {}, node, TODAY)
    assert out.verdict == "UNCERTAIN"
    assert out.confidence == "LOW"
    assert any("名乗れない" in r for r in rej)


def test_not_found_survives_without_evidence(node):
    """自治体にきちんとしたサイトが無い場合。失敗ではなく、これが答えである。"""
    r = make(verdict="NOT_FOUND")
    r.escalation = "自治体ごとに違う。電話で確かめること"
    out, _ = sanitize(r, {}, node, TODAY)
    assert out.verdict == "NOT_FOUND"
    assert out.escalation


def test_patch_without_evidence_is_dropped(node):
    p = ProposedPatch(json_pointer="/deadline/absolute_date",
                      current="2027-12-31", proposed="2029-12-31")
    out, rej = sanitize(make(verdict="AMBIGUOUS", patch=p), {}, node, TODAY)
    assert out.proposed_patch is None
    assert any("根拠が無いのに" in r for r in rej)


# ---- 書き換え先 ----

def test_pointer_must_exist(node):
    p = ProposedPatch(json_pointer="/deadline/no_such_field", proposed="x")
    assert any("ノードに無い" in e for e in validate_patch(node, p))


def test_pointer_outside_allowed_area_is_refused(node):
    p = ProposedPatch(json_pointer="/prerequisites/0", proposed="SELF_OTHER")
    assert any("触ってよい場所ではない" in e for e in validate_patch(node, p))


def test_type_change_is_refused(node):
    p = ProposedPatch(json_pointer="/deadline/absolute_date", proposed=2029)
    assert any("型が変わる" in e for e in validate_patch(node, p))


def test_same_value_is_refused(node):
    p = ProposedPatch(json_pointer="/deadline/absolute_date", proposed="2027-12-31")
    assert any("いまと同じ値" in e for e in validate_patch(node, p))


def test_valid_patch_passes_and_applies(node):
    p = ProposedPatch(json_pointer="/deadline/absolute_date", proposed="2029-12-31",
                      rationale="延長されたため")
    assert validate_patch(node, p) == []
    out = apply_patch(node, p)
    assert out["deadline"]["absolute_date"] == "2029-12-31"
    assert node["deadline"]["absolute_date"] == "2027-12-31"   # 元は壊さない


# ---- 格上げ禁止 ----

def test_expert_verified_cannot_be_proposed(node):
    p = ProposedPatch(json_pointer="/legal_metadata/verification_status",
                      proposed="EXPERT_VERIFIED")
    assert any("人以外が上げられない" in e for e in validate_patch(node, p))


def test_source_verified_can_be_proposed(node):
    doc = json.loads((ROOT / "data" / "nodes" / "nodes_survivor.json").read_text(encoding="utf-8"))
    n = next(x for x in doc["nodes"]
             if x["legal_metadata"]["verification_status"] == "SELF_REVIEWED")
    p = ProposedPatch(json_pointer="/legal_metadata/verification_status",
                      proposed="SOURCE_VERIFIED")
    assert validate_patch(n, p) == []


# ---- 人が見ることを外せない ----

def test_human_review_cannot_be_turned_off():
    with pytest.raises(ValidationError):
        CheckResult(check_id="X#1", node_id="X", claim="", verdict="UNCHANGED",
                    checked_at=TODAY, human_review_required=False)


def test_unknown_field_is_refused():
    with pytest.raises(ValidationError):
        CheckResult(check_id="X#1", node_id="X", claim="", verdict="UNCHANGED",
                    checked_at=TODAY, auto_apply=True)


# ---- 崩す役の急所（S07 第5節） ----

def test_two_deadlines_are_not_mixed(node):
    """
    事業承継税制。計画の提出期限は延びたが、株を渡す期限は動いていない。
    ひとつなぎに読むと「延長された」で終わる。ここを混ぜたら通さない。
    """
    mixed = ev(quote="特例承継計画の提出期限は令和9年9月30日までに延長されました")
    p = ProposedPatch(json_pointer="/deadline/absolute_date",
                      current="2027-12-31", proposed="2027-09-30",
                      rationale="延長されたため")
    out, _ = sanitize(make(verdict="CHANGED", evidence=[mixed], patch=p),
                      {URL: PAGE}, node, TODAY)
    # 引用そのものはページにあるので残る。しかし人の目に必ず掛かる。
    assert out.evidence and out.human_review_required is True
    # そして、渡す期限のほうを裏づける引用は、この根拠には含まれていない
    assert "12月31日" not in out.evidence[0].quote


def test_checks_file_covers_every_unverified_item():
    """extract_checks.py が 66 件を取りこぼしていないか。"""
    data = json.loads((ROOT / "data" / "checks" / "checks.json").read_text(encoding="utf-8"))
    total = 0
    for f in ("nodes_self.json", "nodes_family.json", "nodes_survivor.json"):
        doc = json.loads((ROOT / "data" / "nodes" / f).read_text(encoding="utf-8"))
        total += sum(len(n["legal_metadata"].get("needs_check") or []) for n in doc["nodes"])
    assert data["count"] == total == len(data["checks"])
    assert all(c["claim"] and c["question"] for c in data["checks"])
