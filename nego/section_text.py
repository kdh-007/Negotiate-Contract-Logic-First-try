"""제안요청서/과업지시서 원문을 목차 번호매김에 맞춰 최상위 절 단위로 나눈다.

로드맵(narabid.md) 5단계 — "유사 과거 제안서 검색" — 준비 작업이다. 지금까지는
사업 하나의 원문을 통째로 이어붙여 `summaryText`로 썼는데(`nego/attachments.py`
`save_attachment_texts` 참고), 그러면 "사업개요"·"과업내용"처럼 실제 내용이 담긴
절과 "제안서 작성요령"·"협상 및 계약" 같은 행정 절차 절이 똑같은 비중으로 섞인다.
이 모듈은 절 경계만 찾아준다 — 어떤 절이 "사업개요"에 해당하는지는 호출측이
`find_section`에 키워드를 넘겨 판단한다(회사·공고마다 쓰는 용어가 달라 여기서
하나로 못박지 않는다).

실측(2026-09-22, jiil-past-contracts 2017~2021년 제안요청서·과업지시서 107건 원문
전수 확인, 사용자 지적으로 2차 보정): 최상위 절 번호매김은 3가지 관행이 있다 —
로마숫자(Ⅰ.Ⅱ.Ⅲ..., 70/107), "제N장"(9/107), 아라비아숫자에 가나다 하위항목이
붙는 형태(19/107). 나머지 9건은 신뢰할 구조가 없어 통째로 반환한다(fail-open).

  - **목차 vs 본문**: 거의 모든 문서에 별도 목차가 있어 같은 절 제목이 두 번 이상
    등장한다. 제목 문자열 대조는 목차 쪽 텍스트가 페이지번호 필드 노이즈로 깨져
    있어 불안정하다 — 대신 "번호 1(Ⅰ, 제1장, 아라비아 1)이 문서에서 마지막으로
    등장하는 위치"를 본문 시작 경계로 삼는다. 번호별로 독립적으로 마지막 등장을
    찾으면 순서가 꼬일 수 있어(실측: "상상나라" 2017 문서, Ⅲ이 Ⅰ보다 먼저 나오는
    결과) 경계 이후 번호들을 발견 순서 그대로 취한다.
  - **로마숫자의 오탐**: "Ⅰ., Ⅱ., Ⅲ., Ⅳ. ····"처럼 여러 절 번호를 한 문장에서
    나열하는 상투 문구가 실측 16개 문서에서 발견됐다 — 이 줄도 "Ⅰ."로 시작해
    매칭되면서 진짜 "마지막 Ⅰ"보다 뒤에 있으면 경계를 잘못 잡는다(실측: 국립중앙
    과학관 2017 제안요청서 — 로마숫자 구조가 분명히 있는데도 이 문장 때문에 구조를
    못 찾았었다). 제목이 쉼표 다음 곧장 다른 로마숫자로 이어지는 줄은 제외한다.
  - **아라비아숫자만으로는 신뢰 불가**: "1. 2. 3."는 진짜 최상위 절(예: 고성군
    독립만세운동 기념탑 제안요청서 — "1. 사업내용" 아래 "가.~마."로 이어짐)과
    법률식 조항 나열(예: DMZ/국립전주박물관 제안요청서 — "1. 사업목적"처럼 가나다
    하위항목 없이 문장 하나로 끝나는 조항)을 구분할 수 없다. 가나다 하위항목이
    2개 이상 뒤따르는지로 판별하면 실측 사례가 정확히 갈린다(고성군 5/12 대
    나머지 문서 0/12 확인). 다만 "N. 입찰자는 수행실적 평가를 위하여 ..."처럼
    본문 중 조항 설명 문장이 우연히 가나다를 포함하는 경우가 있어(실측 6건),
    절 제목치고 너무 긴(20자 초과) 캡처는 조항 문장으로 보고 추가로 걸러낸다.
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
_ARABIC_TOP_RE = re.compile(r"^[ \t　]*(\d{1,2})\.[ \t　]+(?=[^\d\s])(.*)$", re.MULTILINE)
_KOREAN_LETTER_ITEM_RE = re.compile(r"^[ \t　]*[가나다라마바사아자차카타파하]\.[ \t　]", re.MULTILINE)

# "Ⅰ., Ⅱ., Ⅲ. ..." 처럼 절 번호 여러 개를 한 문장에서 나열하는 상투 문구 —
# 진짜 절 제목이라면 쉼표 다음 곧장 다른 로마숫자가 나올 일이 없다.
_ROMAN_LISTING_GUARD_RE = re.compile(r"^[,、][ \t　]*[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]")

# 아라비아 절 제목은 짧은 명사구여야 한다 — 이보다 길면 조항 설명 문장으로 본다.
_ARABIC_TITLE_MAX_LEN = 20
# 아라비아 절이 진짜 최상위 구조이려면 가나다 하위항목이 최소 이만큼 뒤따라야 한다.
_MIN_SUBITEMS = 2
# 최상위 구조로 신뢰하려면 최소 이만큼은 있어야 한다 — 1~2개만 우연히 매치된
# 경우(예: 본문 중 번호가 딱 한 번 다른 용도로 등장)는 목차 구조로 보지 않는다.
_MIN_SECTIONS = 3


@dataclass
class Section:
    heading: str
    body: str


def _roman_matches(text: str) -> list[re.Match]:
    return [m for m in _ROMAN_HEADING_RE.finditer(text) if not _ROMAN_LISTING_GUARD_RE.match(m.group(2).strip())]


def _corroborated_arabic_matches(text: str) -> list[re.Match]:
    """가./나./다. 하위 항목이 뒤따르는 아라비아 절만 최상위 구조로 신뢰한다."""
    matches = list(_ARABIC_TOP_RE.finditer(text))
    corroborated = []
    for i, m in enumerate(matches):
        if len(m.group(2).strip()) > _ARABIC_TITLE_MAX_LEN:
            continue
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end() : end]
        if len(_KOREAN_LETTER_ITEM_RE.findall(body)) >= _MIN_SUBITEMS:
            corroborated.append(m)
    return corroborated


def _sections_from_matches(text: str, matches: list[re.Match], first_value) -> list[Section]:
    """matches를 "번호 1이 마지막으로 나오는 위치"부터 절로 나눈다.

    `first_value(match) -> int`가 각 매치의 번호값(Ⅰ=1, 제1장=1, 1.=1 등)을 준다.
    """
    if not matches:
        return []

    first_positions = [m.start() for m in matches if first_value(m) == 1]
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

    로마숫자 → 제N장 → 아라비아숫자(가나다 하위항목으로 검증됨) 순으로 시도하고,
    셋 다 신뢰할 만한 구조를 못 찾으면 전체 텍스트를 절 1개(heading="")로
    반환한다(fail-open) — 어설프게 쪼개서 조항 문장을 "절"로 오인하는 것보다,
    통째로 넘겨서 호출측이 원문 그대로 보는 편이 낫다.
    """
    sections = _sections_from_matches(text, _roman_matches(text), lambda m: _ROMAN_VALUES.get(m.group(1)))
    if sections:
        return sections

    sections = _sections_from_matches(text, list(_CHAPTER_HEADING_RE.finditer(text)), lambda m: int(m.group(1)))
    if sections:
        return sections

    sections = _sections_from_matches(text, _corroborated_arabic_matches(text), lambda m: int(m.group(1)))
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
