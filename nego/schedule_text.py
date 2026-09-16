"""첨부파일 원문에서 제출기한/마감일시를 찾는다.

API의 자격등록마감·공동수급협정마감·입찰마감 세 필드가 전부 비어 리포트에
"일정 미상"으로 뜨는 공고가 있다(실측: 경상남도관광재단 "「K-거상」기획전시관
및 상설주제관 콘텐츠 개선·개발 용역" R26BK01707504 — 세 필드 모두 없지만,
첨부 제안요청서에는 "제출기간 : 2026. 9. 14.(월) 9:00~18:00"가 명시돼
있었다). 이럴 때만(API 필드가 전부 비었을 때만) 보충하는 최후 수단이다 —
API 값이 하나라도 있으면 이 모듈은 아예 쓰이지 않는다
(`screen.Schedule.earliest` 참고).

라벨(제출기한/제출기간 등) 뒤 가까이(문자 수 기준)에서 날짜를, 그 뒤
가까이에서 시각(단일 또는 "시작~종료" 범위)을 찾는다. 범위면 종료 시각을
마감으로 본다. 시각을 못 찾으면 그날 23:59을 마감으로 본다. 라벨 하나가
날짜를 못 찾으면(예: 절 제목만 있고 실제 값은 몇 줄 뒤) 다음 라벨 매치로
넘어간다 — fail-open, 못 찾으면 그냥 None이라 기존 "일정 미상" 그대로다.
"""

from __future__ import annotations

import re
from datetime import datetime

_DEADLINE_LABEL_RE = re.compile(
    r"(?:제안서\s*)?제출\s*(?:기한|기간|일시)|마감\s*(?:일시|일자|기한)?|접수\s*마감"
)
_DATE_RE = re.compile(
    r"(?P<year>20\d{2})\s*[.\-년]\s*(?P<month>1[0-2]|0?[1-9])\s*[.\-월]\s*(?P<day>3[01]|[12]\d|0?[1-9])\s*일?\.?"
)
_TIME_RE = re.compile(
    r"(?P<h1>[01]?\d|2[0-3])\s*[:시]\s*(?P<m1>[0-5]?\d)\s*분?"
    r"(?:\s*[~∼-]\s*(?P<h2>[01]?\d|2[0-3])\s*[:시]\s*(?P<m2>[0-5]?\d)\s*분?)?"
)
# 라벨→날짜, 날짜→시각까지 허용하는 거리(문자 수). 절 제목처럼 라벨만 있고
# 값이 멀리 떨어진 경우를 걸러내는 용도라 넉넉하게 잡을 필요는 없다.
_LABEL_TO_DATE_WINDOW = 20
_DATE_TO_TIME_WINDOW = 25


def extract_deadline(text: str) -> datetime | None:
    """제출기한/마감 표기를 찾아 datetime으로 반환한다. 못 찾으면 None."""
    for label_match in _DEADLINE_LABEL_RE.finditer(text):
        date_match = _DATE_RE.search(text, label_match.end())
        if not date_match or date_match.start() - label_match.end() > _LABEL_TO_DATE_WINDOW:
            continue

        try:
            year = int(date_match.group("year"))
            month = int(date_match.group("month"))
            day = int(date_match.group("day"))
        except (TypeError, ValueError):
            continue

        hour, minute = 23, 59
        time_match = _TIME_RE.search(text, date_match.end())
        if time_match and time_match.start() - date_match.end() <= _DATE_TO_TIME_WINDOW:
            if time_match.group("h2") is not None:
                hour, minute = int(time_match.group("h2")), int(time_match.group("m2"))
            else:
                hour, minute = int(time_match.group("h1")), int(time_match.group("m1"))

        try:
            return datetime(year, month, day, hour, minute)
        except ValueError:
            continue
    return None
