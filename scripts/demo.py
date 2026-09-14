"""API 키 없이 파이프라인 전 구간을 돌려보는 데모.

    python scripts/demo.py

픽스처(실제 응답 형식을 따른 가상 공고)로 수집 이후 전 단계를 실행하고
콘솔 리포트 + HTML/CSV 파일을 만든다. 배포 전 동작 확인용.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nego import qualify  # noqa: E402
from nego.config import load_config  # noqa: E402
from nego.models import notice_from_raw  # noqa: E402
from nego.pipeline import RunStats, build_candidates  # noqa: E402
from nego.report import render_console, save_reports  # noqa: E402
from nego.repository import JsonlRepository  # noqa: E402
from tests import fixtures  # noqa: E402

NOW = datetime(2026, 9, 14, 9, 0)


def main() -> int:
    config = load_config()
    # 픽스처 제목에 맞춰 키워드만 넓혀 준다 (config 원본은 수정하지 않음).
    config.screen.keywords = list(config.screen.keywords) + ["전시물", "전시"]

    notices = [notice_from_raw(raw, "용역") for raw in fixtures.service_notices()]
    license_groups = qualify.group_license_rows(fixtures.license_rows())

    stats = RunStats(fetched=len(notices))
    candidates = build_candidates(notices, config, license_groups, {}, NOW, stats)

    print(render_console(candidates, stats))

    repo = JsonlRepository(ROOT / "data" / "demo_notices.jsonl")
    added = repo.upsert(notices)
    print(f"저장소: 신규 {added}건 기록 (data/demo_notices.jsonl)")

    paths = save_reports(candidates, stats, ROOT / "output", NOW)
    for kind, path in paths.items():
        print(f"{kind.upper()} 저장: {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
