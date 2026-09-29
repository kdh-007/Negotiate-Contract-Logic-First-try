"""수집 없이 LLM 유사도 판정만 시험한다 (나라장터 API 키 불필요).

    # 공고 제목만으로
    python scripts/llm_similarity_test.py --title "청소년 독도디지털체험관 전시체험시설 제작·설치"

    # 여러 건 + 제안요청서 등 본문 텍스트 파일까지
    python scripts/llm_similarity_test.py --notices my_notices.json

my_notices.json 형식 (title만 필수):
    [
      {"title": "...", "noticeNo": "R26BK...", "institution": "...", "productClassName": "조형물",
       "productClassNo": "6012100201", "budget": 300000000, "textFile": "rfp.txt"}
    ]

과거 실적은 기본으로 config/past_projects.json을 쓴다("(예시)" 항목은 제외).
다른 파일로 시험하려면 --past 경로를 준다. ANTHROPIC_API_KEY 환경변수가 필요하다.
결과는 콘솔에 요약되고 output/llm_similarity_*.md / .json으로 저장된다.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nego import llm_similarity  # noqa: E402
from nego.similarity import load_past_projects_file  # noqa: E402


def _load_notices(args) -> list[llm_similarity.NoticeInput]:
    notices = [llm_similarity.NoticeInput(title=t) for t in args.title or []]
    if args.notices:
        base = Path(args.notices).resolve().parent
        for item in json.loads(Path(args.notices).read_text(encoding="utf-8")):
            text = ""
            if item.get("textFile"):
                text = (base / item["textFile"]).read_text(encoding="utf-8")
            notices.append(
                llm_similarity.NoticeInput(
                    title=item["title"],
                    notice_no=item.get("noticeNo", ""),
                    institution=item.get("institution"),
                    product_class_no=item.get("productClassNo"),
                    product_class_name=item.get("productClassName"),
                    industry_text=item.get("industryText"),
                    budget=item.get("budget"),
                    task_text=text or item.get("text", ""),
                )
            )
    return notices


def main() -> int:
    parser = argparse.ArgumentParser(description="LLM 유사도 판정 단독 시험")
    parser.add_argument("--title", action="append", help="공고 제목 (여러 번 줄 수 있음)")
    parser.add_argument("--notices", help="공고 목록 JSON 파일")
    parser.add_argument("--past", default=str(ROOT / "config" / "past_projects.json"), help="과거 실적 JSON")
    parser.add_argument("--output", default=str(ROOT / "output"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")

    config = llm_similarity.LlmConfig.from_env()
    if config is None:
        print("ANTHROPIC_API_KEY 환경변수가 필요합니다.", file=sys.stderr)
        return 1

    notices = _load_notices(args)
    if not notices:
        print("--title 또는 --notices로 공고를 하나 이상 주세요.", file=sys.stderr)
        return 1

    projects = llm_similarity.usable_past_projects(load_past_projects_file(Path(args.past)))
    if not projects:
        print(f"비교할 과거 실적이 없습니다: {args.past} ('(예시)' 항목만 있거나 비어 있음)", file=sys.stderr)
        return 1

    client = llm_similarity.make_client()
    results = [llm_similarity.judge_notice(client, config, n, projects) for n in notices[: config.max_candidates]]
    print(llm_similarity.render_console(results))
    for kind, path in llm_similarity.save_results(results, Path(args.output), datetime.now()).items():
        print(f"{kind.upper()} 저장: {path}")
    return 1 if all(r.error for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
