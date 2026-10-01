"""
制度追従エージェントが出す答えの形（S07 第3節）

エージェントは JSON を書き換えない。この形の「案」を出すところで止まる。
だから human_review_required は常に true であり、false にする道を作らない。
"""
import re
from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

# 答えは成功・失敗の二値ではない（S07 第2節）
Verdict = Literal["UNCHANGED", "CHANGED", "AMBIGUOUS", "NOT_FOUND", "UNCERTAIN", "UNVERIFIED"]
Confidence = Literal["HIGH", "MEDIUM", "LOW"]

# 人以外がここへ上げる道はない
FORBIDDEN_STATUS = "EXPERT_VERIFIED"


class Evidence(BaseModel):
    url: str
    publisher: str = ""
    page_date: Optional[date] = None
    quote: str = ""
    # 取得ページに一字一句あったか。機械が付ける。エージェントの自己申告は信じない。
    quote_verified: bool = False

    model_config = {"extra": "forbid"}


class ProposedPatch(BaseModel):
    json_pointer: str
    current: object = None
    proposed: object = None
    rationale: str = ""

    model_config = {"extra": "forbid"}

    @field_validator("json_pointer")
    @classmethod
    def _must_be_pointer(cls, v: str) -> str:
        if not v.startswith("/"):
            raise ValueError("json_pointer は / で始まること")
        return v


class CheckResult(BaseModel):
    check_id: str
    node_id: str
    claim: str
    verdict: Verdict
    confidence: Confidence = "LOW"
    checked_at: date
    evidence: list[Evidence] = Field(default_factory=list)
    proposed_patch: Optional[ProposedPatch] = None
    risk_if_wrong: str = ""
    # 人に投げる理由（自治体差、ページなし など）
    escalation: Optional[str] = None
    # UNVERIFIED 検出時の詳細情報（複数自治体ルール等）
    detected_constraints: Optional[dict] = None
    # Day 5: Stage 2（Evaluator）で検出された曖昧さ（5形態）
    detected_ambiguities: list[str] = Field(default_factory=list)
    # Day 5: Stage 2（Evaluator）での早期検知ログ
    early_warnings: list[str] = Field(default_factory=list)
    # 関門（guards.sanitize）が落としたものの理由。機械が付ける。
    # 捨てると「なぜ根拠や修正案が消えたか」が分からなくなり、直しようがない。
    rejected: list[str] = Field(default_factory=list)
    human_review_required: bool = True

    model_config = {"extra": "forbid"}

    @field_validator("node_id")
    @classmethod
    def _must_be_alphanumeric_or_underscore(cls, v: str) -> str:
        if not re.match(r"^[A-Z0-9_]+$", v):
            raise ValueError("node_id は英数字・アンダースコア限定（^[A-Z0-9_]+$）である必要があります")
        return v

    @field_validator("human_review_required")
    @classmethod
    def _always_true(cls, v: bool) -> bool:
        if v is not True:
            raise ValueError("human_review_required を false にはできない")
        return True
