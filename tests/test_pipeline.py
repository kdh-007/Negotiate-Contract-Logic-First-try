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
from nego.pipeline import RunStats, build_candidates, group_candidates  # noqa: E402
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


class TestGroupProjects(unittest.TestCase):
    """재공고는 공고번호가 달라 차수 정리로는 안 묶여서, 발주기관+제목 유사도로 묶는다."""

    def test_re_notice_with_new_notice_no_is_grouped_with_original(self):
        """실측 패턴: 재공고는 제목이 그대로라 유사도 1.0으로 잡힌다."""
        raws = [
            fixtures.notice("R26ORIG", title="하남역사박물관 상설전 고려실Ⅱ 개편 전시 용역"),
            fixtures.notice("R26REPOST", title="하남역사박물관 상설전 고려실Ⅱ 개편 전시 용역", kind="재공고", reNtceYn="Y"),
        ]
        notices = [notice_from_raw(r, "용역") for r in raws]
        groups = scope.group_projects(notices)
        self.assertEqual(len(groups), 1)
        self.assertEqual({n.notice_no for n in groups[0]}, {"R26ORIG", "R26REPOST"})

    def test_same_institution_and_budget_but_different_title_is_not_grouped(self):
        """회귀 테스트: 같은 발주기관+같은 예산이라는 이유만으로 다른 사업을 묶으면 안 된다.

        실측(테스트 픽스처 전체가 같은 기관·같은 기본예산을 씀)으로 발견된 버그의
        회귀 테스트 — 예산 일치만으로 묶던 예전 조건을 지웠다.
        """
        raws = [
            fixtures.notice("R26A", title="○○과학관 전시물 제작 및 설치"),
            fixtures.notice("R26B", title="△△박물관 전시디자인"),
        ]
        notices = [notice_from_raw(r, "용역") for r in raws]
        groups = scope.group_projects(notices)
        self.assertEqual(len(groups), 2, "제목이 다르면 예산이 같아도 별개 사업으로 남아야 한다")

    def test_different_institution_is_never_grouped(self):
        raws = [
            fixtures.notice("R26A", title="같은 제목 공고", ntceInsttNm="기관A"),
            fixtures.notice("R26B", title="같은 제목 공고", ntceInsttNm="기관B"),
        ]
        notices = [notice_from_raw(r, "용역") for r in raws]
        groups = scope.group_projects(notices)
        self.assertEqual(len(groups), 2)


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


class TestExtractCandidateCodes(unittest.TestCase):
    def test_extracts_labeled_industry_code(self):
        codes = qualify._extract_candidate_codes("산업디자인전문회사(환경디자인분야)(업종코드: 4442)로 신고를 필한 업체")
        self.assertEqual(codes, ["4442"])

    def test_extracts_bracket_only_code_without_label(self):
        codes = qualify._extract_candidate_codes("소프트웨어사업자(디지털콘텐츠 개발서비스사업)[1469]")
        self.assertEqual(codes, ["1469"])

    def test_extracts_ten_digit_product_code(self):
        codes = qualify._extract_candidate_codes("조합놀이대(세부품명번호 10자리, 4924159701)을 제조물품으로 등록")
        self.assertEqual(codes, ["4924159701"])

    def test_ignores_year_looking_numbers(self):
        codes = qualify._extract_candidate_codes("계약기간: 2026. 12. 15.까지(재공고)")
        self.assertEqual(codes, [])

    def test_ignores_phone_number_tail_inside_parens(self):
        """실측 회귀: 담당부서 연락처의 마지막 4자리가 업종코드로 오인되면 안 된다."""
        text = "실적인정 여부는 발주부서(안성시청 문화관광과 관광팀, ☎031-678-2492)에서 최종 판단함"
        self.assertEqual(qualify._extract_candidate_codes(text), [])

    def test_finds_multiple_codes_across_separate_brackets(self):
        text = "산업디자인전문회사(제품디자인분야)(업종코드: 4441) 또는 산업디자인전문회사(환경디자인분야)(업종코드: 4442)"
        self.assertEqual(qualify._extract_candidate_codes(text), ["4441", "4442"])


class TestEvaluateFromTextItems(unittest.TestCase):
    def test_no_codified_items_is_fail_open(self):
        """실적·신용등급처럼 코드가 없는 항목만 있으면 판정하지 않는다(checked=False)."""
        items = ["가. 부정당업자가 아닌 자", "나. 최근 3년 이내 5천만원 이상의 완료 실적이 있는 업체"]
        result = qualify.evaluate_from_text_items(items, {"4990"}, set())
        self.assertFalse(result.checked)
        self.assertTrue(result.passes)

    def test_passes_when_held_code_matches_one_of_the_alternatives(self):
        items = ["가. 산업디자인전문회사(환경디자인분야)(업종코드: 4442) 또는 (종합디자인분야)(업종코드: 4444)"]
        result = qualify.evaluate_from_text_items(items, {"4442"}, set())
        self.assertTrue(result.checked)
        self.assertTrue(result.passes)
        self.assertEqual(result.missing_count, 0)
        self.assertEqual(result.source, "첨부파일 텍스트")

    def test_one_missing_group_still_passes(self):
        items = [
            "가. 실내건축공사업(업종코드: 4990) 면허 보유업체",
            "나. 정보통신공사업(업종코드: 0036) 등록업체",
        ]
        result = qualify.evaluate_from_text_items(items, {"4990"}, set())
        self.assertTrue(result.checked)
        self.assertEqual(result.missing_count, 1)
        self.assertTrue(result.passes, "1개까지는 통과 — API 판정과 동일 규칙")

    def test_two_missing_groups_fails(self):
        items = [
            "가. 실내건축공사업(업종코드: 4990) 면허 보유업체",
            "나. 정보통신공사업(업종코드: 0036) 등록업체",
        ]
        result = qualify.evaluate_from_text_items(items, set(), set())
        self.assertEqual(result.missing_count, 2)
        self.assertFalse(result.passes)
        self.assertIn("미충족", result.summary)


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

    def test_overseas_korea_pavilion_is_excluded(self):
        """국내 공고만 다루기로 한 방침(2026-09-15) — 실측: UAE 두바이/미국 뉴욕/베오그라드/이스탄불 한국관 설치 공고."""
        for title in (
            "2027 UAE 두바이 의료기기전시회 한국관 전시디자인설치공사 입찰",
            "2026 미국 뉴욕 치과 전시회 한국관 전시디자인설치공사",
            "2027 베오그라드엑스포 한국관 참가사업 원가 검토 용역",
            "2026년 이스탄불 국제식품박람회(WFI) 한국관 장치 용역",
        ):
            result = self._screen(fixtures.notice("X", title=title))
            self.assertFalse(result.matched, title)
            self.assertEqual(result.excluded_by, "해외공고", title)

    def test_group_pavilion_without_korea_hall_word_is_a_known_gap(self):
        """사용자 요청으로 '한국관' 단독 체크로 단순화 — '단체관'만 쓰는 공고(실측: 홍콩 뷰티 전시회)는 이제 못 잡는다."""
        result = self._screen(
            fixtures.notice("X", title="2026 홍콩 코스모프로프 뷰티 전시회 단체관 전시디자인 및 설치용역")
        )
        self.assertNotEqual(result.excluded_by, "해외공고")

    def test_institution_name_containing_hanguk_gwan_is_a_known_accepted_risk(self):
        """'한국관광공사'는 부분일치로 '한국관'을 포함해 같이 걸린다.

        실측(Supabase 협상계약 전체)으로 "한국관광"이 제목에 들어간 공고가
        0건이라 이론상 위험으로만 보고 단순화를 택했다 — 실제로 이런 공고가
        나오면 이 테스트가 그 사실을 알려주는 신호가 된다.
        """
        result = self._screen(fixtures.notice("X", title="한국관광공사 ○○센터 전시관 리모델링 용역"))
        self.assertEqual(result.excluded_by, "해외공고")

    def test_mongolia_is_exempt_from_overseas_pavilion_exclusion(self):
        """사업 확장 범위에 몽골이 들어감(2026-09-15) — '한국관'이 있어도 몽골이면 제외하지 않는다."""
        result = self._screen(
            fixtures.notice("X", title="2026 몽골 울란바토르 국제전시회 한국관 전시디자인설치공사")
        )
        self.assertNotEqual(result.excluded_by, "해외공고")


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

    def test_re_notice_does_not_duplicate_in_final_candidates(self):
        """실측 사례: 하남역사박물관/고삼호수가 원공고+재공고로 각각 2번씩 리포트에 뜨던 문제."""
        config = load_config()
        config.screen.keywords = ["박물관"]
        raws = [
            fixtures.notice("R26ORIG", title="○○박물관 상설전 개편 전시 용역", bidNtceDt="2026-08-01 10:00:00"),
            fixtures.notice(
                "R26REPOST",
                title="○○박물관 상설전 개편 전시 용역",
                kind="재공고",
                reNtceYn="Y",
                bidNtceDt="2026-08-20 10:00:00",
            ),
        ]
        notices = [notice_from_raw(r, "용역") for r in raws]
        stats = RunStats()

        candidates = build_candidates(notices, config, {}, {}, NOW, stats)

        self.assertEqual(len(candidates), 1, "같은 사업은 한 번만 후보로 남아야 한다")
        self.assertEqual(candidates[0].notice.notice_no, "R26REPOST", "더 최근에 게시된 쪽이 대표로 남아야 한다")
        self.assertEqual(stats.duplicate_projects, 1)

        superseded = [c for c in stats.rejected if c.notice.notice_no == "R26ORIG"]
        self.assertEqual(len(superseded), 1)
        self.assertFalse(superseded[0].is_candidate)
        self.assertIn("R26REPOST", superseded[0].excluded_reason)


class TestGroupCandidates(unittest.TestCase):
    def _candidate(self, notice_no: str, posted_at: str) -> "Candidate":
        from nego.pipeline import Candidate

        raw = fixtures.notice(notice_no, title="○○박물관 상설전 개편 전시 용역", bidNtceDt=posted_at)
        n = notice_from_raw(raw, "용역")
        return Candidate(
            notice=n,
            screen_result=screen.ScreenResult(matched=True, confidence="참고용"),
            qualification=qualify.QualificationResult(total_groups=0, missing_groups=[], passes=True, checked=False),
            joint=qualify.parse_joint_supply(None),
            schedule=screen.build_schedule(n),
        )

    def test_keeps_the_most_recently_posted_candidate(self):
        older = self._candidate("R26OLD", "2026-08-01 10:00:00")
        newer = self._candidate("R26NEW", "2026-08-20 10:00:00")

        kept, superseded = group_candidates([older, newer])

        self.assertEqual([c.notice.notice_no for c in kept], ["R26NEW"])
        self.assertEqual([c.notice.notice_no for c in superseded], ["R26OLD"])
        self.assertFalse(older.is_candidate)
        self.assertIn("R26NEW", older.excluded_reason)


if __name__ == "__main__":
    unittest.main(verbosity=2)
