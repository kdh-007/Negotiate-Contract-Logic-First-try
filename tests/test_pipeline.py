"""파이프라인 단위 테스트. 표준 라이브러리 unittest만 사용한다.

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nego import qualify, scope, screen  # noqa: E402
from nego.config import load_config  # noqa: E402
from nego.fields import PERSONAL_FIELDS  # noqa: E402
from nego.http_client import parse_response_body  # noqa: E402
from nego.models import notice_from_raw  # noqa: E402
from nego.pipeline import RunStats, build_candidates  # noqa: E402
from tests import fixtures  # noqa: E402

NOW = datetime(2026, 9, 14, 9, 0)


def _notices():
    return [notice_from_raw(raw, "용역") for raw in fixtures.service_notices()]


class TestHttpClient(unittest.TestCase):
    def test_items_as_object_with_item_list(self):
        body = '{"response":{"header":{"resultCode":"00","resultMsg":"NORMAL"},' \
               '"body":{"items":{"item":[{"bidNtceNo":"A"},{"bidNtceNo":"B"}]},"totalCount":2}}}'
        env = parse_response_body(body, "test")
        self.assertEqual(len(env.items), 2)
        self.assertEqual(env.total_count, 2)

    def test_items_as_single_object(self):
        body = '{"response":{"header":{"resultCode":"00","resultMsg":"OK"},' \
               '"body":{"items":{"bidNtceNo":"A"},"totalCount":1}}}'
        self.assertEqual(len(parse_response_body(body, "test").items), 1)

    def test_items_empty_string(self):
        body = '{"response":{"header":{"resultCode":"00","resultMsg":"OK"},"body":{"items":"","totalCount":0}}}'
        self.assertEqual(parse_response_body(body, "test").items, [])

    def test_nonstandard_error_envelope_is_found(self):
        """비표준 오류 포맷에서도 resultCode를 찾아내야 한다 (99로 덮어쓰면 안 됨)."""
        body = '{"nkoneps.com.response.ResponseError":{"header":{"resultCode":"30",' \
               '"resultMsg":"SERVICE KEY IS NOT REGISTERED ERROR"}}}'
        self.assertEqual(parse_response_body(body, "test").result_code, "30")

    def test_xml_response_is_parsed(self):
        body = (
            "<response><header><resultCode>00</resultCode><resultMsg>OK</resultMsg></header>"
            "<body><items><item><bidNtceNo>A</bidNtceNo></item></items><totalCount>1</totalCount></body></response>"
        )
        env = parse_response_body(body, "test")
        self.assertEqual(env.result_code, "00")
        self.assertEqual(len(env.items), 1)


class TestScope(unittest.TestCase):
    def test_negotiated_detection_covers_variants(self):
        for award in (fixtures.NEGO, fixtures.NEGO_SW):
            n = notice_from_raw(fixtures.notice("X", award=award), "용역")
            self.assertTrue(scope.is_negotiated(n), award)

    def test_non_negotiated_is_rejected(self):
        n = notice_from_raw(fixtures.notice("X", award=fixtures.QUALIFY), "용역")
        self.assertFalse(scope.is_negotiated(n))

    def test_design_contest_is_adjacent_not_negotiated(self):
        n = notice_from_raw(fixtures.notice("X", award=fixtures.DESIGN_CONTEST), "용역")
        self.assertFalse(scope.is_negotiated(n))
        self.assertTrue(scope.is_adjacent(n))

    def test_variant_extraction(self):
        n = notice_from_raw(fixtures.notice("X", award=fixtures.NEGO_SW), "용역")
        self.assertEqual(scope.negotiation_variant(n), "SW사업")

    def test_cancelled_dropped_and_latest_ordinal_kept(self):
        result = scope.apply_scope(_notices())
        numbers = {n.notice_no for n in result.kept}
        self.assertNotIn("R26TEST00003", numbers, "취소공고가 남아 있으면 안 된다")
        self.assertNotIn("R26TEST00004", numbers, "협상이 아닌 공고가 남아 있으면 안 된다")
        ords = {n.notice_no: n.notice_ord for n in result.kept}
        self.assertEqual(ords["R26TEST00002"], "001", "최신 차수만 남아야 한다")
        self.assertEqual(result.dropped_cancelled, 1)
        self.assertEqual(result.dropped_old_ordinal, 1)

    def test_cancelled_notice_does_not_revive_older_ordinal(self):
        """최신 차수가 취소공고면 이전 차수가 되살아나면 안 된다."""
        raws = [
            fixtures.notice("R26REVIVE", "000", title="○○전시관 제작"),
            fixtures.notice("R26REVIVE", "001", title="○○전시관 제작", kind="취소공고"),
        ]
        result = scope.apply_scope([notice_from_raw(r, "용역") for r in raws])
        self.assertEqual([n.notice_no for n in result.kept], [])


class TestJointSupply(unittest.TestCase):
    def test_parses_submit_and_exec_type(self):
        joint = qualify.parse_joint_supply("(전자)공동이행 또는 분담이행")
        self.assertTrue(joint.allowed)
        self.assertEqual(joint.submit_type, "전자")
        self.assertEqual(joint.exec_type, "공동이행 또는 분담이행")

    def test_detects_not_allowed(self):
        joint = qualify.parse_joint_supply("(없음)공동수급불허")
        self.assertFalse(joint.allowed)
        self.assertEqual(joint.submit_type, "없음")

    def test_missing_value(self):
        joint = qualify.parse_joint_supply(None)
        self.assertEqual(joint.label, "정보 없음")


class TestQualification(unittest.TestCase):
    """판정 규칙은 기존 시스템과 동일해야 한다 (변경 금지)."""

    held = ["실내건축공사업", "산업디자인전문회사(환경디자인분야)", "전시사업자(전시장치사업자)"]

    def test_bracketed_industry_list_is_split_and_code_removed(self):
        names = qualify.split_industry_list("[건축공사업/0002][토목건축공사업/0001]")
        self.assertEqual(names, ["건축공사업", "토목건축공사업"])

    def test_no_groups_is_fail_open(self):
        result = qualify.evaluate([], self.held)
        self.assertTrue(result.passes)
        self.assertFalse(result.checked)

    def test_all_groups_satisfied(self):
        groups = qualify.group_license_rows(fixtures.license_rows())["R26TEST00002"]
        result = qualify.evaluate(groups, self.held)
        self.assertEqual(result.missing_count, 0)
        self.assertTrue(result.passes)

    def test_one_missing_group_passes(self):
        groups = [
            qualify.LicenseGroup("1", ["실내건축공사업"]),
            qualify.LicenseGroup("2", ["전기공사업"]),
        ]
        result = qualify.evaluate(groups, self.held)
        self.assertEqual(result.missing_count, 1)
        self.assertTrue(result.passes, "1개까지는 통과 — 기존 규칙")

    def test_substring_matching_is_permissive(self):
        """현행 동작 기록: '건축공사업'이 보유 업종 '실내건축공사업'에 부분일치로 걸린다.

        기존 시스템과 같은 양방향 부분일치를 쓰기 때문이다. 변경 대상이 아니며,
        이런 성질이 있다는 것만 테스트로 남겨 둔다.
        """
        groups = qualify.group_license_rows(fixtures.substring_overmatch_rows())["R26SUBSTR"]
        result = qualify.evaluate(groups, self.held)
        self.assertEqual(result.missing_count, 0)

    def test_two_missing_groups_excluded(self):
        groups = qualify.group_license_rows(fixtures.license_rows())["R26TEST00009"]
        result = qualify.evaluate(groups, self.held)
        self.assertEqual(result.missing_count, 2)
        self.assertFalse(result.passes, "2개 이상 미충족이면 제외 — 기존 규칙")

    def test_attachment_note_surfaces_when_api_has_no_data(self):
        """API가 비어 있어도(checked=False) 첨부파일에서 찾은 정보가 있으면 요약에 드러나야 한다."""
        result = qualify.evaluate([], self.held)
        self.assertFalse(result.checked)
        self.assertIn("자격정보 없음 (판정 보류, 통과)", result.summary)

        result.attachment_note = "3. 입찰 참가자격 — 5개 항목 (원문 확인 필요)"
        self.assertIn("첨부파일 확인", result.summary)
        self.assertIn("5개 항목", result.summary)


class TestScreen(unittest.TestCase):
    config = screen.ScreenConfig(
        keywords=["전시관", "박물관", "과학관", "체험관", "전시디자인", "전시홍보관", "미디어아트", "전시콘텐츠"],
        exclude_keywords=["유지보수", "정비", "진단"],
        min_budget_amount=100_000_000,
        product_codes=[],
        industry_codes=[],
    )

    def _screen(self, raw):
        return screen.screen(notice_from_raw(raw, "용역"), self.config)

    def test_exclude_keyword_blocks(self):
        result = self._screen(fixtures.notice("X", title="○○박물관 전시물 유지보수 용역"))
        self.assertFalse(result.matched)
        self.assertEqual(result.excluded_by, "제외키워드")

    def test_budget_below_minimum_blocks(self):
        result = self._screen(fixtures.notice("X", title="○○과학관 전시", presmptPrce="50000000"))
        self.assertFalse(result.matched)
        self.assertEqual(result.excluded_by, "최소예산")

    def test_unknown_budget_passes(self):
        result = self._screen(
            fixtures.notice("X", title="○○과학관 전시물 제작", presmptPrce="", asignBdgtAmt="")
        )
        self.assertTrue(result.matched, "예산 미상은 통과시킨다 — 기존 정책")

    def test_whitespace_is_ignored_in_matching(self):
        result = self._screen(fixtures.notice("X", title="○○ 과 학 관 전시물"))
        self.assertTrue(result.matched)


class TestSchedule(unittest.TestCase):
    def test_earliest_deadline_is_chosen(self):
        n = notice_from_raw(
            fixtures.notice(
                "X",
                bidQlfctRgstDt="2026-09-20 18:00:00",
                cmmnSpldmdAgrmntClseDt="2026-09-18 18:00:00",
                bidClseDt="2026-09-25 10:00:00",
            ),
            "용역",
        )
        sched = screen.build_schedule(n)
        label, when = sched.earliest
        self.assertEqual(label, "공동수급협정 마감")
        self.assertEqual(when, datetime(2026, 9, 18, 18, 0))
        self.assertEqual(sched.days_left(NOW), 4)

    def test_missing_schedule_returns_none(self):
        n = notice_from_raw(
            fixtures.notice("X", bidQlfctRgstDt="", bidClseDt="", cmmnSpldmdAgrmntClseDt=""), "용역"
        )
        self.assertIsNone(screen.build_schedule(n).days_left(NOW))


class TestPersonalDataStripped(unittest.TestCase):
    def test_person_fields_removed_from_raw(self):
        n = notice_from_raw(fixtures.notice("X"), "용역")
        for field_name in PERSONAL_FIELDS:
            self.assertNotIn(field_name, n.raw, f"{field_name}이 raw에 남아 있으면 안 된다")


class TestAttachments(unittest.TestCase):
    def test_attachments_collected_with_extension(self):
        n = notice_from_raw(fixtures.notice("X"), "용역")
        self.assertEqual(len(n.attachments), 2)
        self.assertEqual(n.attachments[0]["ext"], "hwp")
        self.assertEqual(n.attachments[1]["ext"], "pdf")


class TestEndToEnd(unittest.TestCase):
    def test_full_pipeline_on_fixtures(self):
        config = load_config()
        # 픽스처 제목에 맞춰 키워드만 좁혀서 검증한다 (config 원본은 건드리지 않는다).
        config.screen.keywords = [
            "전시관", "박물관", "과학관", "체험관", "전시디자인", "전시홍보관", "미디어아트", "전시콘텐츠", "전시물",
        ]
        stats = RunStats()
        license_groups = qualify.group_license_rows(fixtures.license_rows())

        candidates = build_candidates(_notices(), config, license_groups, {}, NOW, stats)
        numbers = [c.notice.notice_no for c in candidates]

        self.assertIn("R26TEST00001", numbers)
        self.assertIn("R26TEST00002", numbers)
        self.assertIn("R26TEST00008", numbers)
        self.assertNotIn("R26TEST00003", numbers, "취소공고")
        self.assertNotIn("R26TEST00004", numbers, "협상 아님")
        self.assertNotIn("R26TEST00005", numbers, "설계공모")
        self.assertNotIn("R26TEST00006", numbers, "제외키워드")
        self.assertNotIn("R26TEST00007", numbers, "예산 미달")
        self.assertNotIn("R26TEST00009", numbers, "자격 미충족 2그룹")
        self.assertNotIn("R26TEST00010", numbers, "키워드 미매칭")

        self.assertEqual(stats.cancelled, 1)
        self.assertEqual(stats.gate_excluded, 1)

    def test_sorted_by_deadline_first(self):
        config = load_config()
        config.screen.keywords = ["전시관", "박물관", "과학관", "전시디자인", "전시물", "전시홍보관"]
        stats = RunStats()
        candidates = build_candidates(_notices(), config, {}, {}, NOW, stats)
        days = [c.days_left if c.days_left is not None else 9999 for c in candidates]
        self.assertEqual(days, sorted(days), "마감 임박 순으로 정렬되어야 한다")


if __name__ == "__main__":
    unittest.main(verbosity=2)
