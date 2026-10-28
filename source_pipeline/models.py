from __future__ import annotations

from datetime import date
from typing import List, Literal, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

SourceType = Literal["primary", "authoritative", "secondary", "competitor", "blog", "aggregator"]
SourceRole = Literal["authoritative", "ineligible", "competitor", "neutral"]


class RawRecord(BaseModel):
    """One saved source record as it appears in the input JSONL."""
    model_config = ConfigDict(extra="forbid")

    source_id: str
    url: str
    publisher: Optional[str] = None
    source_type: SourceType
    publication_date: Optional[date] = None
    event_date: Optional[date] = None
    discovery_lineage: List[str] = Field(default_factory=list)
    content: Optional[str] = None

    @field_validator("url")
    @classmethod
    def _url_must_be_absolute_http(cls, v: str) -> str:
        p = urlparse(v)
        if p.scheme not in ("http", "https") or not p.netloc:
            raise ValueError("url must be an absolute http(s) URL")
        return v


class OutputRecord(BaseModel):
    """Normalised record exported after dedupe and policy evaluation.

    Duplicate status is not a separate struct: the contributors are listed in
    `merged_source_ids`, and any downstream consumer that cares can compare
    `len(merged_source_ids)` to 1.
    """
    source_id: str
    canonical_url: str
    publisher: Optional[str]
    source_type: SourceType
    publication_date: Optional[date]
    event_date: Optional[date]
    discovery_lineage: List[str]
    merged_source_ids: List[str]
    content: Optional[str]
    eligibility: Literal["eligible", "excluded"]
    reason_codes: List[str]
    requires_independent_verification: bool = False


class SourceTypeRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: SourceRole


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type_policy: dict[SourceType, SourceTypeRule]
    competitor_promo_keywords: List[str]
    competitor_generic_keywords: List[str]

    def role_for(self, source_type: SourceType) -> SourceRole:
        # Unknown source types are treated as neutral rather than silently
        # authoritative — an unconfigured type should be a review flag.
        rule = self.source_type_policy.get(source_type)
        return rule.role if rule else "neutral"
