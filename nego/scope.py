"""「협상에 의한 계약」 스코프 판별.

핵심: 협상에 의한 계약은 **계약체결방법이 아니라 낙찰자결정방법**이다.
 - 낙찰자결정방법 `sucsfbidMthdNm` = "협상에의한계약-…"   ← 여기로 판별한다
 - 계약체결방법 `cntrctCnclsMthdNm` = 일반경쟁 / 제한경쟁 / …  ← 직교. 필터축이 아니다

접미 괄호가 붙은 변형이 여러 개라 완전일치가 아닌 **접두어 매칭**을 쓴다.
  협상에의한계약-협상에 의한 낙찰자 결정
  협상에의한계약-협상에 의한 낙찰자 결정(SW사업)
  협상에의한계약-협상에 의한 낙찰자 결정(건설엔지니어링)
  협상에의한계약-협상에 의한 낙찰자 결정(엔지니어링)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Notice

NEGOTIATED_PREFIX = "협상에의한계약"

# 협상은 아니지만 제안서로 겨루는 방식이라 업역이 겹친다.
# 제외하지 않고 태그만 달아 둔다 — 나중에 판단할 수 있게.
ADJACENT_PREFIXES = ("설계공모", "규격가격동시입찰")

CANCELLED_KIND = "취소공고"


def _normalize(text: str | None) -> str:
    return re.sub(r"\s+", "", text or "")


def is_negotiated(notice: Notice) -> bool:
    return _normalize(notice.award_method).startswith(NEGOTIATED_PREFIX)


def is_adjacent(notice: Notice) -> bool:
    normalized = _normalize(notice.award_method)
    return any(normalized.startswith(p) for p in ADJACENT_PREFIXES)


def is_cancelled(notice: Notice) -> bool:
    return (notice.notice_kind or "").strip() == CANCELLED_KIND


def is_re_notice(notice: Notice) -> bool:
    """재공고 = 직전 회차 유찰. 경쟁이 적다는 신호라 별도로 표시한다."""
    return (notice.re_notice_yn or "").strip().upper() == "Y"


def negotiation_variant(notice: Notice) -> str | None:
    """'(SW사업)' 같은 접미 변형만 뽑아낸다. 없으면 '일반'."""
    if not is_negotiated(notice):
        return None
    match = re.search(r"\(([^)]+)\)\s*$", notice.award_method or "")
    return match.group(1) if match else "일반"


def latest_ordinals(notices: list[Notice]) -> list[Notice]:
    """같은 공고번호는 최신 차수만 남긴다.

    차수는 '000', '001' 같은 문자열이라 정수로 비교한다.
    변경공고로 덮이더라도 변경사유(change_reason)는 최신 차수 쪽에 남아 있다.
    """
    best: dict[tuple[str, str], Notice] = {}
    for notice in notices:
        key = (notice.work_type, notice.notice_no)
        current = best.get(key)
        if current is None or _ord_value(notice.notice_ord) > _ord_value(current.notice_ord):
            best[key] = notice
    return list(best.values())


def _ord_value(text: str | None) -> int:
    try:
        return int(str(text).strip())
    except (TypeError, ValueError):
        return -1


@dataclass
class ScopeResult:
    kept: list[Notice]
    adjacent: list[Notice]
    dropped_cancelled: int
    dropped_not_negotiated: int
    dropped_old_ordinal: int


def apply_scope(notices: list[Notice]) -> ScopeResult:
    """수집된 전체 공고에서 협상 스코프만 남긴다.

    **순서가 중요하다. 최신 차수를 먼저 고르고, 그 다음에 취소 여부를 본다.**
    취소를 먼저 걷어내면 "000 등록공고 → 001 취소공고"인 사업에서 001만 사라지고
    000이 살아남아, 취소된 사업이 검토 목록에 그대로 올라온다.
    반대로 "001 취소공고 → 002 재공고"처럼 되살아난 건은 최신 차수가 002라 정상 유지된다.
    """
    before = len(notices)

    latest = latest_ordinals(notices)
    dropped_old_ordinal = before - len(latest)

    alive = [n for n in latest if not is_cancelled(n)]
    dropped_cancelled = len(latest) - len(alive)

    negotiated = [n for n in alive if is_negotiated(n)]
    adjacent = [n for n in alive if is_adjacent(n)]
    dropped_not_negotiated = len(alive) - len(negotiated)

    return ScopeResult(
        kept=negotiated,
        adjacent=adjacent,
        dropped_cancelled=dropped_cancelled,
        dropped_not_negotiated=dropped_not_negotiated,
        dropped_old_ordinal=dropped_old_ordinal,
    )


# ── 사업 단위 그룹핑 ──────────────────────────────────────────
# untyNtceNo(통합공고번호)로 재공고가 묶일 거라 기대했지만, 실측 결과 공고번호와 1:1이라
# 묶이지 않는다. 그래서 발주기관 + 금액 + 제목 유사도로 묶는다.

_TITLE_NOISE = re.compile(r"\[[^\]]*\]|\([^)]*\)|긴급|재공고|변경|제\d+차|용역|공고")


def title_signature(title: str) -> str:
    """제목에서 부가 표기를 걷어내고 비교용 문자열을 만든다."""
    cleaned = _TITLE_NOISE.sub(" ", title or "")
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", cleaned)


def _similarity(a: str, b: str) -> float:
    from difflib import SequenceMatcher

    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def group_projects(notices: list[Notice], threshold: float = 0.75) -> list[list[Notice]]:
    """같은 사업으로 보이는 공고를 묶는다 (재공고는 공고번호가 달라 차수로는 안 묶인다)."""
    groups: list[list[Notice]] = []

    for notice in sorted(notices, key=lambda n: (n.notice_institution or "", n.title)):
        signature = title_signature(notice.title)
        placed = False
        for group in groups:
            head = group[0]
            same_institution = (head.notice_institution or "") == (notice.notice_institution or "")
            same_budget = head.budget == notice.budget
            if same_institution and (same_budget or _similarity(signature, title_signature(head.title)) >= threshold):
                group.append(notice)
                placed = True
                break
        if not placed:
            groups.append([notice])

    return groups
