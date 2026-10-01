"""첨부 공고문·제안요청서·과업지시서에서 "사업 범위"(과업 범위) 목록을 찾아 항목으로 꺼낸다.

웹앱 검토 필요 카드의 "과업 유사" 팝업에 공고가 실제로 맡기는 일을 보여주려고 쓴다(2026-10-01 사용자 요청 —
태그 근거 줄을 발췌했더니 목차·안내문·요건문이 섞였다). 문서는 대개 이렇게 적는다:

    마. 사업내용의 범위                 ㅇ 사업 범위
      1) 체험 공간 연출 기획 …            - 전시 주제에 따른 전시 연출·제작·시공 일체
      2) 체험물 설계 및 제작・설치 …       · 전시 공간 구성 계획 및 설계도서 작성
    바. (다음 항목)                      ㅇ 사업기간 : …

제목 줄을 찾고, 그 아래 줄을 같은 종류의 다음 머리표(바. / ㅇ)나 더 바깥 제목이 나올 때까지 모은다.
"범위"가 든 제목을 "과업 내용"·"주요 과업"보다 먼저 찾고, 목차(짧은 제목만 이어짐)는 건너뛴다.
"""

from __future__ import annotations

import re

# 머리표 — 종류별로 묶어 "같은 종류의 다음 머리표"에서 끊는다
_MARKERS = [
    ("roman", r"[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\s*\."),
    ("num", r"\d{1,2}\s*\."),
    ("hangul", r"[가-하]\s*\."),
    ("paren", r"\d{1,2}\s*\)"),
    ("hparen", r"[가-하]\s*\)"),
    ("circled", r"[①-⑳]"),
    ("box", r"[□■]"),
    ("o", r"[ㅇ○◦❍●]"),
    ("dash", r"[-–―]"),
    ("dot", r"[·•ㆍ∙▪※]"),
]
_MARKER_RE = re.compile(r"^\s*(?:" + "|".join(f"(?P<{k}>{p})" for k, p in _MARKERS) + r")\s*")
# 제목 — "사업 범위", "과업의 범위", "사업내용의 범위", "과업 범위 및 내용" / 그다음 우선순위로 "주요 과업", "과업 내용"
_HEADINGS = [
    re.compile(r"^(?:사업|과업|용역)\s*(?:내용\s*의\s*|의\s*)?범위(?:\s*및\s*내용)?\s*[:：]?$"),
    re.compile(r"^(?:주요\s*(?:과업|사업)\s*(?:내용)?|과업\s*(?:수행\s*)?내용|사업\s*내용|세부\s*과업\s*(?:내용)?)\s*[:：]?$"),
]
MAX_ITEMS = 12
# 과업을 말하는 동사 — 이게 하나도 없는 목록은 위치·안내문("범위는 제안요청서에 의한다")이라 건너뛴다
_TASK_WORD = re.compile(r"제작|설치|설계|시공|구축|개발|조성|기획|연출|구성|계획|수립|조사|구매|납품|운영|촬영|편집|디자인|리모델링|개선|교체|철거")


def _marker(line: str) -> tuple[str | None, str]:
    """(머리표 종류, 머리표 뗀 본문)."""
    m = _MARKER_RE.match(line)
    if not m:
        return None, line.strip()
    kind = next(k for k, v in m.groupdict().items() if v)
    return kind, line[m.end():].strip()


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _collect(lines: list[str], start: int) -> list[str]:
    head_kind, _ = _marker(lines[start])
    head_indent = _indent(lines[start])
    items: list[str] = []
    for line in lines[start + 1:start + 1 + 60]:
        if not line.strip():
            continue
        kind, body = _marker(line)
        # 같은 종류의 다음 머리표, 또는 제목보다 바깥(들여쓰기 같거나 얕은) 머리표 → 범위 끝
        if kind and ((head_kind and kind == head_kind) or (_indent(line) <= head_indent and kind in ("roman", "num", "hangul", "box", "o"))):
            break
        if not kind and items and _indent(line) <= head_indent and len(body) < 30 and not body.endswith((")", "다", "함")):
            break  # 머리표 없는 짧은 줄이 제목 높이에 나오면 다음 절 제목으로 본다
        if kind or not items:
            if body:
                items.append(body)
        else:
            items[-1] += " " + body  # 줄바꿈으로 갈린 항목의 이어지는 줄
        if len(items) >= MAX_ITEMS:
            break
    # 한글 문서의 기호 글꼴 머리표(사용자 정의 영역 문자, 예: U+F06D)는 깨진 글자로 보이므로 지운다
    items = [re.sub(r"\s+", " ", re.sub(r"[\ue000-\uf8ff]", " ", i)).strip() for i in items]
    return [i for i in items if len(re.sub(r"[^가-힣]", "", i)) >= 4]


def _looks_like_toc(items: list[str]) -> bool:
    """목차 — 항목이 짧은 제목뿐(또는 끝에 쪽번호)."""
    short = sum(1 for i in items if len(i) < 14 or re.search(r"\s\d{1,3}$", i))
    return short >= max(2, len(items) * 0.6)


def extract_scope_items(text: str) -> list[str]:
    """문서(여러 첨부를 이어 붙인 원문도 됨)에서 사업 범위 항목 목록. 못 찾으면 빈 목록."""
    if not text:
        return []
    lines = [ln for ln in text.splitlines() if not ln.strip().startswith("===")]
    for heading in _HEADINGS:
        for i, line in enumerate(lines):
            _, body = _marker(line)
            if len(body) > 24 or not heading.match(re.sub(r"\s+", " ", body)):
                continue
            items = _collect(lines, i)
            if len(items) >= 2 and not _looks_like_toc(items) and any(_TASK_WORD.search(x) for x in items):
                return items
    return []
