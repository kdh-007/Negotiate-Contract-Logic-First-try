"""매일/주간 실행용 조회 기간(`complete_days_window`)과 Supabase 저장 규칙(`rejected_row`) 테스트.

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nego.config import load_config  # noqa: E402
from nego.models import notice_from_raw  # noqa: E402
from nego.pipeline import RunStats, build_candidates, complete_days_window  # noqa: E402
from nego.supabase_repo import rejected_row  # noqa: E402
from tests import fixtures  # noqa: E402

NOW = datetime(2026, 9, 1, 8, 5)


def _candidates(n: int, award: str = fixtures.QUALIFY):
    config = load_config()
    config.screen.keywords = ["전시관"]
    raws = [fixtures.notice(f"R26TG{i:04d}", title=f"○○전시관 전시물 제작설치 {i}", award=award) for i in range(n)]
    stats = RunStats(period_begin=datetime(2026, 8, 31), period_end=datetime(2026, 8, 31, 23, 59))
    candidates = build_candidates([notice_from_raw(r, "용역") for r in raws], config, {}, {}, NOW, stats, {"입찰"})
    return candidates, stats


class TestCompleteDaysWindow(unittest.TestCase):
    def test_daily_window_is_yesterday_whole_day(self):
        begin, end = complete_days_window(datetime(2026, 9, 29, 8, 0), 1)
        self.assertEqual(begin, datetime(2026, 9, 28, 0, 0))
        self.assertEqual(end, datetime(2026, 9, 28, 23, 59))

    def test_consecutive_runs_do_not_overlap_or_gap(self):
        """스케줄이 늦게 돌아도(08:47) 구간은 같다 — 같은 공고를 두 번 보내지 않는다."""
        _, end_mon = complete_days_window(datetime(2026, 9, 28, 8, 0), 1)
        begin_tue, _ = complete_days_window(datetime(2026, 9, 29, 8, 47), 1)
        self.assertEqual((begin_tue - end_mon).total_seconds(), 60)

    def test_weekly_window(self):
        begin, end = complete_days_window(datetime(2026, 9, 28, 8, 10), 7)
        self.assertEqual(begin, datetime(2026, 9, 21, 0, 0))
        self.assertEqual(end, datetime(2026, 9, 27, 23, 59))


class TestRejectedRow(unittest.TestCase):
    def test_non_negotiated_rejected_rows_drop_raw(self):
        candidates, _ = _candidates(1)
        row = rejected_row(candidates[0])
        self.assertIsNone(row["raw"])
        self.assertEqual(row["title"], candidates[0].notice.title)

    def test_negotiated_rejected_rows_keep_raw(self):
        candidates, _ = _candidates(1)
        candidates[0].category = "협상"
        self.assertIsNotNone(rejected_row(candidates[0])["raw"])


if __name__ == "__main__":
    unittest.main()
