"""제안요청서/과업지시서 원문을 목차 번호매김에 맞춰 최상위 절 단위로 나눈다.

로드맵(narabid.md) 5단계 — "유사 과거 제안서 검색" — 준비 작업이다. 지금까지는
사업 하나의 원문을 통째로 이어붙여 `summaryText`로 썼는데(`nego/attachments.py`
`save_attachment_texts` 참고), 그러면 "사업개요"·"과업내용"처럼 실제 내용이 담긴
절과 "제안서 작성요령"·"협상 및 계약" 같은 행정 절차 절이 똑같은 비중으로 섞인다.
이 모듈은 절 경계만 찾아준다 — 어떤 절이 "사업개요"에 해당하는지는 호출측이
`find_section`에 키워드를 넘겨 판단한다(회사·공고마다 쓰는 용어가 달라 여기서
하나로 못박지 않는다).

실측(2026-09-22, jiil-past-contracts 2017~2021년 제안요청서·과업지시서 107건 원문
전수 확인, 사용자 지적으로 4차 보정): 최상위 절 번호매김은 5가지 관행이 있다 —
로마숫자를 같은 줄에 쓰는 "Ⅰ. 제목"(가장 흔함), 로마숫자와 제목을 **다른 줄에**
쓰는 "Ⅰ\\n제목"(박스형 제목틀 — 사용자 스크린샷으로 확인, 46/107건에서 발견),
"제N장", 원문자(①②③...) 폰트 글리프, 아라비아숫자에 가나다 하위항목이 붙는
형태. 구조 인식률 98%(105/107). 나머지 2건은 챕터 자체가 없는 순수 표/서식
문서(사업수행능력평가표 등)라 신뢰할 구조가 없어 통째로 반환한다(fail-open).

  - **목차 vs 본문**: 거의 모든 문서에 별도 목차가 있어 같은 절 제목이 두 번 이상
    등장한다. 제목 문자열 대조는 목차 쪽 텍스트가 페이지번호 필드 노이즈로 깨져
    있어 불안정하다 — 대신 "번호 1(Ⅰ, 제1장, 아라비아 1)이 문서에서 마지막으로
    등장하는 위치"를 본문 시작 경계로 삼는다. 번호별로 독립적으로 마지막 등장을
    찾으면 순서가 꼬일 수 있어(실측: "상상나라" 2017 문서, Ⅲ이 Ⅰ보다 먼저 나오는
    결과) 경계 이후 번호들을 발견 순서 그대로 취한다.
  - **박스형 제목틀**: "Ⅰ" 한 글자만 있는 줄 다음에(빈 줄이 끼어도 됨) 제목 줄이
    오는 형식 — 원본을 열어보면 번호가 네모 박스 안에, 제목이 그 옆에 굵은 글씨로
    배치된 디자인이다(사용자 스크린샷 확인). "Ⅰ." 한 줄짜리 표기와 같은 문서
    안에서 섞여 쓰이기도 한다(예: KOSCOM 2017 제안요청서) — 두 패턴의 매치를
    합쳐서 위치순으로 정렬한 뒤 같은 로직(경계 찾기)을 적용한다.
  - **로마숫자의 오탐**: "Ⅰ., Ⅱ., Ⅲ., Ⅳ. ····"처럼 여러 절 번호를 한 문장에서
    나열하는 상투 문구가 실측 16개 문서에서 발견됐다 — 이 줄도 "Ⅰ."로 시작해
    매칭되면서 진짜 "마지막 Ⅰ"보다 뒤에 있으면 경계를 잘못 잡는다(실측: 국립중앙
    과학관 2017 제안요청서). 제목이 쉼표 다음 곧장 다른 로마숫자로 이어지는 줄은
    제외한다.
  - **아라비아숫자만으로는 신뢰 불가**: "1. 2. 3."는 진짜 최상위 절(예: 고성군
    독립만세운동 기념탑 제안요청서 — "1. 사업내용" 아래 "가.~마."로 이어짐)과
    법률식 조항 나열(예: DMZ/국립전주박물관 제안요청서 — "1. 사업목적"처럼 가나다
    하위항목 없이 문장 하나로 끝나는 조항)을 구분할 수 없다. 가나다 하위항목이
    2개 이상 뒤따르는지로 판별한다. 조항 설명 문장이 우연히 가나다를 포함하는
    경우가 있어(실측 6건), 절 제목치고 너무 긴(20자 초과) 캡처는 걸러낸다.
  - **최소 절 개수**: 사용자 확인(2026-09-22) — 함양 곶감 조형물(Ⅰ. 하나뿐),
    화진포 씨월드(Ⅰ.Ⅱ. 둘뿐)처럼 최상위 절이 1~2개인 문서도 실제로 존재하는
    정상 구조다. 예전에는 "1~2개만 우연히 매치됐을 가능성"을 우려해 최소 3개를
    요구했지만, 사용자가 실측으로 확인한 만큼 최소 1개로 낮췄다 — 경계(번호 1의
    마지막 등장)를 먼저 잡은 뒤의 결과라 여전히 목차 오매칭 위험은 낮다.
  - **원문자(PUA 폰트 글리프) 절**: 법천사지(2021)·웅진백제역사관(2019) 과업지시서는
    "①②③..." 대신 한컴 전용 폰트가 쓰는 유니코드 개인 영역(Private Use Area)
    코드포인트 U+F02B1(=1)부터 순서대로 올라가는 글리프로 절 번호를 매긴다(사용자
    제보로 원본 파일 직접 역추적해 확인). 각 절 아래 가.나.다.로 하위항목이 이어지는
    건 아라비아+가나다 절과 같은 모양이지만, 최상위 번호 자체가 "1. 2. 3."이 아니라
    이 PUA 글리프라 별도 패턴이 필요하다. 화면에 그대로 출력하면 폰트가 없는 환경에서
    안 보이므로, 절 제목에는 실제 유니코드 원문자(①②③...)로 바꿔서 담는다.
    한계: 법천사지의 첫 절("①")은 바로 앞 제어문자 노이즈 블록에 이 PUA 글리프까지
    같이 먹혀 원문에서 아예 사라졌다 — 그 경우 1번 절 내용은 어느 절에도 안 잡히고
    2번 절부터 시작한다(예약 슬롯 소실이지 이 모듈의 버그는 아님).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_ROMAN_VALUES = {
    "Ⅰ": 1, "Ⅱ": 2, "Ⅲ": 3, "Ⅳ": 4, "Ⅴ": 5,
    "Ⅵ": 6, "Ⅶ": 7, "Ⅷ": 8, "Ⅸ": 9, "Ⅹ": 10,
}

_ROMAN_HEADING_RE = re.compile(r"^[ \t　]*([ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ])\.[ \t　]*(.*)$", re.MULTILINE)
# 박스형 제목틀: 번호만 있는 줄(+빈 줄 허용) 다음에 제목 줄.
_ROMAN_BOXED_RE = re.compile(
    r"^[ \t　]*([ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ])[ \t　]*\r?\n"
    r"(?:[ \t　]*\r?\n)*"
    r"[ \t　]*(\S[^\n]*)$",
    re.MULTILINE,
)
# 한컴 전용 폰트가 원문자(①②③...) 대용으로 쓰는 유니코드 개인 영역 글리프.
# U+F02B1=1, U+F02B2=2, ... (법천사지/웅진백제역사관 과업지시서 실측으로 확인,
# 최대 16개까지 여유 있게 잡는다 — 실측 문서는 8개가 최대였음).
_CIRCLED_PUA_BASE = 0xF02B0
_CIRCLED_PUA_RE = re.compile(
    r"^[ \t　]*([\U000f02b1-\U000f02c0])[ \t　]*(.*)$", re.MULTILINE
)
# 표시용 원문자(①~⑳) — PUA 글리프는 전용 폰트 없이는 안 보이므로 절 제목에는
# 이걸로 바꿔 담는다.
_CIRCLED_DIGITS = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"

_CHAPTER_HEADING_RE = re.compile(r"^[ \t　]*제[ \t　]*(\d{1,2})[ \t　]*장[ \t　]*(.*)$", re.MULTILINE)
_ARABIC_TOP_RE = re.compile(r"^[ \t　]*(\d{1,2})\.[ \t　]+(?=[^\d\s])(.*)$", re.MULTILINE)
_KOREAN_LETTER_ITEM_RE = re.compile(r"^[ \t　]*[가나다라마바사아자차카타파하]\.[ \t　]", re.MULTILINE)

# "Ⅰ., Ⅱ., Ⅲ. ..." 처럼 절 번호 여러 개를 한 문장에서 나열하는 상투 문구 —
# 진짜 절 제목이라면 쉼표 다음 곧장 다른 로마숫자가 나올 일이 없다.
_ROMAN_LISTING_GUARD_RE = re.compile(r"^[,、][ \t　]*[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]")

# 절 제목은 짧은 명사구여야 한다 — 이보다 길면 조항 설명 문장/일반 본문으로 본다.
_TITLE_MAX_LEN = 40
_ARABIC_TITLE_MAX_LEN = 20
# 아라비아 절이 진짜 최상위 구조이려면 가나다 하위항목이 최소 이만큼 뒤따라야 한다.
_MIN_SUBITEMS = 2
# 최상위 구조로 신뢰하기 위한 최소 절 개수. 1로 낮춘 이유는 위 모듈 docstring
# "최소 절 개수" 참고 — 번호 1의 마지막 등장을 경계로 먼저 잡으므로, 절이
# 1~2개뿐인 진짜 문서와 우연한 오매칭을 여전히 어느 정도 구분해준다.
_MIN_SECTIONS = 1


@dataclass
class Section:
    heading: str
    body: str


@dataclass
class _Candidate:
    start: int
    end: int
    heading: str
    value: int


def _roman_candidates(text: str) -> list[_Candidate]:
    candidates = []
    for m in _ROMAN_HEADING_RE.finditer(text):
        title = m.group(2).strip()
        if _ROMAN_LISTING_GUARD_RE.match(title) or len(title) > _TITLE_MAX_LEN:
            continue
        candidates.append(_Candidate(m.start(), m.end(), m.group(0).strip(), _ROMAN_VALUES[m.group(1)]))
    for m in _ROMAN_BOXED_RE.finditer(text):
        title = m.group(2).strip()
        if len(title) > _TITLE_MAX_LEN:
            continue
        candidates.append(_Candidate(m.start(), m.end(), f"{m.group(1)} {title}", _ROMAN_VALUES[m.group(1)]))

    candidates.sort(key=lambda c: c.start)
    # 두 패턴이 같은 자리를 다르게 잡아 겹칠 수 있어(드묾) 먼저 나온 것만 남긴다.
    deduped: list[_Candidate] = []
    last_end = -1
    for c in candidates:
        if c.start < last_end:
            continue
        deduped.append(c)
        last_end = c.end
    return deduped


def _chapter_candidates(text: str) -> list[_Candidate]:
    return [_Candidate(m.start(), m.end(), m.group(0).strip(), int(m.group(1))) for m in _CHAPTER_HEADING_RE.finditer(text)]


def _circled_candidates(text: str) -> list[_Candidate]:
    candidates = []
    for m in _CIRCLED_PUA_RE.finditer(text):
        title = m.group(2).strip()
        if len(title) > _TITLE_MAX_LEN:
            continue
        value = ord(m.group(1)) - _CIRCLED_PUA_BASE
        digit = _CIRCLED_DIGITS[value - 1] if 1 <= value <= len(_CIRCLED_DIGITS) else f"{value}."
        candidates.append(_Candidate(m.start(), m.end(), f"{digit} {title}", value))
    return candidates


def _corroborated_arabic_candidates(text: str) -> list[_Candidate]:
    """가./나./다. 하위 항목이 뒤따르는 아라비아 절만 최상위 구조로 신뢰한다."""
    matches = list(_ARABIC_TOP_RE.finditer(text))
    candidates = []
    for i, m in enumerate(matches):
        title = m.group(2).strip()
        if len(title) > _ARABIC_TITLE_MAX_LEN:
            continue
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end() : end]
        if len(_KOREAN_LETTER_ITEM_RE.findall(body)) >= _MIN_SUBITEMS:
            candidates.append(_Candidate(m.start(), m.end(), m.group(0).strip(), int(m.group(1))))
    return candidates


def _sections_from_candidates(text: str, candidates: list[_Candidate]) -> list[Section]:
    """candidates를 "번호 1이 마지막으로 나오는 위치"부터 절로 나눈다."""
    if not candidates:
        return []

    first_positions = [c.start for c in candidates if c.value == 1]
    boundary = first_positions[-1] if first_positions else candidates[0].start

    body = [c for c in candidates if c.start >= boundary]
    if len(body) < _MIN_SECTIONS:
        return []

    sections = []
    for i, c in enumerate(body):
        end = body[i + 1].start if i + 1 < len(body) else len(text)
        sections.append(Section(heading=c.heading, body=text[c.end : end].strip("\n")))
    return sections


def split_sections(text: str) -> list[Section]:
    """문서를 최상위 절 단위로 나눈다.

    로마숫자(같은 줄/박스형 제목틀 모두) → 제N장 → 원문자(PUA 글리프) → 아라비아숫자
    (가나다 하위항목으로 검증됨) 순으로 시도하고, 넷 다 신뢰할 만한 구조를 못 찾으면
    전체 텍스트를 절 1개(heading="")로 반환한다(fail-open) — 어설프게 쪼개서 조항
    문장을 "절"로 오인하는 것보다, 통째로 넘겨서 호출측이 원문 그대로 보는 편이 낫다.
    """
    sections = _sections_from_candidates(text, _roman_candidates(text))
    if sections:
        return sections

    sections = _sections_from_candidates(text, _chapter_candidates(text))
    if sections:
        return sections

    sections = _sections_from_candidates(text, _circled_candidates(text))
    if sections:
        return sections

    sections = _sections_from_candidates(text, _corroborated_arabic_candidates(text))
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
