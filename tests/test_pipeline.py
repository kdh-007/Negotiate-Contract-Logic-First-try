"""파이프라인 단위 테스트. 표준 라이브러리 unittest만 사용한다.

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nego import fields as F  # noqa: E402
from nego import qualify, schedule_text, scope, screen  # noqa: E402
from nego.config import load_config  # noqa: E402
from nego.fields import PERSONAL_FIELDS  # noqa: E402
from nego.http_client import ApiError, parse_response_body  # noqa: E402
from nego.models import notice_from_raw  # noqa: E402
from nego.pipeline import RunStats, build_candidates, collect_notices  # noqa: E402
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

    def test_satisfied_groups_tracked_separately_from_missing(self):
        """자격판정 팝업에서 파란 원(충족)도 빨간 원(미달)과 같은 형식으로
        보여주려면 어떤 그룹이 충족됐는지 알아야 한다."""
        groups = [
            qualify.LicenseGroup("1", ["실내건축공사업"]),  # 보유 — 충족
            qualify.LicenseGroup("2", ["전기공사업"]),  # 미보유 — 미달
        ]
        result = qualify.evaluate(groups, self.held)
        self.assertEqual([g.group_no for g in result.satisfied_groups], ["1"])
        self.assertEqual([g.group_no for g in result.missing_groups], ["2"])

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

    def test_evaluate_attachment_text_passes_when_all_codes_held(self):
        items = [
            "마.「건설산업기본법」 제9조에 따른 실내건축공사업(업종코드 4990)으로 입찰참가 등록한 자",
            "아. 부정당업자 제재 중에 있지 아니한 자",  # 코드 없음 — 판정 대상에서 제외돼야 함
        ]
        result = qualify.evaluate_attachment_text(items, held_codes={"4990"})
        self.assertTrue(result.checked)
        self.assertEqual(result.total_groups, 1, "코드 없는 일반 결격사유는 판정 대상이 아니다")
        self.assertEqual(result.summary, "자격 충족")

    def test_evaluate_attachment_text_fails_when_code_missing(self):
        items = ["마.「건설산업기본법」 제9조에 따른 실내건축공사업(업종코드 4990)으로 입찰참가 등록한 자"]
        result = qualify.evaluate_attachment_text(items, held_codes={"9999"})
        self.assertIn("자격 미달", result.summary)
        self.assertIn("실내건축공사업(4990)", result.summary, "이름과 코드가 함께 표시돼야 한다")

    def test_evaluate_attachment_text_satisfied_label_uses_held_registry_name(self):
        """실측 버그(사용자 제보, 2026-09-17): 첨부문서 원문 파싱은 문서 표기가
        제각각이라 '충족된 자격' 팝업에 코드만 남거나("6010989901") 절차성
        문구가 뒤섞인 이름("환경디자인을 포함한 종합디자인분야)[4444)")이 뜬다.
        코드가 이미 held_codes에 있다는 걸 확인한 뒤이니, 파싱한 이름표 대신
        등록증 원문 이름(held_code_names)을 그대로 쓰면 이 문제가 없어진다."""
        item = "마.「건설산업기본법」 제9조에 따른 실내건축공사업(업종코드 4990)으로 입찰참가 등록한 자"
        result = qualify.evaluate_attachment_text(
            [item], held_codes={"4990"}, held_code_names={"4990": "실내건축공사업"}
        )
        self.assertEqual(
            [g.allowed_names for g in result.satisfied_groups],
            [["실내건축공사업(4990)"]],
        )

    def test_evaluate_attachment_text_satisfied_label_falls_back_without_registry(self):
        """held_code_names를 안 주면(기존 호출부와의 하위호환) 파싱한 이름표를
        그대로 쓴다 — 동작이 바뀌지 않아야 한다."""
        item = "마.「건설산업기본법」 제9조에 따른 실내건축공사업(업종코드 4990)으로 입찰참가 등록한 자"
        result = qualify.evaluate_attachment_text([item], held_codes={"4990"})
        self.assertEqual([g.allowed_names for g in result.satisfied_groups], [["실내건축공사업(4990)"]])

    def test_load_held_code_names_maps_products_and_industries(self):
        held_config = {
            "heldProducts": [{"code": "6010989901", "name": "실물모형및전시물"}],
            "heldIndustries": [{"code": "4990", "name": "실내건축공사업"}],
        }
        self.assertEqual(
            qualify.load_held_code_names(held_config),
            {"6010989901": "실물모형및전시물", "4990": "실내건축공사업"},
        )

    def test_extract_code_requirements_skips_digit_count_filler(self):
        """실측 오류 재현: "세부품명번호 10자리, 4924159701"에서 "10"을 코드로
        잘못 잡으면, 실제로는 보유한 코드(4924159701)인데도 미달로 오판정된다."""
        item = (
            "나.「국가종합전자조달시스템 입찰참가자격등록규정」에 의하여 조합놀이대"
            "(세부품명번호 10자리, 4924159701)을 제조물품으로 입찰참가 등록한 자"
        )
        requirements = qualify._extract_code_requirements(item)
        self.assertEqual([code for code, _ in requirements], ["4924159701"])

        self.assertEqual(qualify.evaluate_attachment_text([item], held_codes={"4924159701"}).summary, "자격 충족")
        fail_summary = qualify.evaluate_attachment_text([item], held_codes={"9999"}).summary
        self.assertIn("자격 미달", fail_summary)
        self.assertIn("(4924159701)", fail_summary)
        self.assertLess(len(fail_summary), 60, "실측 오류 재현: 문장 전체가 그대로 딸려 나오면 안 된다")

    def test_extract_code_requirements_skips_deadline_clause_before_item_name(self):
        """실측 오류 재현(사용자 제보 원문, 2026-09-17): 연결어("에 의하여")와
        괄호 사이에 마감일자 안내 절차 문구("…마감일시까지")가 끼면
        "마감일시까지 조합놀이대"처럼 이름표에 그 문구가 그대로 딸려 나왔다.
        "까지"도 연결어 목록에 추가해 떼어낸다."""
        item = (
            "나.「국가종합전자조달시스템 입찰참가자격등록규정」에 의하여 국가종합전자조달"
            "시스템G2B(나라장터)에 입찰참가자격등록 마감일시까지 조합놀이대"
            "(세부품명번호 10자리, 4924159701)을 제조물품으로 입찰참가 등록한 자"
        )
        requirements = qualify._extract_code_requirements(item)
        self.assertEqual([code for code, _ in requirements], ["4924159701"])
        self.assertEqual(requirements[0][1], "조합놀이대(4924159701)")

        fail_summary = qualify.evaluate_attachment_text([item], held_codes={"9999"}).summary
        self.assertEqual(fail_summary, "자격 미달(조합놀이대(4924159701))")

    def test_extract_code_requirements_preserves_multi_word_item_name(self):
        """이름을 "마지막 한 단어"로 단순화하면 안 된다 — "실물모형 및 전시물"처럼
        여러 단어로 된 실제 품목명이 있고, 한 단어만 남기면("전시물") 정보가
        없어진다."""
        item = "실물모형 및 전시물(세부품명번호 6010989901)"
        requirements = qualify._extract_code_requirements(item)
        self.assertEqual(requirements, [("6010989901", "실물모형 및 전시물(6010989901)")])

    def test_extract_code_requirements_generalizes_connector_and_strips_quotes(self):
        """실측 오류 재현: "규정에 따라"만 알던 연결어 정규식이 "「무슨무슨법」
        제5조에 따라"처럼 다른 인용구 뒤의 "에 따라"는 못 걸렀고, 인용부호로
        감싼 품목명("조합놀이대")의 따옴표도 그대로 남았다."""
        item = '「무슨무슨법」 제5조에 따라 "조합놀이대"(세부품명번호 4924159701)를 소지한 자'
        requirements = qualify._extract_code_requirements(item)
        self.assertEqual(requirements, [("4924159701", "조합놀이대(4924159701)")])

    def test_extract_code_requirements_recognizes_bare_10_digit_code(self):
        """실측: 두바이 의료기기전시회 한국관 공고문 — "세부품명번호" 키워드 없이
        "이름(코드)"만 쓴 문서. 정확히 10자리일 때만 코드로 인정해 연도·조항
        번호 같은 일반 괄호 숫자를 코드로 오인하지 않게 한다."""
        item = "o 전시부스설치서비스(7215409901) 소지 업체"
        requirements = qualify._extract_code_requirements(item)
        self.assertEqual([code for code, _ in requirements], ["7215409901"])

        result = qualify.evaluate_attachment_text([item], held_codes={"7215409901"})
        self.assertEqual(result.summary, "자격 충족")

    def test_extract_code_requirements_ignores_short_bare_parens(self):
        """4자리 등 10자리가 아닌 괄호 숫자는(예: 법조문 인용) 코드로 보지 않는다."""
        item = "o 국가를 당사자로 하는 계약에 관한 법률(2024) 제12조에 따른 자"
        self.assertEqual(qualify._extract_code_requirements(item), [])

    def test_evaluate_attachment_text_or_group_needs_only_one_code(self):
        items = [
            "바. 다음 중 어느 하나의 자격으로 입찰참가 등록한 자\n"
            "    -「산업디자인진흥법」 제9조에 따른 산업디자인전문회사(환경디자인 분야, 업종코드 4442)\n"
            "    -「공공디자인의 진흥에 관한 법률」 제18조에 따른 공공디자인전문회사(업종코드 6484)"
        ]
        result = qualify.evaluate_attachment_text(items, held_codes={"6484"})
        self.assertEqual(result.summary, "자격 충족", "OR그룹은 코드 하나만 있어도 충족")

    def test_evaluate_attachment_text_and_group_needs_every_code(self):
        items = [
            "라. 다음 직접생산확인증명서를 모두 소지한 자이어야 합니다.\n"
            "    - 실물모형 및 전시물(세부품명번호 6010989901)\n"
            "    - 책장(세부품명번호 5610150701)"
        ]
        result = qualify.evaluate_attachment_text(items, held_codes={"6010989901"})
        self.assertIn("자격 미달", result.summary, "AND그룹은 하나라도 빠지면 미달")

    def test_evaluate_attachment_text_no_codes_found_stays_unchecked(self):
        items = ["아. 부정당업자 제재 중에 있지 아니한 자"]
        result = qualify.evaluate_attachment_text(items, held_codes={"4990"})
        self.assertFalse(result.checked, "코드가 명시된 항목이 하나도 없으면 판정하지 않는다")

    def test_extract_code_requirements_finds_all_codes_sharing_one_paren(self):
        """실측 오류 재현: "세부품명번호 10자리(코드1 이름1, 코드2 이름2, 코드3 이름3)"처럼
        프리픽스 하나 뒤에 괄호 하나를 공유하는 코드 여러 개가 있으면, 기존 정규식은
        프리픽스당 코드 하나만 잡아 나머지가 조용히 빠졌다 — 보유하지 않은 코드가
        빠진 채로 "자격 충족"이라고 잘못 표시되는 원인이었다(단양군 미디어아트 공고문)."""
        item = (
            "1) 국가종합전자조달시스템 입찰참가자격 등록규정에 따라 반드시 입찰(개찰) 전일까지 "
            "나라장터(G2B)에 세부품명번호 10자리(4511161601 비디오프로젝터, 3911160501 LED경관조명기구, "
            "3912110702 조명용제어장치) 제조 또는 공급으로 입찰참가 등록한 업체로 입찰일(낙찰자는 "
            "계약체결일)까지 당해 자격을 계속 유지하여야 합니다."
        )
        requirements = qualify._extract_code_requirements(item)
        self.assertEqual(
            [code for code, _ in requirements], ["4511161601", "3911160501", "3912110702"]
        )

        result = qualify.evaluate_attachment_text([item], held_codes={"4511161601", "3911160501"})
        self.assertIn("자격 미달", result.summary, "코드 하나(3912110702)가 없으면 미달이어야 한다")
        self.assertIn("조명용제어장치(3912110702)", result.summary)

    def test_extract_code_requirements_finds_bracket_codes_without_keyword(self):
        """실측 오류 재현: 직접생산확인증명서 항목이 "업종코드"/"세부품명번호" 키워드 없이
        "[코드, 이름]" 대괄호만 쓰면 코드가 하나도 안 잡혔다(위와 같은 공고문의 2번 항목)."""
        item = (
            "2) 「중소기업제품 구매촉진 및 판로지원에 관한 법률」제9조 및 같은 법 시행령 제10조에 "
            "의한 직접생산확인증명서[3911160501, LED경관조명기구], [3912110702, 조명용제어장치]를 "
            "소지한 업체"
        )
        requirements = qualify._extract_code_requirements(item)
        self.assertEqual([code for code, _ in requirements], ["3911160501", "3912110702"])

        result = qualify.evaluate_attachment_text([item], held_codes={"3911160501"})
        self.assertIn("자격 미달", result.summary)
        self.assertIn("조명용제어장치(3912110702)", result.summary)

    def test_summary_deduplicates_same_missing_label_across_groups(self):
        """단양군 공고문처럼 항목 여러 개가 같은 미보유 코드를 요구하면, 같은
        이름표가 그대로 두 번 표시되지 않고 한 번만 나와야 한다."""
        items = [
            "1) 세부품명번호 10자리(4511161601 비디오프로젝터, 3912110702 조명용제어장치) 제조 업체",
            "2) 직접생산확인증명서[3912110702, 조명용제어장치]를 소지한 업체",
        ]
        result = qualify.evaluate_attachment_text(items, held_codes={"4511161601"})
        self.assertEqual(result.summary, "자격 미달(조명용제어장치(3912110702))")

    def test_extract_code_requirements_handles_semicolon_delimited_group(self):
        """프리픽스 공유형 괄호는 콤마뿐 아니라 세미콜론 등 다른 구분자를 써도
        코드를 전부 뽑아야 한다 — 문서마다 구분자가 다를 때마다 정규식을 새로
        추가하지 않기 위한 일반화."""
        item = "나.「국가종합전자조달시스템 입찰참가자격등록규정」에 의하여 세부품명번호(1234567890; 9876543210)을 소지한 업체"
        requirements = qualify._extract_code_requirements(item)
        self.assertEqual([code for code, _ in requirements], ["1234567890", "9876543210"])

    def test_merge_results_flips_api_pass_to_fail_when_attachment_finds_missing_code(self):
        """실측 버그(단양군 미디어아트 R26BK01731335): API 면허제한정보에는 면허·업종만
        있고 세부품명번호 필드가 아예 없다(fields.LICENSE_LIMIT_FIELDS). 그래서 API가
        '업종 4그룹 전부 충족'이라 해도 첨부파일이 요구하는 품목(직접생산확인)이 빠진
        채로 자격 충족이 돼버렸다. 둘 중 하나라도 미충족이면 미충족이어야 한다."""
        api = qualify.QualificationResult(
            total_groups=4, missing_groups=[], passes=True, checked=True
        )
        attachment = qualify.evaluate_attachment_text(
            ["1) 세부품명번호 10자리(4511161601 비디오프로젝터, 3912110702 조명용제어장치) 제조 업체"],
            held_codes={"4511161601"},
        )
        merged = qualify.merge_results(api, attachment)
        self.assertEqual(merged.summary, "자격 미달(조명용제어장치(3912110702))")
        self.assertEqual(merged.total_groups, 5, "양쪽 그룹 수를 합산한다")

    def test_merge_results_keeps_api_verdict_when_attachment_has_no_codes(self):
        api = qualify.QualificationResult(
            total_groups=4, missing_groups=[], passes=True, checked=True
        )
        attachment = qualify.evaluate_attachment_text(["아. 부정당업자 제재 중에 있지 아니한 자"], set())
        self.assertIs(qualify.merge_results(api, attachment), api)

    def test_merge_results_uses_attachment_when_api_gave_nothing(self):
        api = qualify.QualificationResult(
            total_groups=0, missing_groups=[], passes=True, checked=False
        )
        attachment = qualify.evaluate_attachment_text(
            ["마. 실내건축공사업(업종코드 4990)으로 입찰참가 등록한 자"], held_codes={"4990"}
        )
        self.assertEqual(qualify.merge_results(api, attachment).summary, "자격 충족")

    def test_extract_code_requirements_handles_bare_parens_without_keyword(self):
        """키워드 없이 괄호(대괄호가 아니라)만으로 코드를 표기해도 잡아야 한다 —
        지금까지는 대괄호 표기만 지원했는데, 문서마다 괄호/대괄호가 섞여 쓰이므로
        일반화한다."""
        item = "직접생산확인증명서(LED경관조명기구 3911160501)를 소지한 업체"
        requirements = qualify._extract_code_requirements(item)
        self.assertEqual([code for code, _ in requirements], ["3911160501"])

    def test_summary_is_plain_pass_when_all_groups_satisfied(self):
        groups = qualify.group_license_rows(fixtures.license_rows())["R26TEST00002"]
        result = qualify.evaluate(groups, self.held)
        self.assertEqual(result.summary, "자격 충족")

    def test_summary_lists_missing_license_names(self):
        groups = [
            qualify.LicenseGroup("1", ["실내건축공사업"]),
            qualify.LicenseGroup("2", ["전기공사업", "정보통신공사업"]),
        ]
        result = qualify.evaluate(groups, self.held)
        self.assertEqual(result.summary, "자격 미달(전기공사업/정보통신공사업)")

    def test_summary_dedupes_name_repeated_within_one_group_before_matching_other_groups(self):
        """실측 재현(Run #45, R26BK01731335): 첨부문서 한 항목 안에서 같은 코드가
        중복 추출되면 그 그룹의 allowed_names 자체가 ["A", "A"]가 된다. 이걸
        그대로 "/"로 이으면 "A/A"가 되어, 다른 그룹의 단순 "A"와 문자열이 달라
        중복 제거를 피해간다 — "자격 미달(A/A, A)"로 잘못 표시됐다."""
        name = "조명용제어장치(3912110702)"
        groups = [
            qualify.LicenseGroup("1", [name, name]),
            qualify.LicenseGroup("2", [name]),
        ]
        result = qualify.evaluate(groups, self.held)
        self.assertEqual(result.summary, f"자격 미달({name})")


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

    def test_overseas_exhibition_korea_pavilion_is_excluded(self):
        """실측: R26BK01717819(UAE 두바이 의료기기전시회), R26BK01714892(두바이) —
        둘 다 "한국관" 때문에 국내 공고로 오인됐지만 실제로는 해외 개최다."""
        result = self._screen(
            fixtures.notice("X", title="2027 UAE 두바이 의료기기전시회 한국관 전시디자인설치공사 입찰")
        )
        self.assertFalse(result.matched)
        self.assertEqual(result.excluded_by, "해외개최")

    def test_korea_pavilion_alone_is_excluded_without_event_keyword(self):
        """"한국관"은 "전시회/박람회/엑스포" 같은 행사 키워드 없이 단독으로도 제외된다."""
        result = self._screen(fixtures.notice("X", title="□□센터 한국관 조성 및 운영 지원"))
        self.assertFalse(result.matched)
        self.assertEqual(result.excluded_by, "해외개최")

    def test_group_pavilion_alone_without_event_keyword_is_unaffected(self):
        """"단체관"은 "한국관"과 달리 행사 키워드가 같이 있어야 신호로 본다."""
        result = self._screen(fixtures.notice("X", title="□□체험관 단체관 운영 프로그램"))
        self.assertNotEqual(result.excluded_by, "해외개최")

    def test_overseas_exhibition_without_korea_pavilion_is_unaffected(self):
        """"전시회/박람회/엑스포"만으로는 안 걸린다 — "한국관/단체관"이 같이 있어야 한다."""
        result = self._screen(fixtures.notice("X", title="○○과학관 특별전시회 운영 박람회 홍보"))
        self.assertNotEqual(result.excluded_by, "해외개최")

    def test_domestic_exhibition_design_notice_is_unaffected(self):
        """일반 국내 전시디자인 공고는 "한국관/단체관" 문구가 없으니 그대로 통과해야 한다."""
        result = self._screen(fixtures.notice("X", title="○○박물관 상설전시관 전시디자인 제작 설치"))
        self.assertTrue(result.matched)
        self.assertFalse(result.overseas_flag)

    def test_mongolia_korea_pavilion_is_flagged_not_excluded(self):
        """회사가 실제로 확장 중인 몽골은 예외 — 배제하지 않고 플래그만 남긴다."""
        result = self._screen(fixtures.notice("X", title="2026 몽골 울란바토르 국제산업박람회 한국관 조성"))
        self.assertTrue(result.matched, "몽골은 키워드/코드 불일치로도 배제되면 안 된다")
        self.assertTrue(result.overseas_flag)
        self.assertIsNone(result.excluded_by)

    def test_overseas_flag_carries_hover_tooltip_evidence(self):
        """리포트 배지의 마우스오버 툴팁에 쓸 근거 문구 — 어떤 단어가
        매치됐는지와 몽골이라 배제 안 했다는 사실이 같이 들어있어야 한다."""
        result = self._screen(fixtures.notice("X", title="2026 몽골 울란바토르 국제산업박람회 한국관 조성"))
        self.assertIsNotNone(result.overseas_evidence)
        self.assertIn("한국관", result.overseas_evidence)
        self.assertIn("몽골", result.overseas_evidence)

    def test_excluded_reason_also_carries_evidence(self):
        result = self._screen(
            fixtures.notice("X", title="2027 UAE 두바이 의료기기전시회 한국관 전시디자인설치공사 입찰")
        )
        self.assertIn("한국관", result.excluded_reason)

    def test_mongolia_korea_pavilion_with_no_other_match_is_still_surfaced(self):
        """몽골 해외관은 등록된 업무 키워드가 하나도 없어도(=원래는 '미매칭'으로
        걸러질 상황) 플래그를 위해 강제로 통과시킨다."""
        result = self._screen(fixtures.notice("X", title="2026 몽골 울란바토르 산업박람회 단체관 설치"))
        self.assertTrue(result.matched)
        self.assertTrue(result.overseas_flag)
        self.assertEqual(result.matched_keywords, [])


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

    def test_attachment_deadline_used_only_when_api_fields_all_empty(self):
        sched = screen.Schedule(
            qualification_deadline=None,
            joint_agreement_deadline=None,
            bid_deadline=None,
            attachment_deadline=datetime(2026, 9, 14, 18, 0),
        )
        self.assertEqual(sched.earliest, ("첨부파일 제출기한", datetime(2026, 9, 14, 18, 0)))

    def test_attachment_deadline_ignored_when_an_api_field_exists(self):
        """API 값이 하나라도 있으면 첨부파일 추정치보다 우선한다."""
        sched = screen.Schedule(
            qualification_deadline=None,
            joint_agreement_deadline=None,
            bid_deadline=datetime(2026, 9, 20, 18, 0),
            attachment_deadline=datetime(2026, 9, 14, 18, 0),
        )
        label, when = sched.earliest
        self.assertEqual(label, "입찰 마감")
        self.assertEqual(when, datetime(2026, 9, 20, 18, 0))


class TestNoticeOpeningAt(unittest.TestCase):
    """개찰일(opengDt)은 fields.py에 후보가 있었지만 Notice에 연결이 안 돼 있었다 —
    API가 실제로 주는 값이 파이프라인 어디에도 안 남고 버려지던 상태. 연결됐는지 확인."""

    def test_opening_at_is_mapped_from_api_field(self):
        n = notice_from_raw(fixtures.notice("X", opengDt="2026-09-25 11:00:00"), "용역")
        self.assertEqual(n.opening_at, "2026-09-25 11:00:00")

    def test_opening_at_is_none_when_absent(self):
        n = notice_from_raw(fixtures.notice("X", opengDt=""), "용역")
        self.assertIsNone(n.opening_at)


class TestNoticeEstimatePriceMethod(unittest.TestCase):
    """예가방법(예정가격 결정방법). 실측(2026-09-17 `nego --verify`, Run #46)으로
    확인한 필드 — 용역/물품/공사 세 오퍼레이션 모두에서 prearngPrceDcsnMthdNm으로
    내려온다(예: 용역="비예가", 물품/공사="단일예가")."""

    def test_estimate_price_method_is_mapped_from_api_field(self):
        n = notice_from_raw(fixtures.notice("X", prearngPrceDcsnMthdNm="단일예가"), "용역")
        self.assertEqual(n.estimate_price_method, "단일예가")

    def test_estimate_price_method_is_none_when_absent(self):
        n = notice_from_raw(fixtures.notice("X", prearngPrceDcsnMthdNm=""), "용역")
        self.assertIsNone(n.estimate_price_method)


class TestScheduleTextExtraction(unittest.TestCase):
    def test_extracts_deadline_with_time_range(self):
        """실측: 경상남도관광재단 「K-거상」공고 R26BK01707504 — API 마감
        필드가 전부 비어 있었지만 첨부 제안요청서에는 제출기한이 명시돼
        있었다. 범위(9:00~18:00)면 종료 시각을 마감으로 본다."""
        text = (
            "다. 기본서류 및 제안서 제출일시(반드시 방문제출)\n"
            "- 제출기간 : 2026. 9. 14.(월) 9:00~18:00 (점심시간 12:00~13:00 접수 불가)\n"
        )
        self.assertEqual(schedule_text.extract_deadline(text), datetime(2026, 9, 14, 18, 0))

    def test_defaults_to_end_of_day_when_no_time_given(self):
        text = "마감일자 : 2026.10.5."
        self.assertEqual(schedule_text.extract_deadline(text), datetime(2026, 10, 5, 23, 59))

    def test_skips_label_when_date_is_too_far_away(self):
        """절 제목처럼 라벨만 있고 실제 값은 다른 줄에 있으면 건너뛰고 다음
        라벨(실제 값이 가까운)로 넘어가야 한다."""
        text = (
            "다. 기본서류 및 제안서 제출일시(반드시 방문제출)\n"
            "    * 아래 접수처로 방문 제출하며 우편 및 온라인 접수는 불가합니다.\n"
            "- 제출기한 : 2026.11.3.(화) 17:00\n"
        )
        self.assertEqual(schedule_text.extract_deadline(text), datetime(2026, 11, 3, 17, 0))

    def test_returns_none_when_no_deadline_text_present(self):
        self.assertIsNone(schedule_text.extract_deadline("과업 내용은 별첨 과업지시서를 참조합니다."))


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


class _FailingClient:
    """지정한 label만 재시도 소진(ApiError)으로 실패하는 가짜 클라이언트."""

    def __init__(self, fail_labels: set[str]):
        self.fail_labels = fail_labels
        self.called_labels: list[str] = []

    def fetch_all_pages_chunked(self, base_url, operation, params, begin, end, label):
        self.called_labels.append(label)
        if label in self.fail_labels:
            raise ApiError(label, "요청 실패: 재시도 소진")
        return []


class TestCollectNotices(unittest.TestCase):
    def test_first_failed_operation_stops_the_rest_immediately(self):
        """용역/물품/공사 중 하나라도 재시도를 소진해 실패하면, 나머지 부문은
        시도조차 하지 않고 즉시 ApiError를 올려야 한다 — 부분 데이터로 계속
        진행하지 않고, Actions에서 새 Run으로 빨리 재시도할 수 있어야 하기
        때문이다."""
        for failing in F.BID_NOTICE_OPERATIONS:
            with self.subTest(failing=failing):
                stats = RunStats()
                client = _FailingClient(fail_labels={f"본공고/{failing}"})
                with self.assertRaises(ApiError):
                    collect_notices(client, "202601010000", "202601312359", stats)
                self.assertEqual(stats.failed_operations, [f"본공고/{failing}"])
                self.assertEqual(client.called_labels[-1], f"본공고/{failing}", "실패한 부문 이후는 시도하지 않아야 한다")


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
