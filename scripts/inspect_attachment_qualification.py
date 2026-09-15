"""특정 공고 1건의 첨부파일을 실제로 내려받아 텍스트 추출·참가자격 절 인식 결과를 찍어본다.

목적: "자격정보 없음(판정 보류)"으로 나온 공고가 실제로는 첨부파일 텍스트에
참가자격 요건이 있는 경우, 그 공고의 attachment_text 결과물이 실제로 제대로
만들어지고 있는지(추출 성공 여부·참가자격 절 인식 여부) 직접 확인하기 위한
진단 도구다.

**중요**: `find_qualification_section()`이 절을 찾아 `_참가자격.txt`를 만들어도,
그 결과는 `nego/qualify.py`의 "자격정보 없음" 판정에는 반영되지 않는다 —
그 판정은 오직 면허제한정보 API(`fetch_license_groups`) 결과만 본다.
`_참가자격.txt`는 사람이 참고하는 보조 파일일 뿐, 자동 판정을 바꾸지 않는다
(PoC4에서 다루기로 한 부분). 그래서 이 스크립트가 "절을 찾았다"고 나와도
리포트의 "자격정보 없음" 표시는 그대로 남는 게 현재 설계상 정상이다.

사용법:
    NARA_SERVICE_KEY=... python scripts/inspect_attachment_qualification.py R26BK01719858 [--days 30]
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nego import fields as F  # noqa: E402
from nego.attachments import AttachmentError, extract_text  # noqa: E402
from nego.http_client import ApiConfig, ApiError, DataGoKrClient  # noqa: E402
from nego.qualification_text import find_qualification_section  # noqa: E402

import requests  # noqa: E402


def find_raw_item(client: DataGoKrClient, notice_no: str, begin: str, end: str):
    for work_type, operation in F.BID_NOTICE_OPERATIONS.items():
        try:
            raw_items = client.fetch_all_pages(
                F.BID_NOTICE_BASE_URL, operation, {"inqryDiv": "1", "inqryBgnDt": begin, "inqryEndDt": end}, work_type
            )
        except ApiError as err:
            print(f"  [{work_type}] 조회 실패: {err}")
            continue
        for item in raw_items:
            if F.pick_field(item, "notice_no") == notice_no:
                print(f"  [{work_type}] 매칭된 공고 발견: {F.pick_field(item, 'title')}")
                return item
    return None


def inspect(notice_no: str, days: int) -> int:
    service_key = os.environ.get("NARA_SERVICE_KEY", "").strip()
    if not service_key:
        print("NARA_SERVICE_KEY 환경변수가 필요합니다.", file=sys.stderr)
        return 1

    client = DataGoKrClient(ApiConfig(service_key=service_key, timeout_sec=30.0, max_retries=3))
    now = datetime.now()
    begin = (now - timedelta(days=days)).strftime("%Y%m%d0000")
    end = now.strftime("%Y%m%d%H%M")
    print(f"조회 기간: {begin} ~ {end} ({days}일)\n")

    raw = find_raw_item(client, notice_no, begin, end)
    if raw is None:
        print(f"\n결론: {notice_no} 공고를 이 기간 내 본공고 3종에서 찾지 못했습니다.")
        return 1

    attachments = F.extract_attachments(raw)
    print(f"\n첨부파일 {len(attachments)}건:")
    for att in attachments:
        print(f"  - [{att['seq']}] {att['file_name']} ({att['ext']})")

    if not attachments:
        print("\n결론: 이 공고엔 첨부파일 자체가 없습니다.")
        return 0

    session = requests.Session()
    found_any_section = False
    for att in attachments:
        print(f"\n── {att['file_name']} ──")
        if att["ext"] not in ("hwp", "hwpx", "pdf"):
            print(f"  건너뜀: 지원하지 않는 확장자 (.{att['ext']})")
            continue
        if not att["url"]:
            print("  건너뜀: 다운로드 URL 없음")
            continue
        try:
            res = session.get(att["url"], timeout=30.0)
            res.raise_for_status()
            text = extract_text(res.content, att["ext"])
        except (requests.RequestException, AttachmentError) as err:
            print(f"  실패: {err}")
            continue

        print(f"  추출 성공: {len(text)}자")
        section = find_qualification_section(text)
        if section is None:
            has_substring = "참가자격" in text or "참가 자격" in text
            print(f"  참가자격 절 인식: 실패 (본문에 '참가자격' 문자열 존재 여부: {has_substring})")
            if has_substring:
                idx = text.find("참가자격") if "참가자격" in text else text.find("참가 자격")
                print(f"  주변 원문(디버그, repr): {text[max(0, idx - 40):idx + 60]!r}")
        else:
            found_any_section = True
            print(f"  참가자격 절 인식: 성공 (제목: {section.heading!r}, 항목 {len(section.items)}개)")
            for i, item in enumerate(section.items, 1):
                print(f"    {i}. {item[:80]}")

    print("\n" + "=" * 60)
    if found_any_section:
        print(
            "결론: 첨부파일 텍스트 추출·참가자격 절 인식 자체는 정상 동작합니다.\n"
            "리포트에 '자격정보 없음'이 계속 뜨는 건 파싱 오류가 아니라, 이 값이\n"
            "nego/qualify.py의 면허제한정보 API 판정에만 반영되고 첨부파일 추출\n"
            "결과(_참가자격.txt)는 아직 그 판정에 연결돼 있지 않기 때문입니다."
        )
    else:
        print("결론: 이 공고 첨부파일에서는 참가자격 절을 찾지 못했습니다 — 규칙 보강이 필요할 수 있습니다.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("notice_no", help="공고번호 (bidNtceNo), 예: R26BK01719858")
    parser.add_argument("--days", type=int, default=30, help="조회 기간(일, 기본 30일)")
    args = parser.parse_args()
    return inspect(args.notice_no, args.days)


if __name__ == "__main__":
    raise SystemExit(main())
