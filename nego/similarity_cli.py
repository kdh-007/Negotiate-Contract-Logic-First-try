"""과거 실적 유사도 매칭 프로토타입 — 공고 1건을 손으로 넣어 회사 과거 실적
(`config/past_projects.json`, 실측 81건)과 비교해본다.

    python -m nego.similarity_cli --title "OO 전시관 미디어아트 콘텐츠 제작" \
        --budget 500000000 --industry 실내건축공사업 --top 5

로드맵(narabid.md) 4~5단계 초기 프로토타입이다. 일일 수집 파이프라인(`nego/cli.py`)
에는 아직 연결하지 않았다 — CLAUDE.md 2026-09-18 메모대로 신규 공고·과거실적
데이터 연동 이전 단계라, 지금은 담당자가 원하는 공고 1건을 직접 입력해 결과를
확인하는 용도다. 핵심 로직은 전부 `nego/similarity.py`의 `rank()`에 있고, 이
파일은 그걸 감싼 얇은 CLI 진입점일 뿐이다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .models import Notice
from .similarity import load_past_projects_file, rank


def _build_notice(args: argparse.Namespace) -> Notice:
    return Notice(
        work_type="용역",
        notice_no="ADHOC",
        notice_ord="000",
        title=args.title,
        estimated_price=args.budget,
        industry_text=f"[{args.industry}]" if args.industry else None,
        product_class_no=args.product_code,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m nego.similarity_cli",
        description="새 공고 1건을 config/past_projects.json의 과거 실적과 비교해 유사도 순위를 매긴다.",
    )
    parser.add_argument("--title", required=True, help="공고명")
    parser.add_argument("--budget", type=float, help="추정가격/배정예산(원)")
    parser.add_argument("--industry", help="업종명(공사/용역 공고, 예: 실내건축공사업)")
    parser.add_argument("--product-code", help="세부품명번호(물품 공고)")
    parser.add_argument("--top", type=int, default=5, help="상위 몇 건까지 볼지 (기본 5)")
    parser.add_argument("--past-projects", default="config/past_projects.json", help="과거 실적 파일 경로")
    args = parser.parse_args(argv)

    past_projects = load_past_projects_file(Path(args.past_projects))
    if not past_projects:
        print(f"과거 실적을 읽지 못했습니다: {args.past_projects}", file=sys.stderr)
        return 1

    notice = _build_notice(args)
    matches = rank(notice, past_projects, top_n=args.top)

    print(f"공고: {notice.title}")
    if notice.budget:
        print(f"  예산: {notice.budget:,.0f}원")
    if notice.industry_text:
        print(f"  업종: {notice.industry_text}")
    if notice.product_class_no:
        print(f"  세부품명번호: {notice.product_class_no}")

    print(f"\n과거 실적 {len(past_projects)}건 중 상위 {len(matches)}건:\n")
    for i, m in enumerate(matches, 1):
        print(f"{i}. {m.score:5.1f}점  {m.past.title}")
        print(f"     업역 {m.structural_score:.2f} · 규모 {m.track_record_score:.2f} · 내용 {m.text_score:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
