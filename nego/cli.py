"""실행 진입점.

    python -m nego                    # 최근 LOOKBACK_DAYS일치 수집 → 리포트
    python -m nego --days 7           # 기간 지정
    python -m nego --from-store       # API 호출 없이 저장된 원문으로 재필터링
    python -m nego --verify           # 응답 필드명 진단 (필드가 비어 보일 때)
    python -m nego --fetch-attachment-text  # 후보 공고 첨부파일 텍스트 추출(원문 대조용)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from . import fields as F
from . import qualify
from .config import ConfigError, load_config, redact
from .http_client import ApiError, DataGoKrClient
from .pipeline import build_candidates, run
from .report import render_console, save_reports
from .repository import JsonlRepository


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )


def _verify_api(config) -> int:
    """각 오퍼레이션을 소량 호출해 실제 응답 키를 그대로 출력한다."""
    client = DataGoKrClient(config.api)
    client.config.num_of_rows = 5
    client.config.max_pages = 1
    now = datetime.now()
    begin = now.replace(day=1).strftime("%Y%m%d0000")
    end = now.strftime("%Y%m%d%H%M")

    targets = [(f"본공고/{w}", F.BID_NOTICE_BASE_URL, op) for w, op in F.BID_NOTICE_OPERATIONS.items()]
    targets.append(("면허제한정보", F.BID_NOTICE_BASE_URL, F.LICENSE_LIMIT_OPERATION))
    targets.append(("참가가능지역", F.BID_NOTICE_BASE_URL, F.REGION_LIMIT_OPERATION))

    for label, base_url, operation in targets:
        print(f"\n── {label} ({operation}) " + "─" * 30)
        try:
            items = client.fetch_all_pages(
                base_url, operation, {"inqryDiv": "1", "inqryBgnDt": begin, "inqryEndDt": end}, label
            )
        except ApiError as err:
            print(f"  실패: {redact(str(err), [config.api.service_key])}")
            continue
        if not items:
            print("  응답 0건")
            continue
        print(f"  {len(items)}건 수신. 첫 건의 필드 키:")
        for key in sorted(items[0].keys()):
            value = str(items[0][key])[:40]
            print(f"    {key:32s} = {value}")
    return 0


def _save_to_supabase(candidates, stats) -> None:
    """Supabase 환경변수가 있으면 저장한다. 없으면 조용히 건너뛴다.

    저장 실패가 전체 실행을 죽이지 않게 한다 — 리포트는 이미 만들어졌고,
    다음 실행에서 같은 공고를 upsert하면 복구되기 때문이다.
    """
    from .supabase_repo import SupabaseConfig, SupabaseError, SupabaseRepository

    config = SupabaseConfig.from_env()
    if config is None:
        logging.info("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY 없음 → Supabase 저장 건너뜀")
        return

    try:
        saved = SupabaseRepository(config).save(candidates, stats.rejected)
        print(f"Supabase 저장: {saved}행 → \"{config.table}\" (후보 {len(candidates)} / 제외 {len(stats.rejected)})")
    except SupabaseError as err:
        logging.error("Supabase 저장 실패 (리포트는 정상 생성됨): %s", err)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nego", description="나라장터 「협상에 의한 계약」 공고 추출")
    parser.add_argument("--days", type=int, help="조회 기간(일). 기본값은 LOOKBACK_DAYS 환경변수")
    parser.add_argument("--from-store", action="store_true", help="API 호출 없이 저장된 원문으로 재필터링")
    parser.add_argument("--verify", action="store_true", help="응답 필드명 진단")
    parser.add_argument("--store", default="data/notices.jsonl", help="원문 저장 경로 (로컬 JSONL)")
    parser.add_argument("--output", help="리포트 출력 폴더 (기본 output/)")
    parser.add_argument(
        "--no-supabase",
        action="store_true",
        help="Supabase 저장을 건너뛴다 (환경변수가 있어도)",
    )
    parser.add_argument(
        "--fetch-attachment-text",
        action="store_true",
        help="후보 공고의 첨부파일(HWP/HWPX/PDF)을 내려받아 텍스트를 추출한다 (지역제한/면허제한/공동수급 원문 대조용)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    _setup_logging(args.verbose)

    try:
        config = load_config()
    except ConfigError as err:
        print(f"설정 오류: {err}", file=sys.stderr)
        return 1

    if args.days:
        config.lookback_days = args.days
    if args.output:
        config.output_dir = Path(args.output)

    if args.verify:
        if not config.api.service_key:
            print("NARA_SERVICE_KEY 환경변수가 필요합니다.", file=sys.stderr)
            return 1
        return _verify_api(config)

    now = datetime.now()
    repo = JsonlRepository(Path(args.store))

    try:
        if args.from_store:
            notices = repo.all()
            if not notices:
                print(f"저장된 원문이 없습니다: {args.store}", file=sys.stderr)
                return 1
            logging.info("저장소에서 %d건을 읽었습니다 (API 호출 없음)", len(notices))
            from .pipeline import RunStats

            stats = RunStats(fetched=len(notices))
            candidates = build_candidates(notices, config, {}, {}, now, stats)
        else:
            if not config.api.service_key:
                print("NARA_SERVICE_KEY 환경변수가 필요합니다.", file=sys.stderr)
                return 1
            candidates, stats, notices = run(config, now)
            added = repo.upsert(notices)
            logging.info("저장 완료: 신규 %d건 / 전체 %d건", added, len(notices))
    except ApiError as err:
        print(redact(str(err), [config.api.service_key]), file=sys.stderr)
        return 1

    print(render_console(candidates, stats))

    if not args.no_supabase:
        _save_to_supabase(candidates, stats)

    paths = save_reports(candidates, stats, config.output_dir, now)
    for kind, path in paths.items():
        print(f"{kind.upper()} 저장: {path}")

    if args.fetch_attachment_text:
        from .attachments import save_attachment_texts

        att_stats = save_attachment_texts(candidates, config.output_dir)
        print(
            f"첨부파일 텍스트 추출: 시도 {att_stats['attempted']}건 "
            f"→ 성공 {att_stats['ok']} / 실패 {att_stats['failed']}"
            f" · 참가자격 절 발견 {att_stats['qualification_found']}건"
            f" (저장 위치: {config.output_dir / 'attachment_text'})"
        )

    # 일부 조회가 실패했으면 종료코드 2로 구분한다 (CI에서 성공/부분성공 구분).
    return 2 if stats.failed_operations else 0


if __name__ == "__main__":
    raise SystemExit(main())
