"""리포트(HTML) 생성 단위 테스트. 표준 라이브러리 unittest만 사용한다.

    python -m unittest discover -s tests -v

UI 구조는 기존에 사용 중이던 수집 시스템의 결과페이지(주간 리포트)를 그대로
따른다 — 제목, "조회 기간 ~ 생성 시각" 표기, "본공고 (N건)" 절, 카드별
신뢰도/업무구분 배지, 자격판정, 키워드 태그.
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nego.config import load_config  # noqa: E402
from nego.models import notice_from_raw  # noqa: E402
from nego.pipeline import RunStats, build_candidates  # noqa: E402
from nego.report import _fmt_kr_date, _fmt_kr_datetime, _fmt_money, render_html  # noqa: E402
from tests import fixtures  # noqa: E402

NOW = datetime(2026, 8, 31, 0, 1, 13)


class TestFormatters(unittest.TestCase):
    def test_fmt_money_shows_exact_won_without_eok_abbreviation(self):
        self.assertEqual(_fmt_money(180_600_000), "180,600,000원")

    def test_fmt_kr_date_has_no_leading_zeros(self):
        self.assertEqual(_fmt_kr_date(datetime(2026, 8, 24)), "2026. 8. 24.")

    def test_fmt_kr_datetime_midnight_is_am_12(self):
        self.assertEqual(_fmt_kr_datetime(datetime(2026, 8, 31, 0, 1, 13)), "2026. 8. 31. AM 12:01:13")

    def test_fmt_kr_datetime_noon_is_pm_12(self):
        self.assertEqual(_fmt_kr_datetime(datetime(2026, 8, 31, 12, 0, 0)), "2026. 8. 31. PM 12:00:00")


class TestRenderHtml(unittest.TestCase):
    def _candidates(self):
        config = load_config()
        config.screen.keywords = ["인테리어"]
        raw = fixtures.notice("R26TEST0001", title="(긴급)경북동부근로자건강센터 인테리어공사 입찰공고")
        notices = [notice_from_raw(raw, "공사")]
        stats = RunStats(period_begin=datetime(2026, 8, 24), period_end=NOW)
        candidates = build_candidates(notices, config, {}, {}, NOW, stats)
        self.assertEqual(len(candidates), 1, "픽스처가 스크리닝을 통과해야 아래 검증이 의미가 있다")
        return candidates, stats

    def test_header_matches_existing_system_title_and_period(self):
        candidates, stats = self._candidates()
        out = render_html(candidates, stats, NOW)
        self.assertIn("나라장터 입찰 모니터링 주간 리포트", out)
        self.assertIn("조회 기간: 2026. 8. 24. ~ 2026. 8. 31.", out)
        self.assertIn("생성 시각: 2026. 8. 31. AM 12:01:13", out)

    def test_section_header_shows_candidate_count(self):
        candidates, stats = self._candidates()
        out = render_html(candidates, stats, NOW)
        self.assertIn(f"본공고 ({len(candidates)}건)", out)

    def test_card_shows_confidence_and_worktype_badges(self):
        candidates, stats = self._candidates()
        out = render_html(candidates, stats, NOW)
        self.assertIn("[공사]", out)
        self.assertIn(candidates[0].screen_result.confidence, out)

    def test_card_shows_qualification_verdict_and_keyword_tag(self):
        candidates, stats = self._candidates()
        out = render_html(candidates, stats, NOW)
        self.assertIn("키워드:인테리어", out)
        self.assertIn(candidates[0].qualification.summary, out)

    def test_no_period_line_when_stats_lack_period(self):
        """--from-store처럼 조회 기간 정보가 없는 실행에서도 죽지 않아야 한다."""
        candidates, _ = self._candidates()
        stats = RunStats()
        out = render_html(candidates, stats, NOW)
        self.assertNotIn("조회 기간", out)
        self.assertIn("생성 시각", out)

    def test_qualification_pass_is_wrapped_in_blue_box(self):
        """'자격 충족'도 '자격 미달' 항목처럼 박스(태그)로 표시돼야 한다."""
        from nego.qualify import QualificationResult

        candidates, stats = self._candidates()
        candidates[0].qualification = QualificationResult(
            total_groups=1, missing_groups=[], passes=True, checked=True
        )
        out = render_html(candidates, stats, NOW)
        self.assertIn('<span class="passtag">자격 충족</span>', out)

    def test_qualification_unchecked_is_wrapped_in_gray_box(self):
        """자격정보가 없어 판정을 못 한(통과 처리된) 경우는 회색 박스로,
        '자격 미달'과 헷갈리지 않는 중립적인 문구로 표시돼야 한다."""
        from nego.qualify import QualificationResult

        candidates, stats = self._candidates()
        candidates[0].qualification = QualificationResult(
            total_groups=0, missing_groups=[], passes=True, checked=False
        )
        out = render_html(candidates, stats, NOW)
        self.assertIn('<span class="unchecktag">자격정보 미확인 (통과)</span>', out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
