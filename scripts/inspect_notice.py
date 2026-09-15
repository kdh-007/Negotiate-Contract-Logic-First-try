"""특정 공고 1건의 면허제한정보·참가가능지역 원본 응답을 그대로 찍어본다.

목적: "자격정보 없음(판정 보류)"으로 나온 공고가 실제로 API에 데이터가 없는 건지,
아니면 필드명이 달라서 우리 파싱이 놓친 건지 구분하기 위한 진단 도구다.

현재 파싱 로직(fields.py의 LICENSE_LIMIT_FIELDS)이 아는 필드명으로만 찾지 않고,
응답에 들어있는 **모든 값**에서 공고번호 문자열을 검색한다 — 필드명이 완전히
바뀌어도 값 자체에 공고번호가 남아있으면 여기서 잡힌다.

사용법:
    NARA_SERVICE_KEY=... python scripts/inspect_notice.py R26BK01719858 [--days 180]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nego import fields as F  # noqa: E402
from nego.http_client import ApiConfig, ApiError, DataGoKrClient  # noqa: E402


def _contains_notice_no(item: dict, notice_no: str) -> bool:
    return any(notice_no in str(v) for v in item.values())


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

    targets = [
        ("면허제한정보", F.LICENSE_LIMIT_OPERATION, F.LICENSE_LIMIT_FIELDS),
        ("참가가능지역", F.REGION_LIMIT_OPERATION, F.REGION_LIMIT_FIELDS),
    ]

    found_any = False
    any_call_failed = False
    for label, operation, field_map in targets:
        print(f"── {label} ({operation}) " + "─" * 40)
        try:
            raw_items = client.fetch_all_pages(
                F.BID_NOTICE_BASE_URL, operation, {"inqryDiv": "1", "inqryBgnDt": begin, "inqryEndDt": end}, label
            )
        except ApiError as err:
            print(f"  조회 자체가 실패했습니다 (데이터 유무를 판단할 수 없음): {err}")
            any_call_failed = True
            continue

        print(f"  기간 내 전체 수신: {len(raw_items)}건")

        # 1) 우리가 아는 필드명(notice_no 후보)으로 정상 매칭되는지
        known_field_matches = [
            item for item in raw_items if F.pick_by(item, field_map, "notice_no") == notice_no
        ]
        print(f"  알려진 notice_no 필드로 매칭: {len(known_field_matches)}건")

        # 2) 필드명과 무관하게, 값 어딘가에 공고번호가 통째로 들어있는지 (필드 드리프트 대비)
        loose_matches = [item for item in raw_items if _contains_notice_no(item, notice_no)]
        print(f"  값 전체 검색으로 매칭(필드명 무관): {len(loose_matches)}건")

        if loose_matches:
            found_any = True
            for item in loose_matches:
                print("  ── 매칭된 원본 행 ──")
                print(json.dumps(item, ensure_ascii=False, indent=2))
        print()

    if any_call_failed:
        print(
            "결론: 판단 불가 — 위에서 최소 한 API 호출 자체가 실패했습니다 (네트워크/타임아웃).\n"
            "이 상태에서 '데이터 없음'이라고 단정하면 안 됩니다. API가 정상 응답할 때 다시 실행해야 합니다."
        )
        return 2
    if not found_any:
        print(
            "결론: 두 API 모두 정상 응답했고, 값 전체를 뒤져봐도 이 공고번호가 전혀 없습니다.\n"
            "→ 필드명 드리프트가 아니라, 발주기관이 이 공고에 대해 구조화된 "
            "면허제한/지역제한 정보를 나라장터에 등록하지 않은 것으로 보입니다."
        )
    else:
        print("결론: 원본 응답에 이 공고번호가 존재합니다 — 파싱 단계(필드명 후보)를 점검해야 합니다.")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("notice_no", help="공고번호 (bidNtceNo), 예: R26BK01719858")
    parser.add_argument("--days", type=int, default=180, help="조회 기간(일, 기본 180일)")
    args = parser.parse_args()
    return inspect(args.notice_no, args.days)


if __name__ == "__main__":
    raise SystemExit(main())
