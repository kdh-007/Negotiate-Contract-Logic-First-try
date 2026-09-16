"""업역 스크리닝 + 일정 계산.

키워드·제외키워드·최소예산·코드 매칭 규칙은 **기존 시스템과 동일하게 유지한다**
(`matching/matchEngine.ts`, `keywordMatcher.ts`, `codeMatcher.ts`).

  - 제외키워드가 제목에 하나라도 있으면 무조건 제외
  - 예산이 확인되는 공고 중 minBudgetAmount 미만이면 제외 (예산 미상은 통과)
  - 세부품명번호(물품)는 정확일치 → 단독으로도 인정
  - 업종코드는 '투찰가능업종명' 텍스트 부분일치라 정밀도가 낮음 → 제목 키워드가
    함께 있을 때만 인정 (단독 매칭은 결과에서 제외)
  - 코드+키워드 둘 다 → '강력추천', 하나만 → '참고용'
  - 키워드 비교 시 공백은 무시 ("운영 용역" == "운영용역")

**해외 전시회 한국관/단체관 판별** — 기존 시스템에는 없던 신규 규칙(2026-09
결정). "한국관"이라는 단어만 보고 국내 공고로 오인하기 쉽다(실측:
R26BK01717819 UAE 두바이 의료기기전시회, R26BK01714892 두바이 — 둘 다
해외 개최인데 "한국관" 제목 때문에 국내로 오인됨). "전시회/박람회/엑스포"
+ "한국관/단체관"이 제목에 같이 있으면 해외 부스 설치 공고로 보고 자동
제외한다. 단, 회사가 실제로 확장 중인 몽골만 예외 — 자동 제외 대신
플래그(🌐)만 남겨 담당자가 직접 확인하게 한다(이땐 통상적인 키워드/코드
불일치 제외도 건너뛴다 — 플래그가 붙는데 조용히 빠지면 안 되므로).
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
    # 해외 전시회 한국관/단체관인데 개최지가 몽골이라 자동 제외 대신 통과시킨 경우.
    overseas_flag: bool = False


def _match_exclude(notice: Notice, exclude_keywords: list[str]) -> str | None:
    haystack = _squash(notice.title)
    for word in exclude_keywords:
        if _squash(word) and _squash(word) in haystack:
            return word
    return None


def _match_keywords(notice: Notice, keywords: list[str]) -> list[str]:
    haystack = _squash(notice.title) + _squash(notice.product_class_name)
    return [w for w in keywords if _squash(w) and _squash(w) in haystack]


_OVERSEAS_EVENT_RE = re.compile(r"전시회|박람회|엑스포")
_KOREA_PAVILION_RE = re.compile(r"한국관|단체관")
_MONGOLIA_RE = re.compile(r"몽골")


def _is_overseas_exhibition_booth(notice: Notice) -> bool:
    """해외 전시회·박람회·엑스포에 한국 기업이 참가할 때 짓는 한국관/단체관
    부스 설치 공고인지 제목으로 판별한다."""
    haystack = _squash(notice.title) + _squash(notice.product_class_name)
    return bool(_OVERSEAS_EVENT_RE.search(haystack) and _KOREA_PAVILION_RE.search(haystack))


def _is_mongolia(notice: Notice) -> bool:
    haystack = _squash(notice.title) + _squash(notice.product_class_name)
    return bool(_MONGOLIA_RE.search(haystack))


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
    excluded_word = _match_exclude(notice, config.exclude_keywords)
    if excluded_word:
        return ScreenResult(
            matched=False,
            confidence=None,
            excluded_by="제외키워드",
            excluded_reason=excluded_word,
        )

    overseas_booth = _is_overseas_exhibition_booth(notice)
    mongolia = overseas_booth and _is_mongolia(notice)
    if overseas_booth and not mongolia:
        return ScreenResult(
            matched=False,
            confidence=None,
            excluded_by="해외개최",
            excluded_reason="해외 전시회·박람회·엑스포 한국관/단체관 — 국내 공고 아님",
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

    # 업종코드 단독 매칭은 결과에 포함하지 않는다 (기존 정책). 단, 몽골 해외관은
    # 키워드·코드가 하나도 안 맞아도 조용히 빼지 않는다 — 플래그를 달아 사람이
    # 보게 하는 게 목적이라 "미매칭"으로 걸러지면 그 목적 자체가 무산된다.
    if not product_hits and not keyword_hits and not mongolia:
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
        overseas_flag=mongolia,
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
