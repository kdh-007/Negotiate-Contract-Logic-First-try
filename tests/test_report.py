"""리포트(HTML) 생성 단위 테스트. 표준 라이브러리 unittest만 사용한다.

    python -m unittest discover -s tests -v

제목, "조회 기간 ~ 생성 시각" 표기, "본공고 (N건)" 절은 기존에 사용 중이던
수집 시스템의 결과페이지(주간 리포트)를 그대로 따른다. 후보 목록은 표(테이블)
형식이다 — 행마다 신뢰도/업무구분 배지, 자격판정(체크원+호버 팝업), 키워드 태그.
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
from nego.report import _fmt_kr_date, _fmt_kr_datetime, _fmt_kr_deadline, _fmt_money, render_html  # noqa: E402
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

    def test_fmt_kr_deadline_uses_24_hour_format(self):
        self.assertEqual(_fmt_kr_deadline(datetime(2026, 9, 20, 18, 0, 0)), "2026년 09월 20일 18시")

    def test_fmt_kr_deadline_pads_single_digit_month_day_hour(self):
        self.assertEqual(_fmt_kr_deadline(datetime(2026, 1, 5, 9, 30, 0)), "2026년 01월 05일 09시")

    def test_fmt_kr_deadline_none_is_empty(self):
        self.assertEqual(_fmt_kr_deadline(None), "")


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

    def test_row_shows_confidence_and_worktype_badges(self):
        candidates, stats = self._candidates()
        out = render_html(candidates, stats, NOW)
        self.assertIn('<span class="badge">공사</span>', out)
        self.assertIn(candidates[0].screen_result.confidence, out)

    def test_row_shows_qualification_verdict_and_keyword_tag(self):
        candidates, stats = self._candidates()
        out = render_html(candidates, stats, NOW)
        self.assertIn("키워드:인테리어", out)
        self.assertIn(candidates[0].qualification.summary, out)

    def test_opening_at_is_shown_date_only_without_time(self):
        """개찰일은 연월일까지만 표기한다(시각 생략) — opengDt는 시각을 포함해서
        내려오지만 입찰마감일 칸에서 이미 시각을 다루므로 여기선 날짜만 쓴다."""
        candidates, stats = self._candidates()
        candidates[0].notice.opening_at = "2026-09-24 10:00:00"
        out = render_html(candidates, stats, NOW)
        self.assertIn("개찰일 2026. 9. 24.", out)
        self.assertNotIn("10:00", out)

    def test_opening_at_missing_shows_placeholder(self):
        candidates, stats = self._candidates()
        candidates[0].notice.opening_at = None
        out = render_html(candidates, stats, NOW)
        self.assertIn("개찰일 일정 미상", out)

    def test_deadline_shows_dday_badge(self):
        candidates, stats = self._candidates()
        candidates[0].days_left = 3
        out = render_html(candidates, stats, NOW)
        self.assertIn('<div class="dday">D-3</div>', out)

    def test_estimate_price_method_is_marked_field_unconfirmed(self):
        """예가방법(복수예가/단일예가 등)에 해당하는 API 필드를 아직 확인 못 했다 —
        없는 필드를 있는 척 채우지 않고 명시적으로 '필드 미확인'이라고 표시한다."""
        candidates, stats = self._candidates()
        out = render_html(candidates, stats, NOW)
        self.assertIn('<span class="dim field-gap">필드 미확인</span>', out)

    def test_no_period_line_when_stats_lack_period(self):
        """--from-store처럼 조회 기간 정보가 없는 실행에서도 죽지 않아야 한다."""
        candidates, _ = self._candidates()
        stats = RunStats()
        out = render_html(candidates, stats, NOW)
        self.assertNotIn("조회 기간", out)
        self.assertIn("생성 시각", out)

    def test_qualification_pass_is_a_blue_check_circle(self):
        """'자격 충족'은 파란 체크원(circle-pass)으로 표시돼야 한다."""
        from nego.qualify import QualificationResult

        candidates, stats = self._candidates()
        candidates[0].qualification = QualificationResult(
            total_groups=1, missing_groups=[], passes=True, checked=True
        )
        out = render_html(candidates, stats, NOW)
        self.assertIn('<span class="circle circle-pass">', out)
        self.assertIn('aria-label="자격 충족"', out)

    def test_missing_qualification_tooltip_dedupes_same_name_across_groups(self):
        """실측: 단양군 미디어아트 공고(R26BK01731335) — 첨부문서 참가자격 절에
        같은 세부품명번호(조명용제어장치)가 항목 3개에 걸쳐 등장하면
        `evaluate_attachment_text`가 미충족 그룹 3개를 만든다. 빨간 체크원
        호버 팝업 안 목록은 이름 기준으로 한 번만 나와야 한다(summary()의
        중복 제거와 동일)."""
        from nego.qualify import LicenseGroup, QualificationResult

        candidates, stats = self._candidates()
        name = "조명용제어장치(3912110702)"
        candidates[0].qualification = QualificationResult(
            total_groups=3,
            missing_groups=[LicenseGroup(group_no=str(i), allowed_names=[name]) for i in range(3)],
            passes=False,
            checked=True,
        )
        out = render_html(candidates, stats, NOW)
        self.assertIn('<span class="circle circle-fail">', out)
        self.assertEqual(out.count(f'<div class="tip-item">{name}</div>'), 1)

    def test_qualification_unchecked_is_a_neutral_circle(self):
        """자격정보가 없어 판정을 못 한(통과 처리된) 경우는 노란 원(circle-unchecked)으로,
        '자격 미달'과 헷갈리지 않는 중립적인 문구로 표시돼야 한다."""
        from nego.qualify import QualificationResult

        candidates, stats = self._candidates()
        candidates[0].qualification = QualificationResult(
            total_groups=0, missing_groups=[], passes=True, checked=False
        )
        out = render_html(candidates, stats, NOW)
        self.assertIn('<span class="circle circle-unchecked">', out)
        self.assertIn('aria-label="자격정보 미확인 (통과)"', out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
