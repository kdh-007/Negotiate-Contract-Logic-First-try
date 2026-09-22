"""제안요청서/과업지시서 원문을 목차 번호매김에 맞춰 최상위 절 단위로 나눈다.

로드맵(narabid.md) 5단계 — "유사 과거 제안서 검색" — 준비 작업이다. 지금까지는
사업 하나의 원문을 통째로 이어붙여 `summaryText`로 썼는데(`nego/attachments.py`
`save_attachment_texts` 참고), 그러면 "사업개요"·"과업내용"처럼 실제 내용이 담긴
절과 "제안서 작성요령"·"협상 및 계약" 같은 행정 절차 절이 똑같은 비중으로 섞인다.
이 모듈은 절 경계만 찾아준다 — 어떤 절이 "사업개요"에 해당하는지는 호출측이
`find_section`에 키워드를 넘겨 판단한다(회사·공고마다 쓰는 용어가 달라 여기서
하나로 못박지 않는다).

실측(2026-09-22, jiil-past-contracts 2017~2021년 제안요청서 76건 원문 전수 확인):
  - 최상위 절 번호매김은 로마숫자(Ⅰ. Ⅱ. Ⅲ. ...)가 가장 많고(63/76), "제N장"이
    다음(8/76)이다. 나머지는 로마숫자/장 표기가 아예 없거나 문서 곳곳의 조항
    번호("1. 발주자 및 구성원 전원이 동의하는 경우" 같은 법률식 나열)만 있어
    최상위 구조를 신뢰 있게 못 찾는다 — 이런 문서는 걸러내는 대신 억지로 쪼개면
    조항 하나가 "절"로 둔갑하는 오탐이 나서(실측 확인함) 시도하지 않는다
    (`split_sections`가 통째로 1개 절을 반환 — `qualification_text.py`와 같은
    fail-open 원칙).
  - 거의 모든 문서(74/76)에 별도 "목차" 페이지가 있어 같은 절 제목이 문서에
    두 번(목차 + 본문) 이상 등장한다. 제목 문자열로 목차와 본문을 구분하려 하면
    목차 쪽 텍스트가 페이지번호용 필드/리더점 노이즈로 깨져 있어(첨부 텍스트
    추출 노이즈 참고) 매칭이 불안정하다 — 대신 "번호 1(Ⅰ, 제1장)이 문서에서
    마지막으로 등장하는 위치"를 본문 시작 경계로 삼는다. 그 경계 이후에 나오는
    번호 매김만 순서대로 취해 절을 나눈다(번호별 마지막 등장을 독립적으로 찾는
    방식은 실측상 실패함 — 어떤 번호가 본문 밖에서 우연히 더 나오면 순서가
    꼬였다: 실측 "상상나라" 2017 문서에서 Ⅲ이 Ⅰ보다 먼저 나오는 결과가 나옴).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_ROMAN_VALUES = {
    "Ⅰ": 1, "Ⅱ": 2, "Ⅲ": 3, "Ⅳ": 4, "Ⅴ": 5,
    "Ⅵ": 6, "Ⅶ": 7, "Ⅷ": 8, "Ⅸ": 9, "Ⅹ": 10,
}

_ROMAN_HEADING_RE = re.compile(r"^[ \t　]*([ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ])\.[ \t　]*(.*)$", re.MULTILINE)
_CHAPTER_HEADING_RE = re.compile(r"^[ \t　]*제[ \t　]*(\d{1,2})[ \t　]*장[ \t　]*(.*)$", re.MULTILINE)

# 최상위 구조로 신뢰하려면 최소 이만큼은 있어야 한다 — 1~2개만 우연히 매치된
# 경우(예: 본문 중 "Ⅰ."이 딱 한 번 다른 용도로 등장)는 목차 구조로 보지 않는다.
_MIN_SECTIONS = 3


@dataclass
class Section:
    heading: str
    body: str


def _first_value(pattern: re.Pattern, match: re.Match) -> int:
    group = match.group(1)
    return _ROMAN_VALUES[group] if pattern is _ROMAN_HEADING_RE else int(group)


def _split_by_pattern(text: str, pattern: re.Pattern) -> list[Section]:
    matches = list(pattern.finditer(text))
    if not matches:
        return []

    # "번호 1"이 마지막으로 나오는 위치를 본문 시작 경계로 삼는다 — 그 앞은
    # 전부 목차(또는 목차류 반복)로 버린다.
    first_positions = [m.start() for m in matches if _first_value(pattern, m) == 1]
    boundary = first_positions[-1] if first_positions else matches[0].start()

    body_matches = [m for m in matches if m.start() >= boundary]
    if len(body_matches) < _MIN_SECTIONS:
        return []

    sections = []
    for i, m in enumerate(body_matches):
        end = body_matches[i + 1].start() if i + 1 < len(body_matches) else len(text)
        sections.append(Section(heading=m.group(0).strip(), body=text[m.end() : end].strip("\n")))
    return sections


def split_sections(text: str) -> list[Section]:
    """문서를 최상위 절 단위로 나눈다.

    로마숫자 → 제N장 순으로 시도하고, 둘 다 신뢰할 만한 구조를 못 찾으면
    전체 텍스트를 절 1개(heading="")로 반환한다(fail-open) — 어설프게 쪼개서
    조항 문장을 "절"로 오인하는 것보다, 통째로 넘겨서 호출측이 원문 그대로
    보는 편이 낫다.
    """
    for pattern in (_ROMAN_HEADING_RE, _CHAPTER_HEADING_RE):
        sections = _split_by_pattern(text, pattern)
        if sections:
            return sections
    stripped = text.strip()
    return [Section(heading="", body=stripped)] if stripped else []


def find_section(text: str, keywords: list[str]) -> Section | None:
    """`split_sections` 결과 중 제목에 keywords 중 하나라도 포함된 첫 절을 반환.

    공백 유무 표기 차이(예: "사업개요" vs "사업 개요")를 무시하고 비교한다.
    """
    normalized_keywords = [kw.replace(" ", "") for kw in keywords]
    for section in split_sections(text):
        normalized_heading = section.heading.replace(" ", "")
        if any(kw in normalized_heading for kw in normalized_keywords):
            return section
    return None
