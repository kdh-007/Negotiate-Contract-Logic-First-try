"""업역 스크리닝 + 일정 계산.

키워드·제외키워드·최소예산·코드 매칭 규칙은 **기존 시스템과 동일하게 유지한다**
(`matching/matchEngine.ts`, `keywordMatcher.ts`, `codeMatcher.ts`).

  - 해외 전시회 "한국관"/"단체관" 설치 공고는 무조건 제외 (국내 공고만 다루기로
    한 방침, 2026-09-15 결정 — excludeKeywords와는 별개로 코드에 직접 둔다)
  - 제외키워드가 제목에 하나라도 있으면 무조건 제외
  - 예산이 확인되는 공고 중 minBudgetAmount 미만이면 제외 (예산 미상은 통과)
  - 세부품명번호(물품)는 정확일치 → 단독으로도 인정
  - 업종코드는 '투찰가능업종명' 텍스트 부분일치라 정밀도가 낮음 → 제목 키워드가
    함께 있을 때만 인정 (단독 매칭은 결과에서 제외)
  - 코드+키워드 둘 다 → '강력추천', 하나만 → '참고용'
  - 키워드 비교 시 공백은 무시 ("운영 용역" == "운영용역")
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from .models import Notice


def _squash(text: str | None) -> str:
    return re.sub(r"\s+", "", text or "")


@dataclass
class ScreenConfig:
    keywords: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=list)
    min_budget_amount: float | None = None
    product_codes: list[dict[str, str]] = field(default_factory=list)
    industry_codes: list[dict[str, str]] = field(default_factory=list)


@dataclass
class ScreenResult:
    matched: bool
    confidence: str | None  # 강력추천 / 참고용
    matched_keywords: list[str] = field(default_factory=list)
    matched_product_codes: list[str] = field(default_factory=list)
    matched_industry_codes: list[str] = field(default_factory=list)
    excluded_by: str | None = None
    excluded_reason: str | None = None


# 해외 전시회 참가용 "한국관"/"단체관" 설치 공고 제외 — 국내 공고만 다루기로 한
# 방침(2026-09-15 결정)에 따른 것. `config/keywords.json`의 excludeKeywords는
# "회사가 정한 사업 판단, 임의로 안 바꾼다" 정책 하에 있어서 건드리지 않고
# 코드에 별도로 둔다.
#
# "한국관"/"단체관" 단독으로는 "한국관광공사" 같은 국내 기관명과 겹칠 위험이
# 있어서(부분일치라 "한국관광공사"도 "한국관"을 포함한다), 제목에 전시 행사를
# 뜻하는 단어가 함께 있을 때만 해외 파빌리온 설치로 판단한다.
#
# 처음엔 "전시회"만 봤는데, 실측으로 같은 유형 공고를 더 뽑아보니 "박람회"/
# "엑스포"도 동의어로 흔히 쓰여서("베오그라드엑스포", "이스탄불 국제식품박람회")
# 그것만 보면 8건 중 4건을 놓쳤다 — 세 단어를 모두 포함하도록 넓혔다.
# (발주기관 유형(협동조합/협회 등)으로 걸러보는 것도 검토했으나, 실측 기관명이
# "진흥원"/"공사"/"협동조합"/"협회"로 제각각이라 포기함.)
#
# 그래도 "HLTH USA"처럼 박람회 고유명사만 쓰고 일반 단어(전시회/박람회/엑스포)
# 자체가 없는 제목은 구조적으로 못 잡는다 — 알려진 한계로 남겨 둔다.
_EXHIBITION_WORDS = ("전시회", "박람회", "엑스포")
_OVERSEAS_PAVILION_WORDS = ("한국관", "단체관")


def _is_overseas_pavilion(notice: Notice) -> bool:
    haystack = _squash(notice.title)
    if not any(word in haystack for word in _EXHIBITION_WORDS):
        return False
    return any(word in haystack for word in _OVERSEAS_PAVILION_WORDS)


def _match_exclude(notice: Notice, exclude_keywords: list[str]) -> str | None:
    haystack = _squash(notice.title)
    for word in exclude_keywords:
        if _squash(word) and _squash(word) in haystack:
            return word
    return None


def _match_keywords(notice: Notice, keywords: list[str]) -> list[str]:
    haystack = _squash(notice.title) + _squash(notice.product_class_name)
    return [w for w in keywords if _squash(w) and _squash(w) in haystack]


def _match_codes(notice: Notice, config: ScreenConfig) -> tuple[list[str], list[str]]:
    product_hits = [
        entry["name"]
        for entry in config.product_codes
        if notice.product_class_no and entry.get("code") == notice.product_class_no
    ]
    industry_text = _squash(notice.industry_text)
    industry_hits = [
        entry["name"]
        for entry in config.industry_codes
        if entry.get("name") and _squash(entry["name"]) in industry_text
    ]
    return product_hits, sorted(set(industry_hits))


def screen(notice: Notice, config: ScreenConfig) -> ScreenResult:
    if _is_overseas_pavilion(notice):
        return ScreenResult(
            matched=False,
            confidence=None,
            excluded_by="해외공고",
            excluded_reason="해외 전시회 한국관/단체관 설치",
        )

    excluded_word = _match_exclude(notice, config.exclude_keywords)
    if excluded_word:
        return ScreenResult(
            matched=False,
            confidence=None,
            excluded_by="제외키워드",
            excluded_reason=excluded_word,
        )

    budget = notice.budget
    if config.min_budget_amount is not None and budget is not None and budget < config.min_budget_amount:
        return ScreenResult(
            matched=False,
            confidence=None,
            excluded_by="최소예산",
            excluded_reason=f"{budget:,.0f}원 < {config.min_budget_amount:,.0f}원",
        )

    product_hits, industry_hits = _match_codes(notice, config)
    keyword_hits = _match_keywords(notice, config.keywords)

    # 업종코드 단독 매칭은 결과에 포함하지 않는다 (기존 정책).
    if not product_hits and not keyword_hits:
        return ScreenResult(
            matched=False,
            confidence=None,
            excluded_by="미매칭",
            excluded_reason="키워드·품명코드 모두 불일치",
        )

    code_matched = bool(product_hits or industry_hits)
    return ScreenResult(
        matched=True,
        confidence="강력추천" if code_matched and keyword_hits else "참고용",
        matched_keywords=keyword_hits,
        matched_product_codes=product_hits,
        matched_industry_codes=industry_hits,
    )


# ── 일정 ────────────────────────────────────────────────────

_DATE_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y%m%d%H%M", "%Y%m%d")


def parse_datetime(text: str | None) -> datetime | None:
    if not text:
        return None
    cleaned = str(text).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    return None


@dataclass
class Schedule:
    """협상계약에는 서로 다른 마감이 여럿이다. 가장 이른 것이 실질 마감이다."""

    qualification_deadline: datetime | None
    joint_agreement_deadline: datetime | None
    bid_deadline: datetime | None

    @property
    def earliest(self) -> tuple[str, datetime] | None:
        candidates = [
            ("자격등록 마감", self.qualification_deadline),
            ("공동수급협정 마감", self.joint_agreement_deadline),
            ("입찰 마감", self.bid_deadline),
        ]
        valid = [(label, dt) for label, dt in candidates if dt is not None]
        if not valid:
            return None
        return min(valid, key=lambda pair: pair[1])

    def days_left(self, now: datetime) -> int | None:
        earliest = self.earliest
        if earliest is None:
            return None
        return (earliest[1] - now).days


def build_schedule(notice: Notice) -> Schedule:
    return Schedule(
        qualification_deadline=parse_datetime(notice.qualification_deadline),
        joint_agreement_deadline=parse_datetime(notice.joint_agreement_deadline),
        bid_deadline=parse_datetime(notice.bid_deadline),
    )
