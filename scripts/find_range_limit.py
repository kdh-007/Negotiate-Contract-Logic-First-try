"""면허제한정보/참가가능지역 API가 허용하는 조회기간 상한을 찾는다.

실측: 30일은 통과, 180일은 resultCode=07("입력범위값 초과 에러")로 거부됨.
정확한 경계를 찾기 위해 후보 기간 여러 개를 한 번의 실행 안에서 순서대로
테스트한다 (매번 워크플로를 새로 트리거하지 않기 위함). 각 테스트는
numOfRows=1, max_pages=1로 첫 페이지만 요청해서 resultCode만 빠르게 확인한다
— 조회기간이 유효한지 여부는 페이지네이션 전에 서버가 판단하므로 이걸로 충분하다.

사용법:
    NARA_SERVICE_KEY=... python scripts/find_range_limit.py [--days 15,30,45,60,75,90,120,150,180]
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
from nego.http_client import ApiConfig, ApiError, ApiResultError, DataGoKrClient  # noqa: E402

DEFAULT_DAYS = [15, 30, 45, 60, 75, 90, 120, 150, 180]


def probe(client: DataGoKrClient, operation: str, days: int, now: datetime) -> str:
    """이 기간으로 첫 페이지만 요청해서 결과를 짧게 돌려준다."""
    begin = (now - timedelta(days=days)).strftime("%Y%m%d0000")
    end = now.strftime("%Y%m%d%H%M")
    try:
        client.fetch_all_pages(
            F.BID_NOTICE_BASE_URL, operation, {"inqryDiv": "1", "inqryBgnDt": begin, "inqryEndDt": end}, operation
        )
        return "OK"
    except ApiResultError as err:
        return f"거부 (resultCode={err.result_code})"
    except ApiError as err:
        return f"오류: {err}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", default=",".join(str(d) for d in DEFAULT_DAYS), help="쉼표로 구분한 후보 일수 목록")
    args = parser.parse_args()
    day_values = sorted({int(x) for x in args.days.split(",") if x.strip()})

    service_key = os.environ.get("NARA_SERVICE_KEY", "").strip()
    if not service_key:
        print("NARA_SERVICE_KEY 환경변수가 필요합니다.", file=sys.stderr)
        return 1

    # 첫 페이지만 보면 되므로 numOfRows/max_pages를 최소로 줄여 빠르게 확인한다.
    client = DataGoKrClient(ApiConfig(service_key=service_key, timeout_sec=30.0, max_retries=1, num_of_rows=1, max_pages=1))
    now = datetime.now()

    targets = [
        ("면허제한정보", F.LICENSE_LIMIT_OPERATION),
        ("참가가능지역", F.REGION_LIMIT_OPERATION),
    ]

    last_ok: dict[str, int] = {}
    first_fail: dict[str, int] = {}

    for label, operation in targets:
        print(f"\n── {label} ──")
        for days in day_values:
            result = probe(client, operation, days, now)
            print(f"  {days:4d}일: {result}")
            if result == "OK":
                last_ok[label] = days
            elif label not in first_fail:
                first_fail[label] = days

    print("\n=== 요약 ===")
    for label, _ in targets:
        ok = last_ok.get(label, "?")
        fail = first_fail.get(label, "?")
        print(f"{label}: 테스트한 값 중 마지막 통과 {ok}일 / 첫 거부 {fail}일")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
