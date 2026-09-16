"""첨부파일 원문에서 "입찰 참가자격" 절을 찾아 항목 단위로 나눈다.

API의 면허제한정보가 비어 있을 때(예: 발주기관이 나라장터에 구조화된 형태로
등록하지 않은 경우 — 부안청자박물관 공고(R26BK01719858)로 실측 확인함) 사람이
원문으로 직접 자격요건을 확인해야 한다. 표를 파싱하는 게 아니라, 절 전체와
번호/기호가 붙은 개별 항목을 나눠서 읽기 쉽게 만드는 정도다.

실측한 네 관행을 지원한다:
  - 지자체식: "5. 입찰 참가자격" 아래 "가. / 나. / 다. ..." (정선군 공고문)
  - 조달청식: "3. 입찰참가자격" 아래 "3.1. / 3.2. / 3.3. ..." (세종 입찰설명서)
  - "가." 하나 아래 실제 요건은 "1) / 2) / 3) ..."로 나열 (정선군 제안요청서,
    부안청자박물관 공고 첨부파일에서도 같은 형태 확인)
  - "o" / "○" 불릿으로 나열 (실측: 두바이 의료기기전시회 한국관 공고문)
넷 다 아니면 절 전체를 항목 1개로 반환한다 (fail-open — 잘못 쪼개느니
통째로 보여주는 편이 낫다).

한계: PDF는 문단 구분이 없어서(페이지 레이아웃 기준으로만 줄바꿈됨) 다음
절 제목이 이전 문장 끝에 붙어버리는 경우가 있고, 그러면 절 경계를 못 찾아
다음 절까지 통째로 딸려온다 (실측: 세종 공사입찰설명서 PDF). 같은 공고에
HWPX본이 있으면 그쪽이 훨씬 안정적이다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 　(전각 공백)은 HWP류 문서에서 들여쓰기로 흔히 쓰인다(실측: 두바이
# 의료기기전시회 한국관 공고문의 "o" 불릿 들여쓰기) — 일반 스페이스/탭과
# 같이 줄 앞머리 공백으로 취급해야 항목을 놓치지 않는다.

# 줄 끝을 요구하지 않는다 — "4. 입찰참가자격 : 아래 자격을 모두 충족…"처럼
# 콜론 뒤에 같은 줄에 안내문이 붙는 문서가 있다(실측: 두바이 의료기기전시회
# 한국관 공고문). 그 안내문은 본문 첫 줄로 들어갈 뿐이라 판정에는 지장 없다.
_HEADING_RE = re.compile(r"^[ \t\u3000]*\d{1,2}\.[ \t\u3000]*입찰[ \t\u3000]*참가[ \t\u3000]*자격[ \t\u3000]*[:：]?", re.MULTILINE)

# "6. 입찰내용"처럼 새 최상위 절이 시작되는 줄. "3.1. ..."(소항목)과 구분하기
# 위해, 번호 뒤에 공백이 최소 1개 있고 그다음이 숫자가 아니어야 한다
# ("3.1."은 번호 다음이 공백 없이 바로 숫자라 여기 안 걸림).
_NEXT_TOP_HEADING_RE = re.compile(r"^[ \t\u3000]*\d{1,2}\.[ \t\u3000]+[^\d\s]", re.MULTILINE)

_KOREAN_LETTER_ITEM_RE = re.compile(r"^[ \t\u3000]*([가나다라마바사아자차카타파하])\.[ \t\u3000]*", re.MULTILINE)
_DECIMAL_ITEM_RE = re.compile(r"^[ \t\u3000]*(\d{1,2}\.\d{1,2}\.)[ \t\u3000]*", re.MULTILINE)
# "가." 하나 아래 "1) 2) 3) ..."로 개별 요건을 나열하는 문서도 있다 (실측:
# 정선군 제안요청서 — 요건 자체는 이 번호 목록에 있고 "가."는 도입 문장뿐).
_NUMBERED_PAREN_ITEM_RE = re.compile(r"^[ \t\u3000]*(\d{1,2})\)[ \t\u3000]*", re.MULTILINE)
_BULLET_ITEM_RE = re.compile(r"^[ \t\u3000]*[o○][ \t\u3000]+", re.MULTILINE)


@dataclass
class QualificationSection:
    heading: str
    body: str
    items: list[str]


def find_qualification_section(text: str) -> QualificationSection | None:
    """원문에서 "N. 입찰(참가)자격" 절을 찾아 다음 최상위 절 직전까지 잘라낸다."""
    match = _HEADING_RE.search(text)
    if not match:
        return None

    start = match.end()
    next_heading = _NEXT_TOP_HEADING_RE.search(text, pos=start)
    end = next_heading.start() if next_heading else len(text)
    body = text[start:end].strip("\n")

    return QualificationSection(heading=match.group(0).strip(), body=body, items=split_items(body))


def split_items(body: str) -> list[str]:
    """절 본문을 항목 단위로 나눈다. 아는 패턴이 없으면 통째로 1개 항목."""
    for pattern in (_KOREAN_LETTER_ITEM_RE, _DECIMAL_ITEM_RE, _NUMBERED_PAREN_ITEM_RE, _BULLET_ITEM_RE):
        markers = list(pattern.finditer(body))
        if len(markers) < 2:
            continue
        items = []
        for i, marker in enumerate(markers):
            end = markers[i + 1].start() if i + 1 < len(markers) else len(body)
            item = body[marker.start() : end].strip()
            if item:
                items.append(item)
        return items

    stripped = body.strip()
    return [stripped] if stripped else []
